"""Render and verify the Dovecot 2.3 restore userdb guard before activation."""
from pathlib import Path
import re
import stat
import os
import tempfile
import grp
import uuid
import time

from daemon.procutil import run
from shared.config import settings
from shared.validation import ValidationError

GUARD_BINARY = '/usr/local/libexec/boron-mail-restore-gate'
MANAGED_HEADER = '# Managed by Boron: mailbox restore guard\n'


def _require_mail_running(service_status, *, wait_for_reload=False, timeout=20.0):
    """Require a steady Dovecot service, allowing its bounded reload state.

    ``doveadm reload`` returns before systemd necessarily changes the service
    substate back to ``running``.  The initial activation gate remains strict;
    only the post-reload probe waits through active/reloading transitions.
    """
    deadline = time.monotonic() + timeout
    while True:
        state = service_status('dovecot.service')
        if state.get('ActiveState') == 'active' and state.get('SubState') == 'running':
            return
        transitional = state.get('ActiveState') in {'active', 'activating', 'reloading'}
        if not wait_for_reload or not transitional or time.monotonic() >= deadline:
            raise ValidationError('Dovecot must be running during guard activation')
        time.sleep(0.25)


def _write_config(path, data, mode=0o644, uid=0, gid=0):
    fd, temporary = tempfile.mkstemp(prefix='.boron-mail-config-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fchown(handle.fileno(), uid, gid)
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install_configuration(backup_dir, *, config='/etc/dovecot/dovecot.conf', binary=GUARD_BINARY,
                          reload=False, health_check=None):
    """Prepend a managed guard, validate effective config, and optionally reload.

    Existing configuration bytes/permissions are preserved in a private backup.
    Validation/reload failures restore them. No mailbox data is changed here.
    """
    render(binary)
    main = Path(config)
    fragment = main.with_name('boron-restore-guard.conf.ext')
    render(str(fragment))
    backup = Path(backup_dir)
    if os.geteuid() != 0 or any(path.resolve() != path for path in (main, fragment, backup)):
        raise ValidationError('Mail guard configuration requires root-controlled paths')
    parent_info = main.parent.stat()
    if parent_info.st_uid != 0 or parent_info.st_mode & 0o022:
        raise ValidationError('Dovecot configuration directory is not controlled by root')
    originals = {}
    for path in (main, fragment):
        if path.exists():
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValidationError('Dovecot configuration is not controlled by root')
            originals[path] = (path.read_bytes(), stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)
    if main not in originals:
        raise ValidationError('Main Dovecot configuration is missing')
    if fragment in originals and not originals[fragment][0].startswith(MANAGED_HEADER.encode()):
        raise ValidationError('An unmanaged file occupies the mail guard configuration path')
    backup.mkdir(mode=0o700, exist_ok=True)
    if backup.stat().st_uid != 0 or backup.stat().st_mode & 0o077:
        raise ValidationError('Mail configuration backup must be private and root-owned')
    saved = backup / uuid.uuid4().hex
    saved.mkdir(mode=0o700)
    parent = os.open(backup, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    for path, original in originals.items():
        _write_config(saved / path.name, original[0], 0o600)
    include = '!include ' + str(fragment)
    original_main = originals[main][0].decode()
    updated = include + '\n' + ''.join(line for line in original_main.splitlines(keepends=True)
                                      if line.strip() != include)
    try:
        _write_config(fragment, (MANAGED_HEADER + render(binary)).encode())
        _write_config(main, updated.encode(), *originals[main][1:])
        verify(binary=binary, config=main)
        if reload:
            result = run(['/usr/bin/doveadm', '-c', str(main), 'reload'], timeout=20)
            if not result.ok:
                raise ValidationError('Dovecot rejected the guard configuration reload')
            if health_check is not None:
                health_check()
    except Exception:
        for path in (main, fragment):
            if path in originals:
                original = originals[path]
                _write_config(path, original[0], *original[1:])
            else:
                path.unlink(missing_ok=True)
        if reload:
            restored = run(['/usr/bin/doveadm', '-c', str(main), 'reload'], timeout=20)
            if not restored.ok:
                raise ValidationError('Original configuration restored; Dovecot reload still requires recovery') from None
        raise
    return {'configuration': str(fragment), 'backup': str(saved), 'reloaded': reload}


def install_binary(*, binary=GUARD_BINARY):
    """Build and atomically install the guard; does not activate/reload Dovecot."""
    render(binary)
    path = Path(binary)
    directory = Path(settings.mail_restore_guard_dir)
    render(str(directory))  # same conservative path alphabet for the C literal
    if os.geteuid() != 0 or directory == Path('/'):
        raise ValidationError('Mail restore guard installation requires root-owned storage')
    for parent, mode in ((path.parent, 0o755), (directory, 0o710)):
        if parent.resolve() != parent:
            raise ValidationError('Mail restore guard paths must not contain symbolic links')
        parent.mkdir(exist_ok=True, mode=mode)
        info = parent.stat()
        if info.st_uid != 0 or info.st_mode & (0o067 if parent == directory else 0o022):
            raise ValidationError('Mail restore guard installation directories have unsafe ownership or permissions')
    os.chown(directory, 0, grp.getgrnam('dovecot').gr_gid)
    directory.chmod(0o710)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValidationError('Existing mail restore guard cannot be replaced safely')
        current = run([binary, '--guard-directory'], timeout=5)
        if not current.ok or current.stdout.strip() != str(directory):
            raise ValidationError('Existing guard requires an explicit storage-path migration')
    source = Path(__file__).with_name('mail_restore_gate.c')
    fd, temporary = tempfile.mkstemp(prefix='.boron-mail-guard-', dir=path.parent)
    os.close(fd)
    try:
        result = run(['/usr/bin/cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                      '-fstack-protector-strong', '-D_FORTIFY_SOURCE=2', '-Wl,-z,relro,-z,now',
                      f'-DBORON_MAIL_RESTORE_GATES="{directory}"', str(source), '-o', temporary, '-lcrypto'],
                     timeout=60)
        if not result.ok:
            raise ValidationError('Could not build mail restore guard; install build-essential and libssl-dev')
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o755)
        checked = run([temporary, '--guard-directory'], timeout=5)
        if not checked.ok or checked.stdout.strip() != str(directory):
            raise ValidationError('Built mail restore guard failed its storage-path check')
        _verify_access(temporary)
        with open(temporary, 'rb') as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        for parent in (path.parent, directory, directory.parent):
            fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'binary': str(path), 'guard_directory': str(directory)}


def render(binary=GUARD_BINARY):
    if not isinstance(binary, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', binary) or '..' in Path(binary).parts:
        raise ValidationError('Invalid mail restore guard executable')
    return ('userdb {\n  driver = checkpassword\n  args = ' + binary + '\n'
            '  result_failure = continue\n  result_internalfail = return-fail\n'
            '  result_success = return-fail\n  skip = never\n}\n')


def _verify_access(binary):
    result = run(['/usr/bin/setpriv', '--reuid=dovecot', '--regid=dovecot',
                  '--clear-groups', binary, '--check-access'], timeout=5)
    if not result.ok:
        raise ValidationError('Dovecot cannot traverse the mail restore guard path')


def verify(*, binary=GUARD_BINARY, config=None):
    """Read-only preflight; configuration and compilation are separate operations."""
    render(binary)
    path = Path(binary)
    directory = Path(settings.mail_restore_guard_dir)
    for value in (path, directory):
        if value.resolve() != value:
            raise ValidationError('Mail restore guard paths must not contain symbolic links')
        info = value.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValidationError('Mail restore guard paths must be controlled by root')
    if not stat.S_ISREG(path.stat().st_mode) or not path.stat().st_mode & 0o111:
        raise ValidationError('Mail restore guard executable is unavailable')
    if (not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) != 0o710
            or directory.stat().st_gid != grp.getgrnam('dovecot').gr_gid):
        raise ValidationError('Mail restore guard storage must be private')
    compiled = run([binary, '--guard-directory'], timeout=5)
    if not compiled.ok or compiled.stdout.strip() != str(directory):
        raise ValidationError('Mail restore guard was built for a different storage directory')
    _verify_access(binary)
    command = ['/usr/bin/doveconf', *(['-c', str(config)] if config else [])]
    cache = run([*command, '-h', 'auth_cache_size'], timeout=10)
    if not cache.ok or cache.stdout.strip() != '0':
        raise ValidationError('Disable Dovecot authentication caching before mailbox restoration')
    databases = run([*command, '-h', 'userdb'], timeout=10)
    blocks = re.findall(r'userdb\s*\{([^{}]*)\}', databases.stdout)
    if not databases.ok or not blocks:
        raise ValidationError('Could not verify Dovecot mailbox restore guard ordering')
    fields = dict(line.strip().split(' = ', 1) for line in blocks[0].splitlines() if ' = ' in line.strip())
    expected = {'driver': 'checkpassword', 'args': binary, 'result_failure': 'continue',
                'result_internalfail': 'return-fail', 'result_success': 'return-fail', 'skip': 'never'}
    if any(fields.get(key) != value for key, value in expected.items()):
        raise ValidationError('Dovecot must run the mailbox restore guard before every other userdb')
    return {'guard': 'ready'}


def main():
    """Fresh installation or activation on an existing running mail server."""
    import argparse
    parser = argparse.ArgumentParser(description='Install the Dovecot mailbox restore guard')
    parser.add_argument('--backup-dir', required=True)
    parser.add_argument('--reload', action='store_true')
    args = parser.parse_args()
    try:
        from daemon.snapshot_mail_service import service_status
        if args.reload:
            _require_mail_running(service_status)
        install_binary()
        install_configuration(args.backup_dir, reload=args.reload,
                              health_check=(lambda: _require_mail_running(
                                  service_status, wait_for_reload=True)) if args.reload else None)
    except Exception:
        # Configuration can contain SQL credentials; never print exception data.
        parser.exit(1, 'Mailbox restore guard installation failed; inspect Dovecot configuration and build dependencies.\n')
    print('Mailbox restore guard installed and configuration validated.' if args.reload else
          'Mailbox restore guard installed and configuration validated; Dovecot must load it on startup.')


if __name__ == '__main__':
    main()
