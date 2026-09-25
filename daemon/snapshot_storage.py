"""Encrypted, deduplicated backup storage. Account authorization remains explicit.

Restores always land in an empty staging directory. Applying a restore to an
account is a separate operation, after ownership and archive contents checks.
"""
from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
from urllib.parse import quote, urlsplit

from daemon.procutil import run
from shared.config import settings
from shared.validation import ValidationError


class SnapshotStorageError(RuntimeError):
    pass


def _absolute(value):
    path = Path(value)
    if not path.is_absolute() or path == Path('/') or '..' in path.parts or any(c in str(value) for c in ('\0','\n','\r')):
        raise ValidationError('Use an absolute path without parent-directory references or control characters')
    return path


def _private_file(value):
    path = _absolute(value)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValidationError('Backup credential files must be private regular files owned by the backup service')
    return path


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError('Invalid backup account or policy identifier')
    return value


@dataclass(frozen=True)
class Repository:
    kind: str
    path: str
    namespace: str
    password_file: str
    cache_dir: str
    ssh_host: str = ''
    ssh_user: str = ''
    ssh_port: int = 22
    ssh_key_file: str = ''
    ssh_known_hosts_file: str = ''
    ssh_auth: str = 'key'
    ssh_password: str = field(default='', repr=False)
    ssh_askpass_file: str = ''
    s3_endpoint: str = ''
    s3_bucket: str = ''
    s3_region: str = ''
    s3_credentials: dict = field(default_factory=dict, repr=False)
    rclone_config_file: str = ''
    rclone_remote_name: str = ''

    def __post_init__(self):
        if self.kind not in ('local','ssh','sftp','s3','drive'):
            raise ValidationError('Choose local, SSH, SFTP, S3-compatible, or Google Drive storage')
        if self.kind in ('local','ssh','sftp'):
            _absolute(self.path)
        elif self.path and (self.path.startswith('/') or '..' in Path(self.path).parts or not re.fullmatch(r'[A-Za-z0-9._ /-]{0,512}', self.path)):
            raise ValidationError('Invalid repository prefix')
        _absolute(self.cache_dir)
        if not re.fullmatch(r'[a-z0-9-]{8,64}', self.namespace):
            raise ValidationError('Invalid backup repository namespace')
        if self.kind in ('ssh','sftp'):
            try:
                ipaddress.ip_address(self.ssh_host)
            except ValueError:
                if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', self.ssh_host):
                    raise ValidationError('Invalid SSH hostname')
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}', self.ssh_user):
                raise ValidationError('Invalid SSH username')
            if isinstance(self.ssh_port, bool) or not isinstance(self.ssh_port, int) or not 1 <= self.ssh_port <= 65535:
                raise ValidationError('SSH port must be between 1 and 65535')
            if self.ssh_auth not in ('key','password'):
                raise ValidationError('Choose SSH key or password authentication')
            if self.ssh_auth == 'password' and (not self.ssh_password or len(self.ssh_password) > 2048):
                raise ValidationError('SSH password is required')
        if self.kind == 's3':
            endpoint = urlsplit(self.s3_endpoint)
            if endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                raise ValidationError('S3 endpoint must be an HTTPS URL without credentials, query, or fragment')
            if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', self.s3_bucket) or '..' in self.s3_bucket:
                raise ValidationError('Enter a valid S3 bucket name')
            if self.s3_region and not re.fullmatch(r'[A-Za-z0-9-]{1,64}', self.s3_region):
                raise ValidationError('Invalid S3 region')
            if not self.s3_credentials.get('access_key') or not self.s3_credentials.get('secret_key'):
                raise ValidationError('S3 access key and secret key are required')
        if self.kind == 'drive':
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', self.rclone_remote_name):
                raise ValidationError('Invalid Google Drive remote name')
            _private_file(self.rclone_config_file)

    def owner_tag(self, account_id):
        return f'boron:{self.namespace}:account:{_positive(account_id)}'

    def password_value(self):
        password = _private_file(self.password_file)
        value = password.read_text().rstrip('\r\n')
        if not value:
            raise ValidationError('Backup password file is empty')
        return value

    def arguments(self, *, password_file=True):
        password = _private_file(self.password_file)
        cache = _absolute(self.cache_dir)
        cache.mkdir(parents=True, mode=0o700, exist_ok=True)
        if cache.is_symlink() or cache.stat().st_uid != os.geteuid():
            raise ValidationError('Backup cache must be owned by the backup service')
        cache.chmod(0o700)
        extras = []
        repository = self.path
        if self.kind in ('ssh','sftp'):
            known_hosts = _private_file(self.ssh_known_hosts_file)
            host = f'[{self.ssh_host}]' if ':' in self.ssh_host else self.ssh_host
            repository = f'sftp://{self.ssh_user}@{host}:{self.ssh_port}/{quote(self.path, safe="/")}'
            ssh = ['/usr/bin/ssh','-F','/dev/null','-p',str(self.ssh_port),'-l',self.ssh_user,
                '-o','IdentitiesOnly=yes','-o','IdentityAgent=none',
                '-o','StrictHostKeyChecking=yes','-o',f'UserKnownHostsFile={known_hosts}',
                '-o','GlobalKnownHostsFile=/dev/null','-o','ConnectTimeout=20',
                '-o','ServerAliveInterval=30','-o','ServerAliveCountMax=6','-s',self.ssh_host,'sftp']
            if self.ssh_auth == 'key':
                key = _private_file(self.ssh_key_file)
                ssh[7:7] = ['-i',str(key),'-o',f'BatchMode={"no" if self.ssh_password else "yes"}']
            else:
                askpass = _private_file(self.ssh_askpass_file)
                ssh[7:7] = ['-o','BatchMode=no','-o','PreferredAuthentications=password',
                    '-o','PubkeyAuthentication=no','-o','NumberOfPasswordPrompts=1']
            extras = ['-o','sftp.command='+shlex.join(ssh)]
        elif self.kind == 's3':
            location = '/'.join(part for part in (self.s3_bucket, self.path.strip('/')) if part)
            repository = f's3:{self.s3_endpoint.rstrip("/")}/{location}'
        elif self.kind == 'drive':
            repository = f'rclone:{self.rclone_remote_name}:{self.path.strip("/")}'
        args = [settings.restic_bin,'--repo',repository]
        if password_file:
            args += ['--password-file',str(password)]
        args += ['--cache-dir',str(cache),'--retry-lock','30s','--json',*extras]
        return args


def _landlock_command(repository, command, read_roots):
    helper = Path(__file__).with_name('landlock_exec.py')
    wrapper = [sys.executable, str(helper)]
    for path in read_roots:
        wrapper += ['--ro', str(_absolute(path))]
    if repository.kind == 'local':
        wrapper += ['--rw', str(_absolute(repository.path))]
    wrapper += ['--rw', str(_absolute(repository.cache_dir))]
    if repository.kind in ('ssh','sftp'):
        if repository.ssh_auth == 'key':
            wrapper += ['--ro', str(_private_file(repository.ssh_key_file))]
        if repository.ssh_auth == 'password' or repository.ssh_password:
            wrapper += ['--ro', str(_private_file(repository.ssh_askpass_file))]
        wrapper += ['--ro', str(_private_file(repository.ssh_known_hosts_file))]
        wrapper += ['--exec', '/usr/bin/ssh']
    elif repository.kind == 's3':
        for certificates in ('/etc/ssl/certs', '/usr/share/ca-certificates'):
            if Path(certificates).exists():
                wrapper += ['--ro', certificates]
    elif repository.kind == 'drive':
        # rclone refreshes OAuth access tokens in this exact private config.
        wrapper += ['--rw', str(_private_file(repository.rclone_config_file))]
        wrapper += ['--exec', str(_absolute(settings.rclone_bin))]
        for certificates in ('/etc/ssl/certs', '/usr/share/ca-certificates'):
            if Path(certificates).exists():
                wrapper += ['--ro', certificates]
    for executable in ['/usr/bin/nice', '/usr/bin/ionice', settings.restic_bin]:
        wrapper += ['--exec', str(_absolute(executable))]
    return [*wrapper, '--', *command]


def _execute(repository, arguments, timeout=3600, sandbox_roots=None):
    threads = settings.snapshot_cpu_threads
    if isinstance(threads,bool) or not isinstance(threads,int) or not 1 <= threads <= 8:
        raise ValidationError('Snapshot CPU threads must be between 1 and 8')
    sandboxed = sandbox_roots is not None
    command = ['/usr/bin/nice','-n','10','/usr/bin/ionice','-c','2','-n','7',
        *repository.arguments(password_file=not sandboxed),*arguments]
    env = {**os.environ, 'GOMAXPROCS': str(threads)}
    if repository.kind == 's3':
        env.update({
            'AWS_ACCESS_KEY_ID': repository.s3_credentials['access_key'],
            'AWS_SECRET_ACCESS_KEY': repository.s3_credentials['secret_key'],
            'AWS_DEFAULT_REGION': repository.s3_region or 'us-east-1',
            'AWS_REGION': repository.s3_region or 'us-east-1',
        })
        if repository.s3_credentials.get('session_token'):
            env['AWS_SESSION_TOKEN'] = repository.s3_credentials['session_token']
    elif repository.kind in ('ssh','sftp') and (repository.ssh_auth == 'password' or repository.ssh_password):
        env.update({'BORON_SSH_PASSWORD':repository.ssh_password,'SSH_ASKPASS':repository.ssh_askpass_file,
            'SSH_ASKPASS_REQUIRE':'force','DISPLAY':'boron:0'})
    elif repository.kind == 'drive':
        env['RCLONE_CONFIG'] = repository.rclone_config_file
    if sandboxed:
        env['RESTIC_PASSWORD'] = repository.password_value()
        env['TMPDIR'] = str(_absolute(repository.cache_dir))
        command = _landlock_command(repository, command, sandbox_roots)
    result = run(command, timeout=timeout, env=env)
    if not result.ok:
        # No passwords or private keys are passed on the command line.
        raise SnapshotStorageError((result.stderr.strip() or 'Backup storage operation failed')[-3000:])
    rows = []
    for line in result.stdout.splitlines():
        try: rows.append(json.loads(line))
        except ValueError: continue
    return rows


def initialize(repository):
    if repository.kind == 'local':
        target = _absolute(repository.path)
        target.mkdir(parents=True,exist_ok=True,mode=0o700)
        if target.is_symlink() or target.stat().st_uid != os.geteuid():
            raise ValidationError('Local repository must be owned by the backup service')
        if any(target.iterdir()):
            raise ValidationError('Choose an empty directory for a new backup repository')
        target.chmod(0o700)
    _execute(repository,['init'],timeout=120)


def _literal_pattern(value):
    # Restic uses Go filepath.Match: escape metacharacters, including backslash.
    return ''.join('\\' + char if char in '\\*?[]' else char for char in str(value))


def _filters(patterns):
    if len(patterns) > 200:
        raise ValidationError('Use at most 200 backup exclusion patterns')
    for pattern in patterns:
        if not isinstance(pattern,str) or not pattern or len(pattern)>1024 or any(c in pattern for c in ('\0','\n','\r')):
            raise ValidationError('Invalid backup exclusion pattern')
        yield pattern


def backup(repository, account_id, paths, *, policy_id=None, excludes=(), full_scan=False, exclude_mail_staging=False,
           recovery_operation=None, sandbox_roots=None):
    limit = 1001 if recovery_operation is not None else 200
    if not paths or len(paths)>limit:
        raise ValidationError(f'Select between 1 and {limit} backup paths')
    source_paths = [str(_absolute(p)) for p in paths]
    args = ['backup','--host','boron','--tag',repository.owner_tag(account_id)]
    if recovery_operation is not None:
        if not isinstance(recovery_operation, str) or not re.fullmatch(r'[a-f0-9]{32}', recovery_operation):
            raise ValidationError('Invalid backup recovery operation')
        args += ['--tag', 'mail-safety:' + recovery_operation]
    if full_scan: args += ['--force']
    if policy_id is not None: args += ['--tag',f'policy:{_positive(policy_id)}']
    for pattern in _filters(excludes): args += ['--exclude',pattern]
    if exclude_mail_staging:
        # Ordinary mail jobs must not absorb prepared/displaced recovery trees.
        # Explicit safety snapshots leave this off so those trees can be saved.
        args += ['--exclude', _literal_pattern(_absolute(settings.mail_base)) + '/*/*/.boron-mail-ready-*']
    # The repository/cache must never recursively become part of a snapshot.
    if repository.kind == 'local': args += ['--exclude',_literal_pattern(_absolute(repository.path))]
    args += ['--exclude',_literal_pattern(_absolute(repository.cache_dir)),'--exclude',_literal_pattern(_absolute(repository.password_file))]
    if repository.kind in ('ssh','sftp'):
        if repository.ssh_auth == 'key':args += ['--exclude',_literal_pattern(_absolute(repository.ssh_key_file))]
        else:args += ['--exclude',_literal_pattern(_absolute(repository.ssh_askpass_file))]
        args += ['--exclude',_literal_pattern(_absolute(repository.ssh_known_hosts_file))]
    rows = _execute(repository,[*args,'--',*source_paths], sandbox_roots=sandbox_roots or source_paths)
    summary = next((r for r in reversed(rows) if isinstance(r,dict) and r.get('message_type')=='summary'),None)
    if not summary or not re.fullmatch(r'[a-f0-9]{64}',summary.get('snapshot_id','')):
        raise SnapshotStorageError('Backup storage did not return a completed snapshot')
    return summary


def snapshots(repository, account_id):
    tag = repository.owner_tag(account_id)
    rows = _execute(repository,['snapshots','--tag',tag],timeout=120)
    items = next((row for row in rows if isinstance(row,list)),[])
    return [item for item in items if tag in item.get('tags',[])]


def all_snapshots(repository):
    """Administrator-only repository inventory used for catalog recovery."""
    rows = _execute(repository, ['snapshots'], timeout=120)
    items = next((row for row in rows if isinstance(row, list)), [])
    prefix = f'boron:{repository.namespace}:account:'
    return [item for item in items if any(str(tag).startswith(prefix) for tag in item.get('tags', []))]


def owned_snapshot(repository, account_id, snapshot_id):
    if not isinstance(snapshot_id,str) or not re.fullmatch(r'[a-f0-9]{64}',snapshot_id):
        raise ValidationError('Invalid snapshot identifier')
    rows = _execute(repository,['snapshots',snapshot_id],timeout=120)
    items = next((row for row in rows if isinstance(row,list)),[])
    item = next((r for r in items if r.get('id')==snapshot_id and repository.owner_tag(account_id) in r.get('tags',[])),None)
    if item is None: raise ValidationError('Snapshot not found for this account')
    return item


def entries(repository, account_id, snapshot_id, directory='/'):
    return entries_many(repository, account_id, snapshot_id, [directory])


def entries_many(repository, account_id, snapshot_id, directories):
    if not directories or len(directories) > 1000:
        raise ValidationError('Select between 1 and 1000 snapshot directories')
    owned_snapshot(repository,account_id,snapshot_id)
    paths = sorted({'/' if str(directory) == '/' else str(_absolute(directory)) for directory in directories})
    rows = _execute(repository,['ls',snapshot_id,*paths],timeout=120)
    return [r for r in rows if isinstance(r,dict) and r.get('struct_type')=='node']


def restore_to(repository, account_id, snapshot_id, directory, *, selected_paths=()):
    snapshot = owned_snapshot(repository,account_id,snapshot_id)
    includes = []
    if len(selected_paths)>1000:
        raise ValidationError('Select at most 1000 paths to restore')
    selected_values = [_absolute(value) for value in selected_paths]
    for selected in selected_values:
        if not any(selected == Path(root) or selected.is_relative_to(Path(root)) for root in snapshot.get('paths',[])):
            raise ValidationError('Restore path is outside this snapshot')
    found = {node.get('path') for node in entries_many(repository, account_id, snapshot_id,
             [str(selected.parent) for selected in selected_values])} if selected_values else set()
    for selected in selected_values:
        if str(selected) not in found:
            raise ValidationError('Restore path was not found in this snapshot')
        includes += ['--include',_literal_pattern(selected)]
    target = _absolute(directory)
    target.mkdir(parents=True,exist_ok=True,mode=0o700)
    if target.is_symlink() or target.stat().st_uid != os.geteuid() or any(target.iterdir()):
        raise ValidationError('Restore staging must be an empty directory owned by the backup service')
    target.chmod(0o700)
    _execute(repository,['restore',snapshot_id,'--target',str(target),'--verify',*includes])
    return target


def check(repository):
    _execute(repository,['check'],timeout=3600)


def forget(repository, account_id, snapshot_ids, *, prune=False):
    if not snapshot_ids or len(snapshot_ids)>500:
        raise ValidationError('Select between 1 and 500 snapshots for retention cleanup')
    # Validate every target before deleting any of them.
    for snapshot_id in snapshot_ids:
        owned_snapshot(repository,account_id,snapshot_id)
    _execute(repository,['forget',*(['--prune'] if prune else []),*snapshot_ids])
