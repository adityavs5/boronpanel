"""Bounded waits for the server-wide mutation locks.

The database and DNS mutation locks serialize work that cannot be scoped to
one account or zone (ownership/name-reuse checks, provider transitions,
backup capture and restore). Request handlers used to try them exactly once
and fail, so ordinary concurrency -- two customers saving DNS records, an
account creation next to another tenant's database job -- rejected unrelated
work, and multi-step operations such as termination stopped half-applied.

Every acquisition now waits: request handlers for up to
REQUEST_WAIT_SECONDS and queued work inside ``background_wait()`` for up to
BACKGROUND_WAIT_SECONDS, so two holders that nest the locks in opposite
orders still make progress. Dedicated backup/restore workers
(``blocking=True``) keep waiting without limit, as before. A wait that
expires raises BusyError (HTTP 409) with the lock's message.

Request handlers run on the daemon's shared default executor (cpu+4
threads), which also serves sign-in and every unrelated operation. A waiting
request holds one of those threads, so while a backup keeps a lock for
minutes, unbounded numbers of 30 s waiters would stall the whole daemon.
Only REQUEST_WAITER_SLOTS request threads (across all mutation locks) get the
full wait; any further contended request waits OVERFLOW_WAIT_SECONDS --
enough to queue behind a short save, never long enough to pin the pool.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
import threading
import time

from shared.validation import BusyError

REQUEST_WAIT_SECONDS = 30.0
BACKGROUND_WAIT_SECONDS = 900.0
REQUEST_WAITER_SLOTS = 2
OVERFLOW_WAIT_SECONDS = 2.0
_POLL_SECONDS = 0.05
_MAX_POLL_SECONDS = 0.5

_waiting = threading.local()
_waiter_guard = threading.Lock()
_request_waiters = 0


@contextmanager
def background_wait():
    """Let mutation locks taken by this thread wait like a background worker.

    For queued work (control tasks) that calls request handlers directly: the
    caller is not holding an HTTP request open, so a long wait is preferable
    to failing a job the operator already confirmed.
    """
    previous = getattr(_waiting, 'background', False)
    _waiting.background = True
    try:
        yield
    finally:
        _waiting.background = previous


def _wait_limit(blocking: bool, timeout: float | None) -> float | None:
    """Seconds to wait, or None to wait without limit."""
    if timeout is not None:
        return max(0.0, float(timeout))
    if blocking:
        return None
    if getattr(_waiting, 'background', False):
        return BACKGROUND_WAIT_SECONDS
    return REQUEST_WAIT_SECONDS


class _Wait:
    """The deadline of one acquisition, shortened for overflow request waiters.

    A request thread takes one of REQUEST_WAITER_SLOTS only once it actually
    finds a lock busy, and gives it back as soon as it holds the lock (or
    gives up), so uncontended acquisitions never touch the pool.
    """

    def __init__(self, limit: float | None, request: bool):
        self.deadline = None if limit is None else time.monotonic() + limit
        self._request = request and limit is not None
        self._contended = False
        self._slot = False

    def contended(self) -> None:
        global _request_waiters
        if self._contended:
            return
        self._contended = True
        if not self._request:
            return
        with _waiter_guard:
            if _request_waiters < REQUEST_WAITER_SLOTS:
                _request_waiters += 1
                self._slot = True
                return
        self.deadline = min(self.deadline, time.monotonic() + OVERFLOW_WAIT_SECONDS)

    def remaining(self) -> float | None:
        return None if self.deadline is None else max(0.0, self.deadline - time.monotonic())

    def done(self) -> None:
        global _request_waiters
        if self._slot:
            self._slot = False
            with _waiter_guard:
                _request_waiters -= 1


def _acquire_file(name: str, wait: _Wait) -> int | None:
    """Open and flock the shared lock file, polling until the deadline."""
    from daemon.snapshot_jobs import private_directory
    path = private_directory('locks') / name
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    delay = _POLL_SECONDS
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                wait.contended()
                if wait.deadline is None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    return fd
                remaining = wait.remaining()
                if remaining <= 0:
                    os.close(fd)
                    return None
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, _MAX_POLL_SECONDS)
    except BaseException:
        os.close(fd)
        raise


class MutationLock:
    """A re-entrant in-process mutex plus a cross-process flock."""

    def __init__(self, name: str, busy_message: str):
        self.name = name
        self.busy_message = busy_message
        self._local = threading.RLock()
        self._state = threading.local()

    def _acquire_local(self, wait: _Wait) -> bool:
        if self._local.acquire(blocking=False):
            return True
        wait.contended()
        remaining = wait.remaining()
        return self._local.acquire(timeout=-1 if remaining is None else remaining)

    @contextmanager
    def hold(self, *, blocking: bool = False, timeout: float | None = None):
        request = not blocking and not getattr(_waiting, 'background', False)
        wait = _Wait(_wait_limit(blocking, timeout), request)
        try:
            if not self._acquire_local(wait):
                raise BusyError(self.busy_message)
        except BaseException:
            wait.done()
            raise
        try:
            if getattr(self._state, 'active', False):
                wait.done()
                yield
                return
            try:
                fd = _acquire_file(self.name, wait)
            finally:
                wait.done()
            if fd is None:
                raise BusyError(self.busy_message)
            self._state.active = True
            try:
                yield
            except BlockingIOError:
                # A nested non-blocking account/repository lock inside the
                # critical section is the same "busy, retry" condition, not
                # an internal failure (historic behaviour of these helpers).
                raise BusyError(self.busy_message) from None
            finally:
                self._state.active = False
                os.close(fd)
        finally:
            self._local.release()

    def serialized(self, function):
        from functools import wraps

        @wraps(function)
        def wrapped(*args, **kwargs):
            with self.hold():
                return function(*args, **kwargs)
        return wrapped

    def serialized_worker(self, function):
        from functools import wraps

        @wraps(function)
        def wrapped(*args, **kwargs):
            with self.hold(blocking=True):
                return function(*args, **kwargs)
        return wrapped
