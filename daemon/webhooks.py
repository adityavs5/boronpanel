"""Outbound webhook delivery (Phase 7b feature 4).

Async, retried, HMAC-signed POSTs to admin-configured URLs on account
lifecycle events -- httpx is already a project dependency (daemon/
wordpress.py, daemon/powerdns.py), reused here for outbound delivery so
this doesn't introduce a second HTTP client library into the codebase.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import WEBHOOK_EVENT_TYPES, Webhook, WebhookDelivery, utcnow
from shared.validation import validate_webhook_events, validate_webhook_url

logger = logging.getLogger("forgehostd.webhooks")

_executor = ThreadPoolExecutor(max_workers=settings.webhook_concurrency, thread_name_prefix="webhook")

# Delay before retry attempts 2 and 3 -- not used before attempt 1, which
# always fires immediately. Exponential-ish, bounded (goal: "retry 3x with
# exponential backoff").
_BACKOFF_SECONDS = (5, 30)

# Security-audit-2 (High): a webhook payload leaves the trust boundary
# entirely (POSTed to an admin-configured *external* URL) and also persists in
# WebhookDelivery.payload (plaintext in the control-plane DB). Lifecycle-event
# context must therefore never carry a live secret. The `account.created`
# event in particular is emitted with the account's initial plaintext password
# (for the notification-email channel, which legitimately sends it to the
# account owner's own mailbox) -- that key must be stripped before it reaches a
# webhook. Denylist known-sensitive context keys here, at the webhook boundary,
# so this holds for any current or future event without every emit() call site
# needing to remember it.
_SENSITIVE_CONTEXT_KEYS = frozenset(
    {"initial_password", "password", "new_password", "secret", "token", "api_token", "recovery_codes"}
)


class WebhookError(Exception):
    pass


def _webhook_to_dict(w: Webhook, *, include_secret: bool = False) -> dict:
    d = {
        "id": w.id,
        "url": w.url,
        "events": w.events,
        "enabled": w.enabled,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }
    if include_secret:
        d["secret"] = w.secret
    return d


def create_webhook(params: dict) -> dict:
    url = validate_webhook_url(params["url"])
    events_list = validate_webhook_events(params["events"], WEBHOOK_EVENT_TYPES)
    secret = params.get("secret") or secrets.token_hex(32)
    enabled = bool(params.get("enabled", True))
    with write_session() as session:
        row = Webhook(url=url, secret=secret, events=events_list, enabled=enabled)
        session.add(row)
        session.flush()
        result = _webhook_to_dict(row)
    # The secret is only ever returned once, at creation time -- afterward
    # it's used server-side to compute signatures, never re-displayed
    # (same one-time-reveal posture as WordPressJob.admin_password).
    result["secret"] = secret
    return result


def list_webhooks(params: dict | None = None) -> dict:
    with write_session() as session:
        rows = session.scalars(select(Webhook).order_by(Webhook.id)).all()
        return {"webhooks": [_webhook_to_dict(w) for w in rows]}


def get_webhook(params: dict) -> dict:
    webhook_id = int(params["webhook_id"])
    with write_session() as session:
        row = session.get(Webhook, webhook_id)
        if row is None:
            raise WebhookError(f"webhook {webhook_id} not found")
        return _webhook_to_dict(row)


def update_webhook(params: dict) -> dict:
    webhook_id = int(params["webhook_id"])
    with write_session() as session:
        row = session.get(Webhook, webhook_id)
        if row is None:
            raise WebhookError(f"webhook {webhook_id} not found")
        if params.get("url") is not None:
            row.url = validate_webhook_url(params["url"])
        if params.get("events") is not None:
            row.events = validate_webhook_events(params["events"], WEBHOOK_EVENT_TYPES)
        if "enabled" in params:
            row.enabled = bool(params["enabled"])
        session.flush()
        return _webhook_to_dict(row)


def delete_webhook(params: dict) -> dict:
    webhook_id = int(params["webhook_id"])
    with write_session() as session:
        row = session.get(Webhook, webhook_id)
        if row is None:
            raise WebhookError(f"webhook {webhook_id} not found")
        # Real bug found by adversarial review (confirmed with a real,
        # isolated SQLite reproduction, not just reasoned about): shared/
        # db.py enables PRAGMA foreign_keys=ON for every connection, and
        # WebhookDelivery.webhook_id has no ON DELETE CASCADE -- deleting a
        # webhook that has ANY delivery history (which it will, after even
        # one test-button click) raised a raw IntegrityError instead of
        # cleanly deleting. Same "delete children, flush, then delete the
        # parent" pattern daemon/handlers_mail.py's own
        # _delete_mail_domain_cache already established for the identical
        # reason (a plain ForeignKey column with no ORM relationship() has
        # no unit-of-work ordering information to get this right on its own).
        for delivery in session.scalars(select(WebhookDelivery).where(WebhookDelivery.webhook_id == webhook_id)).all():
            session.delete(delivery)
        session.flush()
        session.delete(row)
    return {"id": webhook_id, "status": "deleted"}


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _delivery_to_dict(d: WebhookDelivery) -> dict:
    return {
        "id": d.id,
        "webhook_id": d.webhook_id,
        "event": d.event,
        "payload": d.payload,
        "status": d.status,
        "response_code": d.response_code,
        "attempt_count": d.attempt_count,
        "error": d.error,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "last_attempted_at": d.last_attempted_at.isoformat() if d.last_attempted_at else None,
    }


def list_deliveries(params: dict) -> dict:
    webhook_id = int(params["webhook_id"])
    with write_session() as session:
        if session.get(Webhook, webhook_id) is None:
            raise WebhookError(f"webhook {webhook_id} not found")
        rows = session.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.webhook_id == webhook_id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(int(params.get("limit", 100)))
        ).all()
        return {"deliveries": [_delivery_to_dict(d) for d in rows]}


def _deliver(delivery_id: int) -> None:
    with write_session() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        webhook = session.get(Webhook, delivery.webhook_id)
        url = webhook.url
        secret = webhook.secret
        event = delivery.event
        payload = delivery.payload

    body = json.dumps({"event": event, "data": payload}).encode()
    signature = _sign(secret, body)

    for attempt in range(1, settings.webhook_max_attempts + 1):
        with write_session() as session:
            delivery = session.get(WebhookDelivery, delivery_id)
            delivery.attempt_count = attempt
            delivery.last_attempted_at = utcnow()

        try:
            resp = httpx.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Forgehost-Signature": signature,
                    "X-Forgehost-Event": event,
                },
                timeout=settings.webhook_delivery_timeout_seconds,
            )
            with write_session() as session:
                delivery = session.get(WebhookDelivery, delivery_id)
                delivery.response_code = resp.status_code
                if 200 <= resp.status_code < 300:
                    delivery.status = "success"
                    delivery.error = None
                    return
                delivery.error = f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            with write_session() as session:
                delivery = session.get(WebhookDelivery, delivery_id)
                delivery.error = str(exc)

        if attempt < settings.webhook_max_attempts:
            time.sleep(_BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)])

    with write_session() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        delivery.status = "failed"


def _trigger(webhook: Webhook, event: str, payload: dict) -> int:
    with write_session() as session:
        delivery = WebhookDelivery(webhook_id=webhook.id, event=event, payload=payload, status="pending")
        session.add(delivery)
        session.flush()
        delivery_id = delivery.id
    _executor.submit(_deliver, delivery_id)
    return delivery_id


def maybe_trigger(event_type: str, account, **context) -> list[int]:
    """Called by daemon/events.py's emit() for every lifecycle event --
    silently returns [] for an event type no webhook can even subscribe to
    (e.g. "account.unsuspended", "login.new", which the goal's own webhook
    event list omits) rather than requiring every caller to know which
    events are webhook-eligible."""
    if event_type not in WEBHOOK_EVENT_TYPES:
        return []
    payload = {k: v for k, v in context.items() if k not in _SENSITIVE_CONTEXT_KEYS}
    if account is not None:
        payload["username"] = account.username
    with write_session() as session:
        hooks = session.scalars(select(Webhook).where(Webhook.enabled == True)).all()  # noqa: E712
        matching = [h for h in hooks if event_type in h.events]
    return [_trigger(h, event_type, payload) for h in matching]


def test_webhook(params: dict) -> dict:
    webhook_id = int(params["webhook_id"])
    with write_session() as session:
        webhook = session.get(Webhook, webhook_id)
        if webhook is None:
            raise WebhookError(f"webhook {webhook_id} not found")
    delivery_id = _trigger(webhook, "test", {"message": "This is a test delivery from Forgehost."})
    return {"delivery_id": delivery_id, "status": "queued"}
