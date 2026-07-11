"""Outbound webhook delivery (Phase 7b feature 4).

Async, retried, HMAC-signed POSTs to admin-configured URLs on account
lifecycle events -- httpx is already a project dependency (daemon/
wordpress.py, daemon/powerdns.py), reused here for outbound delivery so
this doesn't introduce a second HTTP client library into the codebase.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import WEBHOOK_EVENT_TYPES, Webhook, WebhookDelivery, utcnow
from shared.validation import validate_webhook_events, validate_webhook_url

logger = logging.getLogger("borond.webhooks")

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


def _ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    # Covers loopback (127/8, ::1), private (10/8, 172.16/12, 192.168/16, fc00::/7),
    # link-local INCLUDING the 169.254.169.254 cloud-metadata endpoint
    # (169.254/16, fe80::/10), reserved, multicast, and 0.0.0.0/::.
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _assert_public_destination(url: str) -> str:
    """Security-audit-2 (Medium) SSRF guard: webhook delivery is an outbound
    HTTP POST made by borond (root), to an admin-configured URL. Without
    this, an admin (or an over-scoped admin token) could point a webhook at an
    internal-only service (127.0.0.1:8081 PowerDNS, the panel, other
    loopback services) or the cloud metadata endpoint (169.254.169.254) and
    have the root daemon reach it.

    Checked here at *delivery* time (not only at create/validate time) so that
    a hostname which resolved to a public IP when the webhook was created but
    later resolves to an internal one (DNS rebinding) is still refused. httpx's
    `post` does not follow redirects by default, so a 3xx to an internal target
    cannot bypass this either.

    Returns one validated public IP for the caller to PIN the connection to
    (see _pinned_post): every resolved address is checked, and the address that
    was checked is the exact one the request then connects to -- closing the
    check-then-reconnect (DNS-rebinding) TOCTOU where httpx would otherwise
    re-resolve the hostname independently a moment after this check passed."""
    host = urlparse(url).hostname
    if not host:
        raise WebhookError(f"webhook URL has no host: {url!r}")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise WebhookError(f"could not resolve webhook host '{host}': {exc}") from exc
    pinned_ip: str | None = None
    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if _ip_is_blocked(ip):
            raise WebhookError(
                f"refusing to deliver webhook to non-public address {ip} (host '{host}') -- "
                "internal/loopback/link-local/metadata endpoints are blocked (SSRF protection)"
            )
        if pinned_ip is None:
            pinned_ip = addr
    if pinned_ip is None:
        raise WebhookError(f"could not resolve webhook host '{host}'")
    return pinned_ip


def _pinned_post(url: str, body: bytes, headers: dict, timeout: float) -> httpx.Response:
    """POST to `url` but connect to a freshly-resolved, validated-public IP,
    pinning the TLS server hostname (SNI + certificate verification) and the
    Host header to the original hostname. Resolving/validating and connecting
    as one step -- rather than validating a hostname and letting httpx re-resolve
    it moments later -- is what actually closes the DNS-rebinding TOCTOU: the
    address vetted by _assert_public_destination IS the address connected to."""
    parsed = urlparse(url)
    host = parsed.hostname
    pinned_ip = _assert_public_destination(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    netloc_ip = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    pinned_url = parsed._replace(netloc=f"{netloc_ip}:{port}").geturl()
    req_headers = {**headers, "Host": parsed.netloc}
    extensions = {"sni_hostname": host} if parsed.scheme == "https" else {}
    with httpx.Client(timeout=timeout) as client:
        request = client.build_request("POST", pinned_url, content=body, headers=req_headers, extensions=extensions)
        return client.send(request)


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

    # SSRF guard (security-audit-2): resolve + block internal targets before
    # the root daemon makes any outbound request. A blocked destination is a
    # terminal failure (not retried -- the address will not become public on a
    # retry) recorded on the delivery for operator visibility.
    try:
        _assert_public_destination(url)
    except WebhookError as exc:
        with write_session() as session:
            delivery = session.get(WebhookDelivery, delivery_id)
            delivery.status = "failed"
            delivery.error = str(exc)
            delivery.attempt_count = 1
            delivery.last_attempted_at = utcnow()
        logger.warning("webhook delivery %s blocked by SSRF guard: %s", delivery_id, exc)
        return

    for attempt in range(1, settings.webhook_max_attempts + 1):
        with write_session() as session:
            delivery = session.get(WebhookDelivery, delivery_id)
            delivery.attempt_count = attempt
            delivery.last_attempted_at = utcnow()

        try:
            # Re-validate + pin on every attempt (not just once before the
            # loop): a hostname that rebinds to an internal address between
            # retries is refused here too, and the vetted IP is the one hit.
            resp = _pinned_post(
                url,
                body,
                {
                    "Content-Type": "application/json",
                    "X-Boron-Signature": signature,
                    "X-Boron-Event": event,
                },
                settings.webhook_delivery_timeout_seconds,
            )
            with write_session() as session:
                delivery = session.get(WebhookDelivery, delivery_id)
                delivery.response_code = resp.status_code
                if 200 <= resp.status_code < 300:
                    delivery.status = "success"
                    delivery.error = None
                    return
                delivery.error = f"HTTP {resp.status_code}"
        except (httpx.HTTPError, WebhookError) as exc:
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
    delivery_id = _trigger(webhook, "test", {"message": "This is a test delivery from Boron."})
    return {"delivery_id": delivery_id, "status": "queued"}
