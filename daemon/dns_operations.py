"""Coordinate DNS record writes, provider transitions and recovery across processes."""
from daemon.mutation_locks import MutationLock

_LOCK = MutationLock(
    'dns-mutations',
    'A DNS backup, restore or management operation is in progress. Try again shortly.',
)


def mutation_lock(*, blocking=False, timeout=None):
    """Wait for the lock (bounded unless ``blocking``); see daemon.mutation_locks."""
    return _LOCK.hold(blocking=blocking, timeout=timeout)


def serialized(function):
    """Request handlers wait a bounded time, then fail with BusyError."""
    return _LOCK.serialized(function)


def serialized_worker(function):
    """Background jobs wait without holding an API request open."""
    return _LOCK.serialized_worker(function)
