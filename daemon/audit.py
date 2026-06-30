from __future__ import annotations

from shared.db import write_session
from shared.models import AuditLog


def record(actor: str, role: str, op: str, target: str | None, params: dict, result: str, detail: str = "") -> None:
    redacted = {k: ("***" if "password" in k.lower() or "secret" in k.lower() else v) for k, v in params.items()}
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
