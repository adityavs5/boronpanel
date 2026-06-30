from __future__ import annotations

from shared.db import write_session
from shared.models import AuditLog


MAX_PARAM_VALUE_LEN = 500


def _sanitize(key: str, value):
    if "password" in key.lower() or "secret" in key.lower():
        return "***"
    if isinstance(value, str) and len(value) > MAX_PARAM_VALUE_LEN:
        # File contents (file.read/file.write) and similarly bulky values
        # don't belong verbatim in the audit log -- truncate rather than
        # let one file.write of a 10MB file bloat every audit row forever.
        return f"{value[:MAX_PARAM_VALUE_LEN]}... ({len(value)} chars total)"
    return value


def record(actor: str, role: str, op: str, target: str | None, params: dict, result: str, detail: str = "") -> None:
    redacted = {k: _sanitize(k, v) for k, v in params.items()}
    with write_session() as session:
        session.add(
            AuditLog(
                actor=actor,
                role=role,
                op=op,
                target=target,
                params=redacted,
                result=result,
                detail=detail[:4000] if detail else None,
            )
        )
