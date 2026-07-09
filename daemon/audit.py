from __future__ import annotations

import json
import logging
from pathlib import Path

from shared.db import write_session
from shared.models import AccountEvent, AuditLog, utcnow

logger = logging.getLogger("forgehostd.audit")

MAX_PARAM_VALUE_LEN = 500

# Append-only file mirror of the account lifecycle log. Terminated accounts
# are gone from the panel; the account_events table plus this file are their
# only remaining record, so the file survives even a database loss.
ACCOUNT_EVENTS_LOG = Path("/var/log/forgehost/account-events.log")


# Substrings marking a param that must never be stored verbatim in the audit
# log / shown in the admin Audit UI. Beyond passwords/secrets: bearer API
# tokens, TOTP + recovery codes, and session ids are all live credentials.
# Erring toward redaction is deliberate -- a false positive (e.g. a redirect's
# "status_code") only loses a low-value field, a false negative leaks a secret.
_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "session_id", "code", "private_key", "api_key")


def _sanitize(key: str, value):
    lowered = key.lower()
    if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
        return "***"
    if isinstance(value, str) and len(value) > MAX_PARAM_VALUE_LEN:
        # File contents (file.read/file.write) and similarly bulky values
        # don't belong verbatim in the audit log -- truncate rather than
        # let one file.write of a 10MB file bloat every audit row forever.
        return f"{value[:MAX_PARAM_VALUE_LEN]}... ({len(value)} chars total)"
    return value


def record_account_event(
    action: str,
    username: str,
    actor: str = "system",
    role: str = "system",
    ip: str | None = None,
    detail: str = "",
) -> None:
    """Account lifecycle log entry (created/suspended/unsuspended/terminated).

    Written to the account_events table and mirrored as a JSON line to
    ACCOUNT_EVENTS_LOG. The file write is best-effort: a full disk or missing
    directory must never fail the account operation itself.
    """
    with write_session() as session:
        session.add(
            AccountEvent(
                action=action,
                username=username,
                actor=actor,
                actor_role=role,
                ip=ip,
                detail=detail[:400] if detail else None,
            )
        )
    try:
        ACCOUNT_EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "ts": utcnow().isoformat(),
                "action": action,
                "username": username,
                "actor": actor,
                "role": role,
                "ip": ip,
                "detail": detail or None,
            },
            separators=(",", ":"),
        )
        with ACCOUNT_EVENTS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        logger.warning("could not append to %s: %s", ACCOUNT_EVENTS_LOG, exc)


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
