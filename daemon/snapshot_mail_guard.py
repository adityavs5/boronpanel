"""Durable ownership of Dovecot restore guards; not a mailbox mutation lock.

A guard blocks new userdb lookups. The coordinator must separately drain earlier
lookups, authenticated sessions and accepted recipients before changing files.
Guards deliberately survive worker failure and are released only after recovery
has established that the mailbox is usable.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

from shared.config import settings
from shared.validation import ValidationError, validate_domain, validate_mailbox_local_part


def marker_name(domain, local_part):
    address = validate_mailbox_local_part(local_part) + '@' + validate_domain(domain)
    return hashlib.sha256(address.lower().encode('ascii')).hexdigest()


@contextmanager
def _directory():
    path = Path(settings.mail_restore_guard_dir)
    if os.geteuid() != 0 or not path.is_absolute() or path.resolve() != path or path == Path('/'):
        raise ValidationError('Mail restore guards require private service-owned storage')
    # The panel data directory must already exist. Persist creation of its
    # guard subdirectory as well as the later marker entries.
    path.mkdir(exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or info.st_mode & 0o077:
            raise ValidationError('Mail restore guard directory must be private and root-owned')
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
            os.fsync(parent)
        finally:
            os.close(parent)
        # Serialize release/acquire so a duplicate release cannot unlink the
        # next job's marker after reading an older inode.
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


def block(domain, local_part, restore_id):
    if type(restore_id) is not int or restore_id <= 0:
        raise ValidationError('Invalid mail restore job')
    name = marker_name(domain, local_part)
    token = secrets.token_hex(32)
    with _directory() as directory:
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
        except FileExistsError:
            raise ValidationError('Mailbox is already protected by a restore job') from None
        # An interrupted write leaves a blocking marker for explicit recovery.
        with os.fdopen(fd, 'w') as handle:
            json.dump({'format': 1, 'restore_id': restore_id, 'token': token}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.fsync(directory)
    return token


def release(domain, local_part, restore_id, token):
    """Only call when the coordinator has established a usable mailbox state."""
    if type(restore_id) is not int or restore_id <= 0 or not isinstance(token, str):
        raise ValidationError('Invalid mail restore guard ownership')
    name = marker_name(domain, local_part)
    with _directory() as directory:
        _verify(directory, name, restore_id, token)
        os.unlink(name, dir_fd=directory)
        os.fsync(directory)


def _verify(directory, name, restore_id, token):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'r') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 512:
            raise ValidationError('Mail restore guard requires recovery inspection')
        try:
            payload = json.loads(handle.read(513))
            valid = (payload['format'] == 1 and payload['restore_id'] == restore_id
                     and isinstance(payload['token'], str)
                     and secrets.compare_digest(payload['token'], token))
        except (ValueError, TypeError, KeyError):
            valid = False
        if not valid:
            raise ValidationError('Mail restore guard belongs to another job or requires recovery')


@contextmanager
def owned_guards(entries, restore_id):
    """Hold guard ownership stable while an already quiesced batch is exchanged."""
    with _directory() as directory:
        for entry in entries:
            _verify(directory, marker_name(entry['domain'], entry['local_part']),
                    restore_id, entry['token'])
        yield
