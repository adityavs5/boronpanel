"""Optional Sentry-compatible error telemetry with strict data minimization."""
from __future__ import annotations

import re
from typing import Any

from shared.config import settings

_SECRET_KEY = re.compile(r"password|passwd|secret|token|authorization|cookie|session|dsn|api.?key", re.I)


def _scrub(value: Any, key: str = "") -> Any:
    if _SECRET_KEY.search(key):
        return "[Filtered]"
    if isinstance(value, dict):
        return {str(k): _scrub(v, str(k)) for k, v in value.items()
                if str(k).lower() not in {"data", "cookies", "query_string", "env"}}
    if isinstance(value, list):
        return [_scrub(item) for item in value[:50]]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…"
    return value


def before_send(event: dict, hint: dict | None = None) -> dict:
    event = _scrub(event)
    request = event.get("request")
    if isinstance(request, dict):
        # Raw URLs can contain tenant domains/usernames in path segments and
        # credentials in query parameters. The Sentry integration still
        # supplies the HTTP method and templated transaction name, which is
        # enough to group a failure without exporting the requested resource.
        request.pop("url", None)
        request.pop("data", None)
        request.pop("cookies", None)
        request.pop("query_string", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                k: v for k, v in headers.items()
                if k.lower() in {"content-type", "user-agent"}
            }
    event.pop("user", None)
    event.pop("server_name", None)
    return event


def initialize(service: str) -> bool:
    """Enable only when the operator supplied a DSN; no tracing or PII."""
    dsn = settings.sentry_dsn
    if not dsn:
        return False
    import sentry_sdk
    from version import __version__

    sentry_sdk.init(
        dsn=dsn,
        release=f"boron@{__version__}",
        environment=settings.telemetry_environment,
        before_send=before_send,
        send_default_pii=False,
        traces_sample_rate=0.0,
        max_breadcrumbs=25,
    )
    sentry_sdk.set_tag("service", service)
    return True
