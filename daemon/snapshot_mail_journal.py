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
import uuid

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


def recovery_state(path, *, service='dovecot.service'):
    """Observe worker termination before interpreting the directory journal.

    Caller holds the restore/account lock. A completed exchange still needs its
    displaced-mail safety backup and mailbox validation before guards are lifted.
    No status here authorizes deleting either tree or releasing a guard.
    """
    from daemon.snapshot_mail_service import inspect_switch, service_status
    payload = read(path)
    worker = inspect_switch(payload['operation_id'], service=service)
    if worker['state'] == 'running':
        return {'state': 'waiting', 'worker': worker, 'mailboxes': []}
    with guard.owned_guards(payload['entries'], payload['restore_id']):
        mailboxes = inspect(path)
    desired = 'ready' if payload['undo'] else 'applied'
    count = sum(row['state'] == desired for row in mailboxes)
    outcome = 'applied' if count == len(mailboxes) else ('not_applied' if count == 0 else 'partial')
    mail = service_status(service)
    running = (mail.get('LoadState') == 'loaded' and mail.get('ActiveState') == 'active'
               and mail.get('SubState') == 'running' and mail.get('ControlPID') == '0')
    return {'state': outcome if running else 'service_recovery_required',
            'outcome': outcome, 'worker': worker, 'mailboxes': mailboxes}


def prepare_rollback(path, destination, *, service='dovecot.service'):
    """Persist a separate undo journal for exactly the exchanged mailboxes.

    Caller retains its account/restore lock and must separately supervise this
    new journal. The original journal and both mail trees remain untouched.
    """
    payload = read(path)
    if payload['undo']:
        raise ValidationError('An undo journal cannot be reversed automatically')
    state = recovery_state(path, service=service)
    if state['state'] not in ('applied', 'partial'):
        raise ValidationError('Mailbox switch is not ready for rollback preparation')
    changed = {(row['domain'], row['local_part']) for row in state['mailboxes'] if row['state'] == 'applied'}
    entries = [entry for entry in payload['entries'] if (entry['domain'], entry['local_part']) in changed]
    with guard.owned_guards(payload['entries'], payload['restore_id']):
        # A replaced tree or changed state between observation and persistence
        # must never become an automatically executable undo instruction.
        if inspect(path) != state['mailboxes']:
            raise ValidationError('Mailbox switch changed during rollback preparation')
        return create(destination, dict(format=1, restore_id=payload['restore_id'],
                                         operation_id=uuid.uuid4().hex, undo=True, entries=entries))


def continue_rollback(original_path, previous_path, destination, *, service='dovecot.service'):
    """Persist remaining undo work after an interrupted, verified undo worker.

    Caller holds the account lock. Link the prior undo to its original forward
    journal before observing it; never treat another operation as this rollback.
    Neither original nor previous journals are overwritten.
    """
    from daemon.snapshot_mail_service import inspect_switch, service_status
    original, previous = read(original_path), read(previous_path)
    if (original['undo'] or not previous['undo'] or previous['restore_id'] != original['restore_id']
            or previous['operation_id'] == original['operation_id']):
        raise ValidationError('Rollback journals do not describe the same forward restore')
    source = {(entry['domain'], entry['local_part']): entry for entry in original['entries']}
    if any(source.get((entry['domain'], entry['local_part'])) != entry for entry in previous['entries']):
        raise ValidationError('Rollback entries do not match the original restore')
    worker = inspect_switch(previous['operation_id'], service=service)
    if worker['state'] == 'running':
        raise ValidationError('Previous rollback worker is still running')
    mail = service_status(service)
    if mail.get('ActiveState') != 'active' or mail.get('SubState') != 'running' or mail.get('ControlPID') != '0':
        raise ValidationError('Mail service has not resumed after rollback')
    with guard.owned_guards(original['entries'], original['restore_id']):
        states = inspect(original_path)
        entries = [source[row['domain'], row['local_part']] for row in states if row['state'] == 'applied']
        if not entries:
            raise ValidationError('All original mailbox directories are already restored')
        return create(destination, dict(format=1, restore_id=original['restore_id'],
                                         operation_id=uuid.uuid4().hex, undo=True, entries=entries))


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
