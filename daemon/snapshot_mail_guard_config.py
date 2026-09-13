"""Render and verify the Dovecot 2.3 restore userdb guard before activation."""
from pathlib import Path
import re
import stat

from daemon.procutil import run
from shared.config import settings
from shared.validation import ValidationError

GUARD_BINARY = '/usr/local/libexec/boron-mail-restore-gate'


def render(binary=GUARD_BINARY):
    if not isinstance(binary, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', binary) or '..' in Path(binary).parts:
        raise ValidationError('Invalid mail restore guard executable')
    return ('userdb {\n  driver = checkpassword\n  args = ' + binary + '\n'
            '  result_failure = continue\n  result_internalfail = return-fail\n'
            '  result_success = return-fail\n  skip = never\n}\n')


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
    if not directory.is_dir() or directory.stat().st_mode & 0o077:
        raise ValidationError('Mail restore guard storage must be private')
    compiled = run([binary, '--guard-directory'], timeout=5)
    if not compiled.ok or compiled.stdout.strip() != str(directory):
        raise ValidationError('Mail restore guard was built for a different storage directory')
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
