"""Apply preflighted DirectAdmin components through Boron's ownership handlers."""
from pathlib import Path
import shutil
import tempfile
import time
import re
import json

from sqlalchemy import select
from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import ValidationError, generate_strong_password, validate_env_vars, validate_app_entry_point, validate_python_entry_point
from daemon import handlers_domain, handlers_mail, mail, ols, nodeapps, pythonapps
from daemon.directadmin_fidelity import password_hash, relative_path
from daemon.procutil import run


def apply_domain(username, entry):
    relative = relative_path(entry['docroot'], username)
    docroot = str(Path(settings.home_base) / username / relative)
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        domain = session.scalar(select(Domain).where(Domain.domain == entry['domain'], Domain.account_id == account.id))
        if domain is None: raise ValidationError('Imported domain ownership check failed')
    handlers_domain.ensure_docroot(username, docroot, entry['domain'])
    with write_session() as session:
        domain = session.scalar(select(Domain).where(Domain.domain == entry['domain'], Domain.account_id == account.id))
        if domain is None: raise ValidationError('Imported domain ownership changed')
        domain.docroot = docroot
    if entry.get('php_version'):
        handlers_domain.set_domain_php_version({'username': username, 'domain': entry['domain'], 'php_version': entry['php_version']})
    else:
        ols.refresh_vhost(account)
    return f"restored document root {relative}; PHP {entry.get('php_version') or 'not required by static site'}"


def restore_mail(root, entry):
    hashed = password_hash(entry['password_hash'])
    domain, local = entry['domain'], entry['local_part']
    handlers_mail.create_mailbox({'domain': domain, 'local_part': local, 'password': generate_strong_password(), 'quota_mb': entry['quota_mb']})
    # Parameterized SQL; never shell out with an original credential/hash.
    conn = mail._connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute('UPDATE mail_user SET password = %s WHERE domain_id = %s AND local_part = %s',
                           (hashed, mail._domain_id(domain), local))
            if cursor.rowcount != 1: raise ValidationError('Mailbox credential restore did not update exactly one mailbox')
    finally:
        conn.close()
    src = root / 'homedir' / 'mail' / domain / local
    dest = Path(settings.mail_base) / domain / local
    if src.is_dir():
        # Only private normalized regular files/directories reach this stage.
        shutil.copytree(src, dest, dirs_exist_ok=True)
        run(['chown', '-R', 'vmail:vmail', str(dest)], timeout=300, check=True)
    return 'mail folders, quota and original password hash restored'


def restore_app(root, username, entry, db_name_map, db_credentials=None):
    from daemon.cpanel_import import _copy_homedir
    name = 'migrated' + str(entry['index'])
    kind = entry['kind']
    module = nodeapps if kind == 'node' else pythonapps
    environment = validate_env_vars(entry.get('env') or {})
    database_env = {}
    if entry.get('database_name'):
        destination = db_name_map.get(entry['database_name'])
        if not destination or not db_credentials or destination not in db_credentials:
            raise ValidationError('Application database restore did not complete; refusing to start the app')
        db_user, db_password = db_credentials[destination]
        database_env = {'DB_HOST': '127.0.0.1', 'DB_PORT': '3306', 'DB_NAME': destination,
                        'DB_USER': db_user, 'DB_PASSWORD': db_password}
        environment.update(database_env)
    # Only preflighted framework mappings receive destination credentials.
    # Arbitrary URLs and external database connections are never guessed.
    params = {'username': username, 'domain': entry['domain'], 'name': name, 'env_vars': environment}
    if kind == 'node':
        params.update(entry_point=validate_app_entry_point(entry['startup']), node_version=entry['version'])
    else:
        startup = entry['startup']
        if not startup.endswith('.py'): raise ValidationError('Python startup must be a Python module')
        params.update(entry_point=validate_python_entry_point(startup[:-3].replace('/', '.') + ':' + entry.get('callable', 'application')), app_type='wsgi')
    try:
        app = module.create_app(params)
        with tempfile.TemporaryDirectory(prefix='boron-da-app-') as temporary:
            staging = Path(temporary)
            target = staging / 'homedir' / ('nodeapps' if kind == 'node' else 'pythonapps') / name
            target.parent.mkdir(parents=True)
            shutil.copytree(root / 'homedir' / entry['root'], target,
                            ignore=shutil.ignore_patterns('node_modules', 'venv', '__pycache__'))
            if database_env:
                dotenv = target / '.env'
                lines = dotenv.read_text().splitlines() if dotenv.is_file() else []
                lines = [line for line in lines if not re.match(r'\s*(?:export\s+)?(?:' + '|'.join(database_env) + r')\s*=', line)]
                lines.extend(key + '=' + json.dumps(value) for key, value in database_env.items())
                dotenv.write_text('\n'.join(lines) + '\n')
                dotenv.chmod(0o600)
            _copy_homedir(staging, username)
        action = {'username': username, 'id': app['id']}
        if kind == 'node': module.npm_install(action)
        else: module.pip_install(action)
        if not entry.get('enabled', True):
            return f"{kind} application recreated with runtime {entry['version']}; kept stopped as on source"
        module.start_app(action)
        deadline = time.monotonic() + 15
        state = module.get_app(action)
        while state.get('active') != 'active' and time.monotonic() < deadline:
            time.sleep(0.5)
            state = module.get_app(action)
        if state.get('active') != 'active':
            raise ValidationError('Application did not become active')
    except Exception:
        # Dependency managers may include registry credentials in their errors.
        raise ValidationError('Application recreation/start failed; migration will roll back. Check destination application logs privately.') from None
    return f"{kind} application recreated with runtime {entry['version']}; dependencies rebuilt as the account user"
