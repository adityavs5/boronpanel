"""Atomic Maildir replacement with inode-based interrupted-operation inspection.

Caller obligations: authorize the mailbox, persist the plan before applying it,
and quiesce Dovecot for every plan/apply/undo sequence. This primitive does not
stop services, create safety snapshots, release guards or delete either tree.
"""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import re
import stat

from shared.config import settings
from shared.validation import ValidationError, validate_domain, validate_mailbox_local_part

FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def _home(domain, local_part):
    domain = validate_domain(domain)
    local_part = validate_mailbox_local_part(local_part)
    base = Path(settings.mail_base)
    if not base.is_absolute() or '..' in base.parts or base == Path('/'):
        raise ValidationError('Invalid mail storage root')
    fd = os.open('/', FLAGS)
    try:
        # Resolve each directory through an already opened parent. Ancestor
        # symlinks cannot redirect privileged mailbox operations.
        for name in (*base.parts[1:], domain, local_part):
            child = os.open(name, FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _prepared_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'\.boron-mail-ready-[a-f0-9]{32}', value):
        raise ValidationError('Invalid prepared mailbox identifier')
    return value


def _identity(directory, name):
    child = os.open(name, FLAGS, dir_fd=directory)
    try:
        info = os.fstat(child)
        if not stat.S_ISDIR(info.st_mode):
            raise ValidationError('Maildir exchange requires directories')
        for part in ('cur', 'new', 'tmp'):
            nested = os.open(part, FLAGS, dir_fd=child)
            os.close(nested)
        return [info.st_dev, info.st_ino]
    finally:
        os.close(child)


def _describe(directory, prepared):
    parent = os.fstat(directory)
    return {'format': 1, 'prepared': prepared,
            'home': [parent.st_dev, parent.st_ino],
            'current': _identity(directory, 'Maildir'),
            'replacement': _identity(directory, prepared)}


def plan(domain, local_part, prepared):
    """Return private journal data; caller must fsync it before applying."""
    prepared = _prepared_name(prepared)
    with _home(domain, local_part) as directory:
        return _describe(directory, prepared)


def _state(directory, saved):
    if not isinstance(saved, dict) or saved.get('format') != 1:
        raise ValidationError('Invalid mailbox exchange journal')
    actual = _describe(directory, _prepared_name(saved.get('prepared')))
    if actual == saved:
        return 'ready'
    reversed_plan = dict(saved, current=saved.get('replacement'), replacement=saved.get('current'))
    if actual == reversed_plan:
        return 'applied'
    raise ValidationError('Mailbox directories no longer match the recovery journal')


def inspect(domain, local_part, saved):
    """Recognize a completed exchange even if the worker died before reporting it."""
    with _home(domain, local_part) as directory:
        return _state(directory, saved)


def _exchange(directory, prepared):
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    # Linux RENAME_EXCHANGE: both directory entries always exist, including
    # during a crash. Never fall back to two non-atomic rename operations.
    if rename(directory, b'Maildir', directory, prepared.encode('ascii'), 2) != 0:
        error = ctypes.get_errno()
        raise OSError(error, 'Atomic Maildir exchange failed')
    os.fsync(directory)


def apply(domain, local_part, saved, *, undo=False):
    """Exchange the planned directories once, retaining the displaced Maildir.

    The coordinator must inspect state after any exception, including fsync
    failure, rather than blindly replaying the exchange.
    """
    with _home(domain, local_part) as directory:
        state = _state(directory, saved)
        expected = 'applied' if undo else 'ready'
        if state != expected:
            raise ValidationError('Mailbox exchange has already been applied or undone')
        _exchange(directory, saved['prepared'])
        return _state(directory, saved)
