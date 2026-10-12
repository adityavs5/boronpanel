"""Cross-cutting account-lifecycle event dispatch (Phase 7b features 3/4/5).

The one place account-lifecycle/backup/SSL-expiry/usage-alert code calls
into to fan an event out to both channels those features need (email
notification, outbound webhook) -- callers emit an event once instead of
every future event source needing to know about both channels and their
own independent failure handling. Mirrors this project's existing
TERMINATE_HOOKS/CREATE_HOOKS pattern in spirit (many independent side
effects off one lifecycle moment), just as a single fan-out function
rather than a list, since both channels always run for every event type
(each channel does its own internal enabled/subscribed filtering).
"""
from __future__ import annotations

import logging

logger = logging.getLogger("borond.events")


def emit(event_type: str, account, **context) -> dict[str, object]:
    """`account` is an Account ORM row (attached or detached -- every
    caller's session-scoped snapshot works fine here, since only
    already-loaded columns like .id/.username are read) or None for an
    event with no single owning account. Each channel's failure is caught
    and logged independently here -- notification delivery failing must
    never block webhook delivery, and neither may ever propagate back to
    block the real provisioning action that triggered this event."""
    from daemon import backup_notifications, notifications, webhooks

    # Host monitoring can be configured per channel.  All pre-existing
    # callers omit this private routing hint and retain the historical fanout
    # to every configured provider.
    selected = context.pop("_channels", None)
    channels = set(selected or ("email", "webhook", "telegram"))
    results: dict[str, object] = {}

    if "email" in channels:
        try:
            results["email"] = notifications.maybe_send(event_type, account, **context)
        except Exception:
            results["email"] = False
            logger.exception("notification dispatch failed for event '%s'", event_type)

    if "webhook" in channels:
        try:
            results["webhook"] = webhooks.maybe_trigger(event_type, account, **context)
        except Exception:
            results["webhook"] = []
            logger.exception("webhook dispatch failed for event '%s'", event_type)

    if "telegram" in channels:
        try:
            results["telegram"] = backup_notifications.send_telegram(event_type, account, **context)
        except Exception:
            results["telegram"] = False
            logger.exception("Telegram dispatch failed for event '%s'", event_type)
    return results
