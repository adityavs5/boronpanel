"""Admin-only DirectAdmin pull migrations. Source credentials remain in memory.

Use documented user impersonation / user backups so both transports have
identical archive semantics. Source archives are retained; no DNS is changed.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import shlex
import time
import uuid
from contextlib import contextmanager
from urllib.parse import parse_qs, urlencode

import httpx
import paramiko

from daemon import webhooks
from shared.config import settings
from shared.validation import ValidationError, validate_domain

class UserBackupsDisabled(ValidationError):
    pass


DA = '/usr/local/directadmin/directadmin'
BACKUP_ITEMS = ('domain', 'subdomain', 'email', 'forwarder', 'autoresponder',
                'vacation', 'list', 'emailsettings', 'ftp', 'ftpsettings', 'database')


def source_username(value):
    # Source logins (not local Linux account creation) may legitimately be admin.
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9]{0,31}', value):
        raise ValidationError('Invalid DirectAdmin source username')
    return value


def fingerprint(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


def validate(params):
    mode = params.get('mode', 'admin')
    if mode not in ('admin', 'root'):
        raise ValidationError('Choose DirectAdmin admin or root SSH access')
    host = str(params.get('host', '')).strip()
    if not re.fullmatch(r'[a-zA-Z0-9.-]+', host) or len(host) > 253:
        raise ValidationError('Enter a server hostname or IPv4 address without a URL or path')
    port = int(params.get('port') or (22 if mode == 'root' else 2222))
    if not 1 <= port <= 65535:
        raise ValidationError('Port must be between 1 and 65535')
    login = source_username(params.get('login') or 'admin')
    if not isinstance(params.get('password'), str) or not params['password'] or len(params['password']) > 4096:
        raise ValidationError('A source password or DirectAdmin login key is required')
    if mode == 'root' and not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', params.get('host_key', '')):
        raise ValidationError('Enter the source SSH SHA256 host-key fingerprint (from its console)')
    # Pin the resolved public address for the entire connection, including HTTPS.
    ip = webhooks._assert_public_destination(f'https://{host}:{port}')
    return dict(params, mode=mode, host=host, port=port, login=login, ip=ip)


class PinnedKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected):
        self.expected = expected

    def missing_host_key(self, client, hostname, key):
        if fingerprint(key) != self.expected:
            raise ValidationError('SSH host key does not match the supplied fingerprint')


class Source:
    def __init__(self, params):
        self.p = validate(params)
        self.ssh = None
        self.http = None

    def __enter__(self):
        p = self.p
        try:
            if p['mode'] == 'root':
                self.ssh = paramiko.SSHClient()
                self.ssh.set_missing_host_key_policy(PinnedKey(p['host_key']))
                self.ssh.connect(p['ip'], port=p['port'], username='root', password=p['password'],
                                 look_for_keys=False, allow_agent=False, timeout=15,
                                 banner_timeout=15, auth_timeout=15)
            else:
                self.http = httpx.Client(timeout=httpx.Timeout(60, connect=15),
                                         follow_redirects=False, trust_env=False)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise ValidationError('Source connection failed. Check credentials, SSH fingerprint, and connectivity.') from None

    def __exit__(self, *args):
        if self.ssh:
            self.ssh.close()
        if self.http:
            self.http.close()
        self.p.pop('password', None)

    @contextmanager
    def stream(self, endpoint, data=None, user=None, method='GET'):
        """Never forward authentication to redirects or include remote errors in logs."""
        p = self.p
        data = data or {}
        if self.ssh:
            # Only fixed endpoints and shell-quoted query/form values. Curl runs
            # on the authenticated source, and connects only to its loopback DA.
            login = ' --user=' + shlex.quote(source_username(user)) if user else ''
            command = f'url=$({DA} api-url{login}) && '
            command += "curl --silent --fail --insecure --noproxy '*' --connect-timeout 15 --max-time 7200 --connect-to '*:*:127.0.0.1:' "
            query = '?' + urlencode(data) if method == 'GET' and data else ''
            if method == 'POST':
                command += '--data ' + shlex.quote(urlencode(data)) + ' '
            command += '"${url%/}"' + shlex.quote('/' + endpoint + query)
            stdin, stdout, stderr = self.ssh.exec_command(command, timeout=90)
            stdin.close()
            try:
                yield iter(lambda: stdout.read(256 * 1024), b'')
                if stdout.channel.recv_exit_status() != 0:
                    raise ValidationError('DirectAdmin request failed; verify API access and source backup permissions')
            finally:
                stdout.channel.close()
        else:
            ip = '[' + p['ip'] + ']' if ':' in p['ip'] else p['ip']
            url = f'https://{ip}:{p["port"]}/{endpoint}'
            login = p['login'] + ('|' + source_username(user) if user else '')
            request = self.http.build_request(method, url, params=data if method == 'GET' else None,
                data=data if method == 'POST' else None, headers={'Host': f'{p["host"]}:{p["port"]}'},
                extensions={'sni_hostname': p['host']})
            try:
                response = self.http.send(request, auth=(login, p['password']), stream=True)
                try:
                    if response.status_code != 200:
                        raise ValidationError('DirectAdmin rejected the request; check admin/login-key permissions')
                    yield response.iter_bytes(256 * 1024)
                finally:
                    response.close()
            except httpx.HTTPError:
                raise ValidationError('DirectAdmin HTTPS connection failed; use a hostname with a valid TLS certificate') from None

    def request(self, endpoint, data=None, user=None, method='GET'):
        content = io.BytesIO()
        with self.stream(endpoint, data, user, method) as chunks:
            for chunk in chunks:
                if content.tell() + len(chunk) > 4 * 1024 * 1024:
                    raise ValidationError('DirectAdmin response exceeded the metadata limit')
                content.write(chunk)
        text = content.getvalue().decode('utf-8', errors='strict')
        try:
            result = json.loads(text)
        except ValueError:
            if '<' in text or (text.strip() and '=' not in text):
                raise ValidationError('Unrecognized DirectAdmin API response')
            result = {k: v if k == 'list[]' else v[-1] for k, v in parse_qs(text, keep_blank_values=True).items()}
        if not isinstance(result, (dict, list)):
            raise ValidationError('Unrecognized DirectAdmin API response')
        if isinstance(result, dict) and str(result.get('error', '0')).lower() not in ('0', 'false', ''):
            # Do not reflect arbitrary source output: it can contain credentials.
            stage = {
                'CMD_API_SHOW_ALL_USERS': 'listing source accounts',
                'CMD_API_ADMIN_BACKUP': 'creating the administrator backup' if method == 'POST' else 'reading administrator backup settings',
                'CMD_API_SHOW_DOMAINS': 'listing the selected account domains',
                'CMD_API_SITE_BACKUP': 'creating the source backup' if method == 'POST' else 'listing source backups',
                'CMD_API_FILE_MANAGER': 'reading the source backup directory',
            }.get(endpoint, 'requesting source data')
            detail = ' '.join(str(result.get(k, '')) for k in ('text', 'details')).lower()
            if 'user backups have been disabled' in detail:
                raise UserBackupsDisabled('Source DirectAdmin has disabled user-level backups; administrator backup is required')
            reason = 'check its task/message log and API permissions'
            if 'domain' in detail and any(word in detail for word in ('invalid', 'exist', 'owned', 'belong')):
                reason = 'the source rejected the account domain; verify its ownership and status'
            elif any(word in detail for word in ('permission', 'not allowed', 'access denied', 'not permitted')):
                reason = 'the source denied permission; check login-key commands and user backup permissions'
            elif any(word in detail for word in ('disk space', 'quota', 'no space')):
                reason = 'the source has insufficient disk space or backup quota'
            raise ValidationError(f'DirectAdmin failed while {stage}: {reason}')
        return result

    def accounts(self):
        result = self.request('CMD_API_SHOW_ALL_USERS')
        names = result if isinstance(result, list) else result.get('list[]', result.get('list'))
        if not isinstance(names, list):
            raise ValidationError('DirectAdmin did not return an account list; use an administrator login')
        return sorted({source_username(name) for name in names})

    def backup_domain(self, user):
        # DA validates the domain context even though backups include all domains.
        # Cache it for the polling loop; never invent a domain for this account.
        if not hasattr(self, '_backup_domains'):
            self._backup_domains = {}
        if user not in self._backup_domains:
            result = self.request('CMD_API_SHOW_DOMAINS', user=user)
            names = result if isinstance(result, list) else result.get('list[]', result.get('list', []))
            if not isinstance(names, list) or not names:
                raise ValidationError('The selected DirectAdmin account has no domain available for a user backup')
            self._backup_domains[user] = validate_domain(names[0])
        return self._backup_domains[user]

    def backups(self, user):
        listing = self.request('CMD_API_SITE_BACKUP', {'domain': self.backup_domain(user)}, user)
        if not listing or (isinstance(listing, dict) and not listing.get('list[]', listing.get('list', []))):
            return {}
        result = self.request('CMD_API_FILE_MANAGER', {'path': '/backups'}, user)
        files = {}
        for path, item in result.items():
            if not re.fullmatch(r'/backups/[A-Za-z0-9_.-]+\.(?:tar\.gz|tgz)', path):
                continue
            if isinstance(item, str):
                item = {k: v[-1] for k, v in parse_qs(item, keep_blank_values=True).items()}
            if not isinstance(item, dict) or item.get('type') != 'file' or item.get('linkpath'):
                continue
            files[path] = (int(item.get('size', 0)), str(item.get('mtime', item.get('date', ''))))
        return files


    def start_admin_backup(self, user):
        """Run one full admin backup without altering saved schedules/settings."""
        config = self.request('CMD_API_ADMIN_BACKUP')
        login = self.p['login']
        location = config.get('location', '')
        # File Manager is relative to the admin home. Derive only a known
        # home path, never follow FTP destinations or arbitrary source paths.
        match = re.match(r'^(/home[0-9]*/' + re.escape(login) + r')(?:/|$)', location)
        if not match:
            raise ValidationError('Administrator backup location must be under its /home directory for HTTPS transfer')
        relative = '/admin_backups/boron-' + uuid.uuid4().hex
        self.request('CMD_API_ADMIN_BACKUP', {
            'action': 'create', 'when': 'now', 'who': 'selected', 'select0': user,
            'what': 'all', 'where': 'local', 'local_path': match[1] + relative,
            'append_to_path': 'nothing', 'write_backup_conf': 'no', 'form_version': '4',
        }, method='POST')
        return relative

    def admin_backups(self, directory, user):
        listing = self.request('CMD_API_FILE_MANAGER', {'path': directory})
        files = {}
        pattern = re.escape(directory) + r'/user\.[a-z][a-z0-9]*\.' + re.escape(user) + r'\.tar(?:\.gz|\.zst)?'
        for path, item in listing.items():
            if not re.fullmatch(pattern, path):
                continue
            if isinstance(item, str):
                item = {k: v[-1] for k, v in parse_qs(item, keep_blank_values=True).items()}
            if not isinstance(item, dict) or item.get('type') != 'file' or item.get('linkpath'):
                continue
            files[path] = (int(item.get('size', 0)), str(item.get('mtime', item.get('date', ''))))
        return files


def inspect_source(params):
    with Source(params) as source:
        names = source.accounts()
    from sqlalchemy import select
    from shared.db import write_session
    from shared.models import Account, PanelUser, CpanelImportJob
    with write_session() as session:
        occupied = set(session.scalars(select(Account.username))) | set(session.scalars(select(PanelUser.username)))
        active_jobs = select(CpanelImportJob.username).where(CpanelImportJob.status.in_(['pending', 'running']))
        occupied |= set(session.scalars(active_jobs))
    from daemon.import_compat import target_capabilities
    target = target_capabilities()
    return {'accounts': [{'username': name, 'available': name not in occupied} for name in names],
            'destination_database': target['version'],
            'notes': ['Live sites remain active on the source. Schedule a final sync/cutover to avoid losing later writes.',
                      'Source backups are retained. DNS and nameservers are not switched automatically.',
                      'Database dumps are checked before account creation; incompatible features block the import by default.']}


def fetch_archive(job_id, params, work_dir):
    from daemon.cpanel_import import _update_job
    user = source_username(params['remote_user'])
    try:
        with Source(params['remote']) as source:
            if user not in source.accounts():
                raise ValidationError('Selected account is not available on the source')
            admin_directory = None
            before = source.backups(user)
            _update_job(job_id, progress_message='creating DirectAdmin source backup')
            payload = {'action': 'backup', 'domain': source.backup_domain(user)}
            payload.update({f'select{i}': item for i, item in enumerate(BACKUP_ITEMS)})
            try:
                source.request('CMD_API_SITE_BACKUP', payload, user, 'POST')
            except UserBackupsDisabled:
                _update_job(job_id, progress_message='creating administrator backup (source user backups disabled)')
                admin_directory = source.start_admin_backup(user)
                before = {}
            deadline = time.monotonic() + 7200
            last = {}
            stable = {}
            backup_started = time.monotonic()
            while time.monotonic() < deadline:
                time.sleep(10)
                _update_job(job_id, progress_message=f'waiting for source backup — {int(time.monotonic() - backup_started)}s elapsed')
                current = source.admin_backups(admin_directory, user) if admin_directory else source.backups(user)
                for path, signature in current.items():
                    if signature == before.get(path) or signature[0] <= 0:
                        continue
                    stable[path] = stable.get(path, 0) + 1 if last.get(path) == signature else 0
                    if stable[path] < 3:
                        continue
                    if signature[0] > settings.cpanel_import_max_upload_bytes:
                        raise ValidationError('Source backup exceeds the configured import size limit')
                    _update_job(job_id, progress_message='transferring DirectAdmin backup')
                    dest = work_dir / 'directadmin.tar.gz'
                    total = 0
                    last_progress = time.monotonic()
                    with source.stream('CMD_FILE_MANAGER', {'path': path}, None if admin_directory else user) as chunks, dest.open('wb') as output:
                        for chunk in chunks:
                            total += len(chunk)
                            if total > settings.cpanel_import_max_upload_bytes:
                                raise ValidationError('Source backup exceeds the configured import size limit')
                            output.write(chunk)
                            if time.monotonic() - last_progress >= 2:
                                _update_job(job_id, progress_message=f'transferring backup — {total / 1048576:.1f} / {signature[0] / 1048576:.1f} MiB')
                                last_progress = time.monotonic()
                    after = source.admin_backups(admin_directory, user) if admin_directory else source.backups(user)
                    if total != signature[0] or after.get(path) != signature:
                        dest.unlink(missing_ok=True)
                        raise ValidationError('Source backup changed during transfer; retry after the backup completes')
                    return dest
                last = current
            raise ValidationError('Source backup did not finish within two hours; inspect DirectAdmin task/message logs')
    except ValidationError:
        raise
    except Exception:
        raise ValidationError('DirectAdmin transfer failed; verify source connectivity and backup status') from None
    finally:
        params.get('remote', {}).pop('password', None)


def recover_interrupted():
    """Secrets intentionally do not survive a daemon restart; release queue locks."""
    from sqlalchemy import select
    from shared.db import write_session
    from shared.models import CpanelImportJob, utcnow
    with write_session() as session:
        jobs = session.scalars(select(CpanelImportJob).where(
            CpanelImportJob.source == 'directadmin_remote',
            CpanelImportJob.status.in_(['pending', 'running'])))
        for job in jobs:
            job.status = 'failed'
            job.progress_message = 'interrupted by daemon restart'
            job.error = 'Reconnect to the source to retry. If an account was already created, inspect it before choosing a new destination username.'
            job.completed_at = utcnow()
            job.source_ref = None
