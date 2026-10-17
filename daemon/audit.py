from __future__ import annotations

import contextvars
import datetime as dt
import hashlib
import ipaddress
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, select

from shared.config import settings
from shared.db import write_session
from shared.models import AccountEvent, ActionStep, AuditLog, utcnow
from shared.request_context import current_request_id
from shared.audit_redaction import sanitize_output, secret_values

logger = logging.getLogger("borond.audit")

MAX_PARAM_VALUE_LEN = 500
MAX_STEP_OUTPUT_BYTES = 4096
MIN_AUDIT_RETENTION_DAYS = 90
MIN_STEP_RETENTION_DAYS = 30
DEFAULT_PRUNE_BATCH_SIZE = 100

# Append-only file mirror of the account lifecycle log. Terminated accounts
# are gone from the panel; the account_events table plus this file are their
# only remaining record, so the file survives even a database loss.
ACCOUNT_EVENTS_LOG = Path("/var/log/boron/account-events.log")


# Substrings marking a param that must never be stored verbatim in the audit
# log / shown in the admin Audit UI. Beyond passwords/secrets: bearer API
# tokens, TOTP + recovery codes, and session ids are all live credentials.
# Erring toward redaction is deliberate -- a false positive (e.g. a redirect's
# "status_code") only loses a low-value field, a false negative leaks a secret.
_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "session_id", "code", "private_key", "api_key", "license", "recovery", "passphrase", "access_key", "credential")
_PRIVATE_PAYLOAD_KEYS = {
    "env", "env_vars", "environment", "content", "contents", "body",
    "content_base64", "headers", "authorization", "credential", "credentials",
}

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|authorization|api[_-]?key|private[_-]?key|session[_-]?id|license[_-]?key|recovery[_-]?key|ssh[_-]?key[_-]?passphrase|passphrase|access[_-]?key)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_PEM_RE = re.compile(
    r"-----BEGIN [^-\r\n]+-----.*?-----END [^-\r\n]+-----", re.DOTALL
)
_MAIL_HEADER_RE = re.compile(r"(?im)^(?:from|to|cc|bcc|subject|message-id|mime-version|content-type):")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_EMAIL_RE = re.compile(r"(?i)(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}")


@dataclass
class _StepData:
    name: str
    started_at: dt.datetime
    duration_ms: int
    result: str
    exit_code: int | None = None
    stdout_tail: str | None = None
    stderr_tail: str | None = None


@dataclass
class ActionTrace:
    op: str
    secret_values: set[str] = field(default_factory=set)
    changes: dict[str, dict] = field(default_factory=dict)
    steps: list[_StepData] = field(default_factory=list)


@dataclass
class StepCapture:
    stdout: object = None
    stderr: object = None
    exit_code: int | None = None

    def capture(self, result) -> object:
        """Capture a command-like result without changing the caller's value."""
        self.stdout = getattr(result, "stdout", None)
        self.stderr = getattr(result, "stderr", None)
        code = getattr(result, "returncode", getattr(result, "code", None))
        self.exit_code = code if isinstance(code, int) and not isinstance(code, bool) else None
        return result


_ACTION_TRACE: contextvars.ContextVar[ActionTrace | None] = contextvars.ContextVar(
    "boron_action_trace", default=None
)


def _collect_secret_values(key: str, value, output: set[str]) -> None:
    lowered = key.lower()
    private = lowered in _PRIVATE_PAYLOAD_KEYS or any(part in lowered for part in _SENSITIVE_KEY_PARTS)
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            _collect_secret_values(key if private else str(child_key), child_value, output)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_secret_values(key, item, output)
        return
    if private and isinstance(value, str) and value:
        output.add(value)


@contextmanager
def action_trace(op: str, params: dict | None = None, *, enabled: bool = True):
    """Share one mutable action trace with copied worker-thread contexts."""
    if not enabled:
        yield None
        return
    secrets: set[str] = set()
    for key, value in (params or {}).items():
        _collect_secret_values(str(key), value, secrets)
    trace = ActionTrace(op=str(op)[:64], secret_values=secrets)
    token = _ACTION_TRACE.set(trace)
    try:
        yield trace
    finally:
        _ACTION_TRACE.reset(token)


def begin_action_trace(op: str, params: dict | None = None, *, enabled: bool = True):
    """Low-level pair for dispatch(), which has several explicit exit paths."""
    if not enabled:
        return None, None
    secrets: set[str] = set()
    for key, value in (params or {}).items():
        _collect_secret_values(str(key), value, secrets)
    trace = ActionTrace(op=str(op)[:64], secret_values=secrets)
    return trace, _ACTION_TRACE.set(trace)


def end_action_trace(token) -> None:
    if token is not None:
        _ACTION_TRACE.reset(token)


def current_trace() -> ActionTrace | None:
    return _ACTION_TRACE.get()


def _hash_value(value) -> dict | None:
    if value is None:
        return None
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return {"hash": "sha256:" + hashlib.sha256(canonical).hexdigest()}


def change(field_name: str, before, after, *, sensitive: bool = False) -> None:
    """Record one redacted before/after field on the active action trace."""
    trace = current_trace()
    if trace is None:
        return
    name = str(field_name)[:128]
    private = sensitive or name.lower() in _PRIVATE_PAYLOAD_KEYS or any(
        part in name.lower() for part in _SENSITIVE_KEY_PARTS
    )
    if private:
        trace.changes[name] = {"before": _hash_value(before), "after": _hash_value(after)}
    else:
        trace.changes[name] = {
            "before": _sanitize(name, before),
            "after": _sanitize(name, after),
        }


def _bounded_tail(value: object, secrets: set[str]) -> str | None:
    if value is None:
        return None
    text = sanitize_output(value, {})
    # Mail bodies are customer content, not diagnostic output. If a captured
    # stream has message headers, omit the stream wholesale rather than trying
    # to parse every MIME/content-transfer variant safely.
    if len(_MAIL_HEADER_RE.findall(text)) >= 2:
        return "[REDACTED MAIL CONTENT]"
    text = _PEM_RE.sub("[REDACTED PRIVATE KEY]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda m: m.group(1) + m.group(2) + "***", text)
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            text = text.replace(secret, "***")
    encoded = text.encode("utf-8", "replace")
    if len(encoded) > MAX_STEP_OUTPUT_BYTES:
        encoded = encoded[-MAX_STEP_OUTPUT_BYTES:]
        text = encoded.decode("utf-8", "ignore")
    return text or None


def redact_diagnostic_text(value: object, *, max_bytes: int = 64 * 1024,
                           secret_values: set[str] | None = None) -> str:
    """Redact arbitrary diagnostic text before it enters an export.

    Unlike step tails, this helper accepts a caller-selected bound for log
    excerpts. It deliberately removes email addresses as well as credentials:
    a support archive is operational metadata, never a copy of mail content.
    """
    text = sanitize_output(value or "", {})
    if len(_MAIL_HEADER_RE.findall(text)) >= 2:
        return "[REDACTED MAIL CONTENT]"
    text = _PEM_RE.sub("[REDACTED PRIVATE KEY]", text)
    text = _BEARER_RE.sub("Bearer ***", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: match.group(1) + match.group(2) + "***", text)
    text = _EMAIL_RE.sub("[REDACTED EMAIL]", text)
    for secret in sorted(secret_values or set(), key=len, reverse=True):
        if secret:
            text = text.replace(secret, "***")
    limit = max(1, min(int(max_bytes), 1024 * 1024))
    encoded = text.encode("utf-8", "replace")
    if len(encoded) > limit:
        text = encoded[-limit:].decode("utf-8", "ignore")
    return text


@contextmanager
def step(name: str):
    """Record a bounded step on the active action, without swallowing errors."""
    trace = current_trace()
    capture = StepCapture()
    if trace is None:
        yield capture
        return
    safe_name = _bounded_tail(name, trace.secret_values) or "action step"
    safe_name = safe_name[:160]
    started_at = utcnow()
    started = time.monotonic()
    result = "ok"
    try:
        yield capture
    except BaseException:
        result = "failed"
        raise
    finally:
        if capture.exit_code not in (None, 0):
            result = "failed"
        trace.steps.append(
            _StepData(
                name=safe_name,
                started_at=started_at,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                result=result,
                exit_code=capture.exit_code,
                stdout_tail=_bounded_tail(capture.stdout, trace.secret_values),
                stderr_tail=_bounded_tail(capture.stderr, trace.secret_values),
            )
        )


from shared.audit_redaction import sanitize as _sanitize


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
        fd = os.open(ACCOUNT_EVENTS_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o640)
        try:
            os.fchmod(fd, 0o640)
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)
    except OSError as exc:
        logger.warning("could not append to %s: %s", ACCOUNT_EVENTS_LOG, exc)


def record(actor: str, role: str, op: str, target: str | None, params: dict,
           result: str, detail: str = "", request_id: str | None = None,
           source_ip: str | None = None, duration_ms: int | None = None,
           changes: dict | None = None, trace: ActionTrace | None = None) -> int:
    redacted = {k: _sanitize(k, v) for k, v in params.items()}
    safe_target = str(target)[:128] if target is not None else None
    active_trace = trace or current_trace()
    if changes is None and active_trace is not None:
        # Only change() can populate this structure; sensitive values are
        # already represented by hash wrappers. Running the generic sanitizer
        # again would turn an `api_token` field's hash into the string "***".
        safe_changes = active_trace.changes or None
    else:
        # Defensive path for generic/direct record() callers.
        safe_changes = _sanitize("changes", changes) if changes else None
    try:
        safe_ip = str(ipaddress.ip_address(source_ip)) if source_ip else None
    except ValueError:
        safe_ip = None
    safe_duration = None if duration_ms is None else max(0, min(int(duration_ms), 86_400_000))
    with write_session() as session:
        row = AuditLog(
            actor=actor,
            role=role,
            op=op,
            target=safe_target,
            params=redacted,
            result=result,
            detail=detail[:4000] if detail else None,
            request_id=request_id or current_request_id(),
            source_ip=safe_ip,
            duration_ms=safe_duration,
            changes=safe_changes or None,
        )
        session.add(row)
        session.flush()
        if active_trace is not None:
            for item in active_trace.steps:
                session.add(ActionStep(
                    audit_id=row.id,
                    name=item.name,
                    started_at=item.started_at,
                    duration_ms=item.duration_ms,
                    result=item.result,
                    exit_code=item.exit_code,
                    stdout_tail=_bounded_tail(item.stdout_tail, active_trace.secret_values),
                    stderr_tail=_bounded_tail(item.stderr_tail, active_trace.secret_values),
                ))
        ident = row.id
    return ident


def prune_retention(*, now: dt.datetime | None = None, batch_size: int = DEFAULT_PRUNE_BATCH_SIZE,
                    max_batches: int = 1000) -> dict:
    """Prune old step detail and audit rows in short, independently committed batches."""
    current = now or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    audit_days = max(MIN_AUDIT_RETENTION_DAYS, int(settings.audit_retention_days))
    step_days = max(MIN_STEP_RETENTION_DAYS, int(settings.audit_step_retention_days))
    size = max(1, min(int(batch_size), 250))
    budget = max(1, min(int(max_batches), 10_000))
    removed = {"action_steps": 0, "audit_rows": 0}

    def prune_model(model, timestamp, cutoff, key):
        batches = 0
        while batches < budget:
            with write_session() as session:
                ids = session.scalars(
                    select(model.id).where(timestamp < cutoff).order_by(model.id).limit(size)
                ).all()
                if ids:
                    session.execute(delete(model).where(model.id.in_(ids)))
            if not ids:
                break
            removed[key] += len(ids)
            batches += 1

    prune_model(ActionStep, ActionStep.started_at, current - dt.timedelta(days=step_days), "action_steps")
    prune_model(AuditLog, AuditLog.created_at, current - dt.timedelta(days=audit_days), "audit_rows")
    return {
        **removed,
        "audit_retention_days": audit_days,
        "step_retention_days": step_days,
        "batch_size": size,
    }


def redact_historical_batch(after_id=0, limit=100):
    """Remove previously stored credentials without deleting audit events."""
    with write_session() as db:
        rows = db.scalars(select(AuditLog).where(AuditLog.id > after_id).order_by(AuditLog.id).limit(limit)).all()
        for row in rows:
            values = set()
            for key, value in (row.params or {}).items():
                _collect_secret_values(str(key), value, values)
            values.update(secret_values(row.changes))
            row.params = _sanitize("", row.params)
            row.changes = _sanitize("", row.changes)
            row.detail = redact_diagnostic_text(row.detail, secret_values=values) if row.detail else row.detail
            for item in db.scalars(select(ActionStep).where(ActionStep.audit_id == row.id)).all():
                item.stdout_tail = _bounded_tail(item.stdout_tail, values)
                item.stderr_tail = _bounded_tail(item.stderr_tail, values)
        return rows[-1].id if rows else None
