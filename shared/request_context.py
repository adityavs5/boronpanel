"""Correlation context shared by the API, daemon and background jobs.

Only opaque identifiers live here.  Request IDs are deliberately bounded and
validated before an upstream value is accepted; error references are always
generated locally and contain no user data.
"""
from __future__ import annotations

import contextvars
import re
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from contextlib import contextmanager
from typing import Iterator

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "boron_request_id", default=None
)
_error_reference: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "boron_error_reference", default=None
)


def new_request_id() -> str:
    return uuid.uuid4().hex


def new_error_reference() -> str:
    return secrets.token_hex(8)


def normalize_request_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _SAFE_REQUEST_ID.fullmatch(value) else None


def current_request_id() -> str | None:
    return _request_id.get()


def current_error_reference() -> str | None:
    return _error_reference.get()


def set_request_id(value: str | None):
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def set_error_reference(value: str | None):
    return _error_reference.set(value)


def reset_error_reference(token) -> None:
    _error_reference.reset(token)


def ensure_error_reference(preferred: object = None) -> str:
    value = preferred if isinstance(preferred, str) and re.fullmatch(r"[a-f0-9]{16}", preferred) else None
    value = value or current_error_reference() or new_error_reference()
    _error_reference.set(value)
    return value


def correlation_id_for_new_work() -> str:
    """Keep a request's ID, or mint one for scheduler/recovery-created work."""
    return current_request_id() or new_request_id()


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    """Thread pool that carries correlation ContextVars into each submitted job."""

    def submit(self, fn, /, *args, **kwargs):
        context = copy_context()
        return super().submit(context.run, fn, *args, **kwargs)


@contextmanager
def request_context(request_id: object = None, error_reference: object = None) -> Iterator[str]:
    rid = normalize_request_id(request_id) or new_request_id()
    rid_token = set_request_id(rid)
    error_token = set_error_reference(
        error_reference if isinstance(error_reference, str) else None
    )
    try:
        yield rid
    finally:
        reset_error_reference(error_token)
        reset_request_id(rid_token)
