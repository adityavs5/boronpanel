import hashlib
import hmac
import json
import time

import httpx
import pytest
from sqlalchemy import select

from daemon import webhooks as wh
from shared.db import write_session
from shared.models import Account, Webhook, WebhookDelivery


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Retry backoff (5s/30s) would make every retry test glacially slow --
    the retry *logic* (attempt count, status transitions) is what's under
    test, not real wall-clock timing."""
    monkeypatch.setattr(wh.time, "sleep", lambda seconds: None)


def _account(username="demo1"):
    with write_session() as session:
        account = Account(username=username, status="active")
        session.add(account)
        session.flush()
        return session.get(Account, account.id)


# --- CRUD --------------------------------------------------------------


def test_create_webhook_validates_url(isolated_db):
    with pytest.raises(Exception):
        wh.create_webhook({"url": "not-a-url", "events": ["account.created"]})


def test_create_webhook_validates_events(isolated_db):
    with pytest.raises(Exception):
        wh.create_webhook({"url": "https://example.com/hook", "events": ["not.a.real.event"]})


def test_create_webhook_generates_secret_if_not_given(isolated_db):
    result = wh.create_webhook({"url": "https://example.com/hook", "events": ["account.created"]})
    assert result["secret"]
    assert len(result["secret"]) >= 32


def test_create_webhook_uses_given_secret(isolated_db):
    result = wh.create_webhook({"url": "https://example.com/hook", "events": ["account.created"], "secret": "my-secret"})
    assert result["secret"] == "my-secret"


def test_list_webhooks_never_exposes_secret(isolated_db):
    wh.create_webhook({"url": "https://example.com/hook", "events": ["account.created"], "secret": "hidden"})
    listed = wh.list_webhooks()["webhooks"]
    assert "secret" not in listed[0]


def test_update_webhook_partial_fields(isolated_db):
    created = wh.create_webhook({"url": "https://example.com/hook", "events": ["account.created"]})
    updated = wh.update_webhook({"webhook_id": created["id"], "enabled": False})
    assert updated["enabled"] is False
    assert updated["url"] == "https://example.com/hook"  # untouched


def test_delete_webhook(isolated_db):
    created = wh.create_webhook({"url": "https://example.com/hook", "events": ["account.created"]})
    wh.delete_webhook({"webhook_id": created["id"]})
    with pytest.raises(wh.WebhookError):
        wh.get_webhook({"webhook_id": created["id"]})


def test_delete_webhook_with_delivery_history_does_not_raise(isolated_db):
    """Regression test for a real bug found by adversarial review,
    confirmed live against an isolated SQLite DB: shared/db.py enables
    PRAGMA foreign_keys=ON, and WebhookDelivery.webhook_id has no ON
    DELETE CASCADE -- deleting a webhook with any delivery history (which
    it will have after even one test-button click) used to raise a raw
    sqlite3.IntegrityError instead of cleanly deleting."""
    created = wh.create_webhook({"url": "https://example.com/hook", "events": ["account.created"]})
    with write_session() as session:
        webhook = session.get(Webhook, created["id"])
        session.add(WebhookDelivery(webhook_id=webhook.id, event="test", payload={}, status="success"))
        session.add(WebhookDelivery(webhook_id=webhook.id, event="account.created", payload={}, status="failed"))

    wh.delete_webhook({"webhook_id": created["id"]})  # must not raise

    with write_session() as session:
        assert session.get(Webhook, created["id"]) is None
        remaining = session.scalars(select(WebhookDelivery).where(WebhookDelivery.webhook_id == created["id"])).all()
        assert remaining == []


def test_get_delete_update_missing_webhook_raises(isolated_db):
    with pytest.raises(wh.WebhookError):
        wh.get_webhook({"webhook_id": 999999})
    with pytest.raises(wh.WebhookError):
        wh.update_webhook({"webhook_id": 999999, "enabled": False})
    with pytest.raises(wh.WebhookError):
        wh.delete_webhook({"webhook_id": 999999})


# --- signing --------------------------------------------------------------


def test_sign_matches_manual_hmac():
    body = b'{"event": "account.created", "data": {}}'
    sig = wh._sign("s3cret", body)
    expected = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert sig == expected


# --- delivery (mocked httpx) -----------------------------------------------


def _poll_delivery(delivery_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with write_session() as session:
            d = session.get(WebhookDelivery, delivery_id)
            if d.status != "pending":
                return d.status, d.attempt_count, d.response_code, d.error
        time.sleep(0.02)
    pytest.fail("delivery did not settle in time")


def test_deliver_success_on_first_attempt(isolated_db, monkeypatch):
    created = wh.create_webhook({"url": "https://example.com/hook", "events": ["account.created"], "secret": "s3cret"})
    calls = []

    def fake_post(url, content=None, headers=None, timeout=None):
        calls.append((url, content, headers))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(wh.httpx, "post", fake_post)
    with write_session() as session:
        webhook = session.get(Webhook, created["id"])
        delivery = WebhookDelivery(webhook_id=webhook.id, event="account.created", payload={"username": "demo1"}, status="pending")
        session.add(delivery)
        session.flush()
        delivery_id = delivery.id

    wh._deliver(delivery_id)  # synchronous call, not through the executor -- deterministic for this test
    status, attempts, code, error = _poll_delivery(delivery_id)
    assert status == "success"
    assert attempts == 1
    assert code == 200
    assert error is None
    assert len(calls) == 1
    signature = calls[0][2]["X-Forgehost-Signature"]
    expected_body = json.dumps({"event": "account.created", "data": {"username": "demo1"}}).encode()
    assert signature == wh._sign("s3cret", expected_body)


def test_deliver_retries_then_succeeds(isolated_db, monkeypatch):
    created = wh.create_webhook({"url": "https://example.com/hook", "events": ["account.created"]})
    attempts_made = []

    def flaky_post(url, content=None, headers=None, timeout=None):
        attempts_made.append(1)
        if len(attempts_made) < 3:
            return httpx.Response(500, request=httpx.Request("POST", url))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(wh.httpx, "post", flaky_post)
    with write_session() as session:
        webhook = session.get(Webhook, created["id"])
        delivery = WebhookDelivery(webhook_id=webhook.id, event="account.created", payload={}, status="pending")
        session.add(delivery)
        session.flush()
        delivery_id = delivery.id

    wh._deliver(delivery_id)
    status, attempts, code, error = _poll_delivery(delivery_id)
    assert status == "success"
    assert attempts == 3
    assert len(attempts_made) == 3


def test_deliver_exhausts_retries_and_marks_failed(isolated_db, monkeypatch):
    created = wh.create_webhook({"url": "https://example.com/hook", "events": ["account.created"]})

    def always_fails(url, content=None, headers=None, timeout=None):
        return httpx.Response(503, request=httpx.Request("POST", url))

    monkeypatch.setattr(wh.httpx, "post", always_fails)
    with write_session() as session:
        webhook = session.get(Webhook, created["id"])
        delivery = WebhookDelivery(webhook_id=webhook.id, event="account.created", payload={}, status="pending")
        session.add(delivery)
        session.flush()
        delivery_id = delivery.id

    wh._deliver(delivery_id)
    status, attempts, code, error = _poll_delivery(delivery_id)
    assert status == "failed"
    assert attempts == 3
    assert "503" in error


def test_deliver_handles_connection_error(isolated_db, monkeypatch):
    created = wh.create_webhook({"url": "https://example.com/hook", "events": ["account.created"]})

    def raises(url, content=None, headers=None, timeout=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(wh.httpx, "post", raises)
    with write_session() as session:
        webhook = session.get(Webhook, created["id"])
        delivery = WebhookDelivery(webhook_id=webhook.id, event="account.created", payload={}, status="pending")
        session.add(delivery)
        session.flush()
        delivery_id = delivery.id

    wh._deliver(delivery_id)
    status, attempts, code, error = _poll_delivery(delivery_id)
    assert status == "failed"
    assert "connection refused" in error


# --- maybe_trigger / test_webhook -------------------------------------------


def test_maybe_trigger_ignores_unknown_event_type(isolated_db, monkeypatch):
    wh.create_webhook({"url": "https://example.com/hook", "events": ["account.created"]})
    submitted = []
    monkeypatch.setattr(wh._executor, "submit", lambda fn, *a: submitted.append(a))
    result = wh.maybe_trigger("login.new", _account())  # not a webhook-eligible event type
    assert result == []
    assert submitted == []


def test_maybe_trigger_skips_disabled_webhook(isolated_db, monkeypatch):
    created = wh.create_webhook({"url": "https://example.com/hook", "events": ["account.created"]})
    wh.update_webhook({"webhook_id": created["id"], "enabled": False})
    submitted = []
    monkeypatch.setattr(wh._executor, "submit", lambda fn, *a: submitted.append(a))
    result = wh.maybe_trigger("account.created", _account())
    assert result == []
    assert submitted == []


def test_maybe_trigger_skips_unsubscribed_webhook(isolated_db, monkeypatch):
    wh.create_webhook({"url": "https://example.com/hook", "events": ["ssl.expiring"]})
    submitted = []
    monkeypatch.setattr(wh._executor, "submit", lambda fn, *a: submitted.append(a))
    result = wh.maybe_trigger("account.created", _account())
    assert result == []
    assert submitted == []


def test_maybe_trigger_queues_matching_webhook(isolated_db, monkeypatch):
    wh.create_webhook({"url": "https://example.com/hook", "events": ["account.created"]})
    submitted = []
    monkeypatch.setattr(wh._executor, "submit", lambda fn, *a: submitted.append(a))
    result = wh.maybe_trigger("account.created", _account(), initial_password="x")
    assert len(result) == 1
    assert len(submitted) == 1
    with write_session() as session:
        delivery = session.get(WebhookDelivery, result[0])
        assert delivery.payload["username"] == "demo1"


def test_test_webhook_bypasses_event_subscription(isolated_db, monkeypatch):
    """The test button must work even for a webhook subscribed to a
    completely different event -- it's explicitly a synthetic 'test' event,
    not a real one, so maybe_trigger's own filtering must not apply."""
    created = wh.create_webhook({"url": "https://example.com/hook", "events": ["ssl.expiring"]})
    submitted = []
    monkeypatch.setattr(wh._executor, "submit", lambda fn, *a: submitted.append(a))
    result = wh.test_webhook({"webhook_id": created["id"]})
    assert result["status"] == "queued"
    assert len(submitted) == 1
