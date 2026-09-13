"""Serialize SQL ownership mutations and restore decisions across processes."""
from contextlib import contextmanager
from functools import wraps
import threading

from shared.validation import ValidationError

_local = threading.RLock()
_state = threading.local()


@contextmanager
def mutation_lock():
    if not _local.acquire(blocking=False):
        raise ValidationError('A database backup or management operation is in progress. Try again shortly.')
    try:
        if getattr(_state, 'active', False):
            yield
            return
        from daemon.snapshot_jobs import lock
        try:
            with lock('database-mutations', blocking=False):
                _state.active = True
                try:
                    yield
                finally:
                    _state.active = False
        except BlockingIOError:
            raise ValidationError('A database backup or management operation is in progress. Try again shortly.') from None
    finally:
        _local.release()


def serialized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with mutation_lock():
            return function(*args, **kwargs)
    return wrapped
