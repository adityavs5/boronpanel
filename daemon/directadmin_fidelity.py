"""Native DirectAdmin inventory and compatibility checks before provisioning.

All paths refer to private extracted staging, never arbitrary server files.
Secrets remain in this in-memory inventory, not in job reports.
"""
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs
import json
import re
import shlex
import subprocess
import os

from shared.config import settings
from shared.validation import ValidationError, validate_domain, validate_mailbox_local_part, validate_email_address, validate_env_vars, validate_app_entry_point, validate_python_entry_point


def kv(path):
    if not path.is_file():
        return {}
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValidationError('DirectAdmin metadata exceeds the size limit')
    return dict(line.split('=', 1) for line in path.read_text(errors='strict').splitlines()
                if '=' in line and not line.startswith('#'))


def relative_path(value, username):
    value = str(value)
    prefix = f'/home/{username}/'
    if value.startswith(prefix):
        value = value[len(prefix):]
    elif value.startswith('/domains/'):
        value = value[1:]
    elif value.startswith('/'):
        raise ValidationError('Source path is outside the account home; manual mapping is required')
    path = PurePosixPath(value)
    if not value or not path.parts or '..' in path.parts or path.is_absolute() or '\\' in value or any(ord(c) < 32 for c in value):
        raise ValidationError('Unsafe or empty source account path')
    if path.parts[0] in ('.ssh', '.config', '.gnupg', 'etc', 'var', 'tmp'):
        raise ValidationError('Source document/application path uses a protected account directory')
    return str(path)


def php_version(value):
    match = re.fullmatch(r'(?:PHP\s+)?(\d+\.\d+)(?:\.\d+)?', str(value).strip())
    return match[1] if match else None


def password_hash(value):
    # SHA crypt is shared by DA crypt() and Dovecot. Do not silently reset
    # passwords or label an unknown scheme as compatible.
    value = value.removeprefix('{CRYPT}')
    patterns = [r'\$6\$(?:rounds=\d+\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}',
                r'\$5\$(?:rounds=\d+\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{43}']
    if any(re.fullmatch(pattern, value) for pattern in patterns):
        return '{CRYPT}' + value
    raise ValidationError('Mailbox uses an unsupported password hash; convert it on the source before migration')


def available_php_versions():
    return {version for version in settings.php_versions
            if os.access(Path(settings.lsphp_base) / ('lsphp' + version.replace('.', '')) / 'bin/lsphp', os.X_OK)}


def php_extensions(version):
    binary = Path(settings.lsphp_base) / ('lsphp' + version.replace('.', '')) / 'bin/lsphp'
    result = subprocess.run([str(binary), '-m'], capture_output=True, text=True, timeout=15, check=True)
    return {line.strip().lower().replace('-', '_') for line in result.stdout.splitlines() if line and not line.startswith('[')}


def build(source, normalized, username, domains, runtime=None):
    runtime = runtime or {}
    manifest = {'domains': [], 'mailboxes': [], 'forwards': [], 'catchalls': [], 'apps': [], 'warnings': [], 'blockers': []}
    home = normalized / 'homedir'
    for domain in domains:
        directory = source / 'backup' / domain
        conf = kv(directory / 'domain.conf') or kv(source / 'backup' / f'{domain}.conf')
        inventory = runtime.get(domain, {})
        version = php_version(conf.get('php_version', '')) or php_version(inventory.get('selected', ''))
        slots = inventory.get('slots', {})
        slot = conf.get('php1_select')
        if slot:
            version = php_version(slots.get(slot, '')) or version
        root = relative_path(conf.get('documentroot') or conf.get('public_html') or f'domains/{domain}/public_html', username)
        primary = {'domain': domain, 'kind': 'primary' if domain == domains[0] else 'addon', 'docroot': root, 'php_version': version}
        manifest['domains'].append(primary)
        subdomains = directory / 'subdomain.list'
        overrides = kv(directory / 'domain.subdomains.docroot.override') or kv(directory / 'subdomains.docroot.override')
        labels = set(subdomains.read_text().splitlines()) if subdomains.is_file() else set()
        labels |= set(overrides)
        for label in sorted(labels):
            if not label.strip():
                continue
            name = validate_domain(f'{label.strip()}.{domain}')
            props = {k: v[-1] for k, v in parse_qs(overrides.get(label, ''), keep_blank_values=True).items()}
            subroot = relative_path(props.get('public_html') or f'domains/{domain}/public_html/{label}', username)
            subversion = php_version(slots.get(props.get('php1_select'), '')) if props.get('php1_select') else version
            if props.get('private_html') and relative_path(props['private_html'], username) != subroot:
                manifest['blockers'].append(f'{name}: distinct HTTPS document root requires manual mapping')
            manifest['domains'].append({'domain': name, 'kind': 'subdomain', 'docroot': subroot, 'php_version': subversion})
        private = source / 'domains' / domain / 'private_html'
        if private.is_dir() and any(private.iterdir()):
            manifest['blockers'].append(f'{domain}: separate private_html content requires manual mapping')
        passwd = directory / 'email' / 'passwd'
        if not passwd.is_file():
            passwd = directory / 'passwd'
        quotas = {}
        for quota_file in [directory / 'email' / 'quota', directory / 'quota']:
            if quota_file.is_file():
                for line in quota_file.read_text().splitlines():
                    if ':' in line:
                        name, quota = line.split(':', 1)
                        if quota.isdigit(): quotas[name] = int(quota)
        known = set()
        if passwd.is_file():
            for line in passwd.read_text().splitlines():
                if not line.strip(): continue
                parts = line.split(':')
                local = validate_mailbox_local_part(parts[0])
                known.add(local)
                try:
                    hashed = password_hash(parts[1])
                except (ValidationError, IndexError):
                    manifest['blockers'].append(f'{local}@{domain}: unsupported or missing mailbox password hash')
                    continue
                quota = quotas.get(local, 0)
                # DA quota is bytes; Boron stores MiB with a finite upper bound.
                from daemon.handlers_mail import MAILBOX_QUOTA_MAX_MB
                quota_mb = min(MAILBOX_QUOTA_MAX_MB, max(1, (quota + 1048575) // 1048576)) if quota else MAILBOX_QUOTA_MAX_MB
                if quota == 0:
                    manifest['warnings'].append(f'{local}@{domain}: unlimited quota mapped to destination maximum ({quota_mb} MiB)')
                manifest['mailboxes'].append({'domain': domain, 'local_part': local, 'password_hash': hashed, 'quota_mb': quota_mb})
                maildir = home / 'mail' / domain / local
                for folder in ('cur', 'new', 'tmp'): (maildir / folder).mkdir(parents=True, exist_ok=True)
        mailroot = home / 'mail' / domain
        if mailroot.is_dir():
            for box in mailroot.iterdir():
                if box.is_dir() and box.name not in known:
                    manifest['blockers'].append(f'{box.name}@{domain}: mail data exists without credentials; include email accounts in the backup')
        aliases = directory / 'email' / 'aliases'
        if not aliases.is_file(): aliases = directory / 'aliases'
        if aliases.is_file():
            for line in aliases.read_text().splitlines():
                if not line.strip() or line.startswith('#') or ':' not in line: continue
                local, destinations = (part.strip() for part in line.split(':', 1))
                if local in known and destinations == local: continue
                if local == username and destinations == username and local not in known:
                    system_mail = home / 'Maildir'
                    if system_mail.is_dir() and any(p.is_file() and p.parent.name in ('cur', 'new') for p in system_mail.rglob('*')):
                        manifest['blockers'].append(f'{domain}: system mailbox needs a verified credential mapping')
                    else:
                        manifest['warnings'].append(f'{domain}: empty system-mailbox alias omitted; virtual mailboxes are imported separately')
                    continue
                if local == '*' and destinations in (':fail:', ':blackhole:'):
                    if destinations == ':blackhole:': manifest['warnings'].append(f'{domain}: catchall discard maps to rejection; review mail policy')
                    continue
                if local == '*' and ',' in destinations:
                    manifest['blockers'].append(f'{domain}: multiple catchall destinations require explicit mail routing')
                    continue
                for destination in destinations.split(','):
                    destination = destination.strip()
                    if destination in known: destination += '@' + domain
                    try:
                        validate_email_address(destination)
                        if local == '*':
                            manifest['catchalls'].append({'domain': domain, 'destination': destination})
                        else:
                            validate_mailbox_local_part(local)
                            if destination != f'{local}@{domain}': manifest['forwards'].append({'domain': domain, 'local_part': local, 'destination': destination})
                    except ValidationError:
                        manifest['blockers'].append(f'{domain}: mail alias {local} uses an unsupported destination')
        for name in ('filter', 'filter.conf', 'autoresponder.conf', 'vacation.conf', 'list.conf'):
            for path in (directory / name, directory / 'email' / name):
                if path.is_file() and path.read_text().strip():
                    manifest['blockers'].append(f'{domain}: {name} requires a mail-policy conversion before migration')
    extension_cache = {}
    for entry in manifest['domains']:
        if not (home / entry['docroot']).is_dir():
            manifest['blockers'].append(f"{entry['domain']}: document root is missing from the archive")
        if not entry['php_version']:
            # A runtime is required for PHP sites, but static-only sites need no PHP mapping.
            if any((home / entry['docroot']).rglob('*.php')):
                manifest['blockers'].append(f"{entry['domain']}: actual source PHP version is unknown; selector slots alone are not portable")
        elif entry['php_version'] not in available_php_versions():
            manifest['blockers'].append(f"{entry['domain']}: PHP {entry['php_version']} is not installed/enabled on the destination")
        composer = home / entry['docroot'] / 'composer.json'
        if composer.is_file() and entry['php_version'] in available_php_versions():
            if composer.stat().st_size > 4 * 1024 * 1024:
                raise ValidationError('Composer metadata exceeds the size limit')
            required = json.loads(composer.read_text()).get('require', {})
            extensions = {name[4:].lower().replace('-', '_') for name in required if name.startswith('ext-')}
            if extensions:
                version = entry['php_version']
                if version not in extension_cache: extension_cache[version] = php_extensions(version)
                missing = extensions - extension_cache[version]
                if missing: manifest['blockers'].append(f"{entry['domain']}: PHP {version} lacks declared extensions: {', '.join(sorted(missing))}")
    discover_apps(home, username, manifest, runtime)
    return manifest


def discover_apps(home, username, manifest, runtime=None):
    """Passenger declarations are authoritative; package.json alone is not an app."""
    runtime = runtime or {}
    source_apps = list(runtime.get('__applications', []))
    selector = home / '.cl.selector'
    checked = set(runtime.get('__selector_checked', []))
    node_config = selector / 'node-selector.json'
    if not runtime.get('__cloudlinux_checked') and 'node-selector.json' not in checked and node_config.is_file():
        if node_config.stat().st_size > 4 * 1024 * 1024:
            raise ValidationError('CloudLinux metadata exceeds the size limit')
        source_apps.extend(selector_file_applications(json.loads(node_config.read_text()), username))
        checked.add('node-selector.json')
    used_apps = set()
    for entry in manifest['domains']:
        htaccess = home / entry['docroot'] / '.htaccess'
        configured = [app for app in source_apps if app['domain'] == entry['domain']]
        if len(configured) > 1:
            manifest['blockers'].append(f"{entry['domain']}: multiple apps on one hostname need explicit route mapping")
            continue
        selected_app = configured[0] if configured else None
        if not htaccess.is_file() and selected_app is None: continue
        directives = {}
        environment = {}
        for line in (htaccess.read_text(errors='replace').splitlines() if htaccess.is_file() else []):
            try: parts = shlex.split(line, comments=True)
            except ValueError: continue
            if len(parts) == 3 and parts[0] == 'SetEnv': environment[parts[1]] = parts[2]
            if len(parts) >= 2 and parts[0].startswith('Passenger'):
                directives[parts[0]] = parts[1]
        if selected_app:
            directives.update(PassengerAppRoot=selected_app['root'], PassengerAppType=selected_app['kind'], PassengerStartupFile=selected_app['startup'], PassengerBaseURI=selected_app['uri'])
            environment.update(selected_app['env'])
            used_apps.add(selected_app['domain'])
        if not directives: continue
        try:
            root = relative_path(directives.get('PassengerAppRoot', entry['docroot']), username)
            node = directives.get('PassengerNodejs', '')
            python = directives.get('PassengerPython', '')
            kind = directives.get('PassengerAppType') or ('node' if node else 'wsgi' if python else '')
            if kind not in ('node', 'wsgi'):
                raise ValidationError('unsupported or unknown Passenger application type')
            startup = directives.get('PassengerStartupFile', 'app.js' if kind == 'node' else 'passenger_wsgi.py')
            startup = relative_path(startup, username)
            if directives.get('PassengerBaseURI', '/') != '/':
                raise ValidationError('path-mounted applications require a dedicated hostname on the destination')
            if not (home / root / startup).is_file():
                raise ValidationError('application source/startup file is missing from the backup')
            version_match = re.search(r'(?:alt-nodejs|nodevenv/[^\s]+/)(\d+)', node) if kind == 'node' else re.search(r'(?:alt-python|virtualenv/[^\s]+/)(\d+(?:\.\d+)?)', python)
            version = version_match[1] if version_match else None
            if kind == 'wsgi' and version and '.' not in version: version = version[0] + '.' + version[1:]
            if selected_app: version = selected_app['version']
            if not version:
                raise ValidationError('application runtime version is unknown')
            if kind == 'node' and (version not in settings.node_versions or not os.access(Path(settings.node_base_dir) / version / 'bin/node', os.X_OK)):
                raise ValidationError('source Node.js major version is unavailable on destination')
            if kind == 'wsgi':
                target = subprocess.run([settings.python_bin, '-c', 'import sys; print("%d.%d" % sys.version_info[:2])'], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
                if version != target: raise ValidationError(f'source Python {version} does not match destination {target}')
            if kind == 'node': validate_app_entry_point(startup)
            else:
                if not startup.endswith('.py'): raise ValidationError('Python startup must be a .py module')
                validate_python_entry_point(startup[:-3].replace('/', '.') + ':' + (selected_app['callable'] if selected_app else 'application'))
            if kind == 'node' and not (home / root / 'package.json').is_file():
                raise ValidationError('package.json is missing')
            if kind == 'wsgi' and not (home / root / 'requirements.txt').is_file():
                raise ValidationError('requirements.txt is required to rebuild Python dependencies')
            if set(environment) & {'PATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'NODE_PATH'}:
                raise ValidationError('source-specific runtime environment paths require explicit mapping')
            environment = validate_env_vars(environment)
            manifest['apps'].append({'index': len(manifest['apps']) + 1, 'kind': kind, 'domain': entry['domain'], 'root': root, 'startup': startup, 'source_username': username, 'version': version, 'env': environment, 'callable': selected_app['callable'] if selected_app else 'application', 'enabled': selected_app['enabled'] if selected_app else True})
        except ValidationError as exc:
            manifest['blockers'].append(f"{entry['domain']}: {exc}")
    # Never silently overlook CloudLinux apps without a supported domain declaration.
    selector = home / '.cl.selector'
    for app in source_apps:
        if app['domain'] not in used_apps:
            manifest['blockers'].append(f"{app['domain']}: application domain is missing from the archive")
    if not runtime.get('__cloudlinux_checked') and selector.is_dir() and any(('node' in p.name.lower() or 'python' in p.name.lower()) and p.name not in checked for p in selector.iterdir()):
        manifest['blockers'].append('CloudLinux selector data needs environment/application inventory review; automatic recreation is blocked to avoid losing settings')


def selector_file_applications(data, username):
    """Parse the account-owned Node Selector file, verified against a live source.

    No code is evaluated, and no dependencies or scripts execute during inventory.
    Unknown schema/state is blocked instead of silently dropping an application.
    """
    if not isinstance(data, dict):
        raise ValidationError('Unsupported CloudLinux Node Selector metadata')
    result = []
    for root, app in data.items():
        if not isinstance(app, dict) or app.get('app_status') not in ('started', 'stopped'):
            raise ValidationError('Unsupported CloudLinux Node Selector application state')
        version = str(app.get('nodejs_version', ''))
        if not re.fullmatch(r'\d+(?:\.\d+){0,2}', version):
            raise ValidationError('CloudLinux Node.js version is missing or invalid')
        env = dict(app.get('env_vars') or {})
        mode = app.get('app_mode')
        if mode: env.setdefault('NODE_ENV', str(mode))
        result.append({'domain': validate_domain(app.get('domain', '')),
                       'root': relative_path(root, username), 'kind': 'node',
                       'version': version.split('.')[0],
                       'startup': relative_path(app.get('startup_file') or 'app.js', username),
                       'uri': app.get('app_uri') or '/', 'callable': 'application',
                       'enabled': app['app_status'] == 'started', 'env': validate_env_vars(env)})
    return result


def dotenv_values(path):
    """Read simple literal dotenv assignments without evaluating/interpolating them."""
    if not path.is_file(): return {}
    if path.stat().st_size > 1024 * 1024:
        raise ValidationError('Application environment file exceeds the size limit')
    result = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        match = re.fullmatch(r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)', line)
        if not match: raise ValidationError('Application environment needs explicit mapping (non-literal dotenv format)')
        key, value = match.groups()
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0] or '\\' in value:
                raise ValidationError('Application environment needs explicit mapping (complex dotenv quoting)')
            value = value[1:-1]
        else:
            value = value.split('#', 1)[0].strip()
        if '$' in value or key in result:
            raise ValidationError('Application environment needs explicit mapping (expansion or duplicate key)')
        result[key] = value
    return result


def map_application_databases(root, manifest, dump_names):
    """Support the verified Node mysql2/DB_* layout; never guess arbitrary frameworks."""
    if not dump_names: return
    fields = {'host': 'DB_HOST', 'port': 'DB_PORT', 'user': 'DB_USER',
              'password': 'DB_PASSWORD', 'database': 'DB_NAME'}
    for app in manifest['apps']:
        try:
            home = root / 'homedir' / app['root']
            config = home / 'src/config/database.js'
            if app['kind'] != 'node' or not config.is_file() or config.stat().st_size > 1024 * 1024:
                raise ValidationError('application database credentials need an explicit framework mapping')
            code = config.read_text()
            if 'mysql2' not in code or any(not re.search(r'\b' + field + r'\s*:\s*process\.env\.' + key + r'\b', code) for field, key in fields.items()):
                raise ValidationError('application database configuration is not a supported DB_* mapping')
            environment = dotenv_values(home / '.env')
            environment.update(app['env'])
            if environment.get('DB_HOST') not in ('localhost', '127.0.0.1', '::1') or environment.get('DB_NAME') not in dump_names:
                raise ValidationError('application database must match a local database included in the backup')
            app['database_name'] = environment['DB_NAME']
        except ValidationError as exc:
            manifest['blockers'].append(f"{app['domain']}: {exc}")


def selector_applications(data, username, interpreter):
    """Parse CloudLinux's documented read-only selector get JSON response."""
    if data.get('result', 'success') != 'success':
        raise ValidationError('CloudLinux refused application inventory')
    result = []
    for version, details in data.get('available_versions', {}).items():
        user = details.get('users', {}).get(username, {})
        for root, app in user.get('applications', {}).items():
            result.append({'domain': validate_domain(app['domain']), 'root': relative_path(root, username),
                           'kind': 'node' if interpreter == 'nodejs' else 'wsgi',
                           'version': str(version).split('.')[0] if interpreter == 'nodejs' else '.'.join(str(version).split('.')[:2]),
                           'startup': relative_path(app.get('startup_file') or ('app.js' if interpreter == 'nodejs' else 'passenger_wsgi.py'), username),
                           'callable': app.get('entry_point') or 'application',
                           'uri': app.get('app_uri') or '/', 'enabled': app.get('app_status') == 'started',
                           'env': validate_env_vars(app.get('env_vars') or {})})
    return result
