import pytest

from daemon import events
from daemon import notifications as nt
from daemon import webhooks as wh


class _FakeAccount:
    id = 1
    username = "demo1"


def test_emit_calls_both_channels(monkeypatch):
    calls = []
    monkeypatch.setattr(nt, "maybe_send", lambda event_type, account, **ctx: calls.append(("notify", event_type)))
    monkeypatch.setattr(wh, "maybe_trigger", lambda event_type, account, **ctx: calls.append(("webhook", event_type)))
    events.emit("account.created", _FakeAccount(), initial_password="x")
    assert ("notify", "account.created") in calls
    assert ("webhook", "account.created") in calls


def test_emit_notification_failure_does_not_block_webhook(monkeypatch):
    calls = []

    def boom(event_type, account, **ctx):
        raise RuntimeError("smtp exploded")

    monkeypatch.setattr(nt, "maybe_send", boom)
    monkeypatch.setattr(wh, "maybe_trigger", lambda event_type, account, **ctx: calls.append("webhook"))
    events.emit("account.created", _FakeAccount())  # must not raise
    assert calls == ["webhook"]


def test_emit_webhook_failure_does_not_block_notification(monkeypatch):
    calls = []

    def boom(event_type, account, **ctx):
        raise RuntimeError("http exploded")

    monkeypatch.setattr(nt, "maybe_send", lambda event_type, account, **ctx: calls.append("notify"))
    monkeypatch.setattr(wh, "maybe_trigger", boom)
    events.emit("account.created", _FakeAccount())  # must not raise
    assert calls == ["notify"]


def test_emit_never_raises_even_if_both_channels_fail(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(nt, "maybe_send", boom)
    monkeypatch.setattr(wh, "maybe_trigger", boom)
    events.emit("account.created", _FakeAccount())  # must not raise
