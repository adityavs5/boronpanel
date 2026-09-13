"""Private switch journal and offline exchange worker.

The restore coordinator owns account authorization and preparation. This worker
accepts only an exclusive service-owned journal within private snapshot storage.
It never releases guards or deletes displaced mail; partial batches are inspected
by inode identity after interruption, not silently replayed.
"""
import json
import os
from pathlib import Path
import re
import stat
import sys

from daemon import snapshot_mail_exchange as exchange, snapshot_mail_guard as guard
from daemon.snapshot_mail_service import require_stopped, supervised_command
from shared.config import settings
from shared.validation import ValidationError, validate_domain, validate_mailbox_local_part


def validate(payload):
    try:
        if payload['format'] != 1 or type(payload['restore_id']) is not int or payload['restore_id'] <= 0:
            raise ValueError()
        if not re.fullmatch(r'[a-f0-9]{32}', payload['operation_id']):
            raise ValueError()
        if type(payload['undo']) is not bool:
            raise ValueError()
        entries = payload['entries']
        if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
            raise ValueError()
        seen = set()
        for entry in entries:
            address = (validate_domain(entry['domain']), validate_mailbox_local_part(entry['local_part']))
            if address in seen or not re.fullmatch(r'[a-f0-9]{64}', entry['token']):
                raise ValueError()
            seen.add(address)
            plan = entry['plan']
            if plan['format'] != 1:
                raise ValueError()
            exchange._prepared_name(plan['prepared'])
            for key in ('home', 'current', 'replacement'):
                value = plan[key]
                if not isinstance(value, list) or len(value) != 2 or any(type(v) is not int or v < 0 for v in value):
                    raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValidationError('Invalid private mailbox switch journal') from None
    return payload


def _path(path):
    path = Path(path)
    root = Path(settings.snapshot_private_dir)
    if (os.geteuid() != 0 or root.resolve() != root or path.resolve() != path
            or not path.is_relative_to(root) or path == root):
        raise ValidationError('Mailbox journal must remain in private snapshot storage')
    info = root.stat()
    if info.st_uid != 0 or info.st_mode & 0o077:
        raise ValidationError('Mailbox journal directory must be private and root-owned')
    for directory in path.parents:
        if directory == root:
            break
        info = directory.stat()
        if info.st_uid != 0 or info.st_mode & 0o077:
            raise ValidationError('Mailbox journal directory must be private and root-owned')
    return path


def create(path, payload):
    payload = validate(payload)
    path = _path(path)
    data = json.dumps(payload, separators=(',', ':')).encode()
    if len(data) > 1024 * 1024:
        raise ValidationError('Mailbox switch journal is too large')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return path


def read(path):
    path = _path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 1024 * 1024:
            raise ValidationError('Invalid private mailbox journal file')
        try:
            return validate(json.loads(handle.read(1024 * 1024 + 1)))
        except (ValueError, UnicodeError):
            raise ValidationError('Invalid private mailbox switch journal') from None


def inspect(path):
    payload = read(path)
    return [{'domain': entry['domain'], 'local_part': entry['local_part'],
             'state': exchange.inspect(entry['domain'], entry['local_part'], entry['plan'])}
            for entry in payload['entries']]


def execute(path, *, service='dovecot.service'):
    payload = read(path)
    if service == 'dovecot.service':
        from daemon.snapshot_mail_guard_config import verify
        verify()
    require_stopped(service)
    with guard.owned_guards(payload['entries'], payload['restore_id']):
        required = 'applied' if payload['undo'] else 'ready'
        # Validate every item before changing any. A partially applied batch
        # needs explicit recovery; replay must not reverse completed items.
        for entry in payload['entries']:
            if exchange.inspect(entry['domain'], entry['local_part'], entry['plan']) != required:
                raise ValidationError('Mailbox batch requires recovery inspection before another switch')
        for entry in payload['entries']:
            exchange.apply(entry['domain'], entry['local_part'], entry['plan'], undo=payload['undo'])
    return {'exchanged': len(payload['entries'])}


def launch(path):
    """Launch only a fresh, privately authorized batch; caller owns account checks."""
    path = _path(path)
    payload = read(path)
    from daemon.snapshot_mail_guard_config import verify
    verify()
    with guard.owned_guards(payload['entries'], payload['restore_id']):
        required = 'applied' if payload['undo'] else 'ready'
        if any(row['state'] != required for row in inspect(path)):
            raise ValidationError('Mailbox batch requires recovery inspection before another switch')
    return supervised_command([sys.executable, '-m', 'daemon.snapshot_mail_journal', str(path)],
                              payload['operation_id'])


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValidationError('A private mailbox journal is required')
        execute(sys.argv[1])
    except Exception:
        # Do not put private journal data or ownership tokens in system logs.
        print('Mailbox switch failed; inspect private recovery state.', file=sys.stderr)
        sys.exit(1)
