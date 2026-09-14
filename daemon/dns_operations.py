"""Coordinate DNS record writes, provider transitions and recovery across processes."""
from contextlib import contextmanager
from functools import wraps
import threading

from shared.validation import ValidationError

_local = threading.RLock()
_state = threading.local()


@contextmanager
def mutation_lock(*, blocking=False):
    if not _local.acquire(blocking=blocking):
        raise ValidationError('A DNS backup, restore or management operation is in progress. Try again shortly.')
    try:
        if getattr(_state, 'active', False):
            yield
            return
        from daemon.snapshot_jobs import lock
        try:
            with lock('dns-mutations', blocking=blocking):
                _state.active = True
                try:
                    yield
                finally:
                    _state.active = False
        except BlockingIOError:
            raise ValidationError('A DNS backup, restore or management operation is in progress. Try again shortly.') from None
    finally:
        _local.release()


def serialized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with mutation_lock():
            return function(*args, **kwargs)
    return wrapped


def serialized_worker(function):
    """Background jobs wait without holding an API request open."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with mutation_lock(blocking=True):
            return function(*args, **kwargs)
    return wrapped
