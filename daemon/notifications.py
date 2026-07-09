"""Transactional email notifications (Phase 7b feature 3).

Sends via this server's own local Postfix relay (already the account's
MTA, ARCHITECTURE.md SS1) through stdlib `smtplib` -- no new third-party
mail library, matching this project's existing "stdlib/real system tool
over a new dependency" posture (RESEARCH.md's own WP-CLI/predis
precedent). A send requires BOTH the admin's global per-event-type switch
(NotificationSettings) AND the account's own per-event-type preference
(AccountNotificationPrefs) to allow it, and a customer_email to actually be
set -- three independent reasons a "notification" can be a correct, silent
no-op rather than an error.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, AccountNotificationPrefs, BrandingSettings, NOTIFICATION_EVENT_TYPES, NotificationSettings
from shared.validation import ValidationError, validate_email_address, validate_username

logger = logging.getLogger("forgehostd.notifications")

SMTP_HOST = "localhost"
SMTP_PORT = 25
SMTP_TIMEOUT = 10


def _panel_name(session) -> str:
    # Run A feature 3: branding applied to email notifications. Reads the
    # same BrandingSettings singleton row branding.py owns -- this module
    # only reads it (never writes), same read-only cross-feature reuse as
    # e.g. usage_alerts reading Account.quota_hard_mb.
    row = session.get(BrandingSettings, 1)
    return row.panel_name if row and row.panel_name else "Forgehost"


def _subjects(panel_name: str) -> dict:
    return {
        "account.created": f"Your {panel_name} hosting account has been created",
        "account.suspended": f"Your {panel_name} hosting account has been suspended",
        "account.unsuspended": f"Your {panel_name} hosting account has been reactivated",
        "account.terminated": f"Your {panel_name} hosting account has been terminated",
        "backup.completed": "Backup completed",
        "backup.failed": "Backup failed",
        "ssl.expiring": "SSL certificate expiring soon",
        "usage.limit.reached": "Resource usage alert",
        "login.new": "New login to your hosting panel",
        "dns.zone_activated": "Your domain is now active on Cloudflare",
    }


def _render_body(event_type: str, username: str, context: dict) -> str:
    if event_type == "account.created":
        lines = [f"Your hosting account '{username}' has been created."]
        if context.get("initial_password"):
            lines.append(f"Initial password: {context['initial_password']}")
            lines.append("Please log in and change this password as soon as possible.")
        return "\n".join(lines)
    if event_type in ("account.suspended", "account.unsuspended", "account.terminated"):
        verb = event_type.split(".")[1]
        return f"Your hosting account '{username}' has been {verb}."
    if event_type == "backup.completed":
        return f"A backup for account '{username}' completed successfully (job {context.get('job_id')})."
    if event_type == "backup.failed":
        return f"A backup for account '{username}' failed: {context.get('error', 'unknown error')}"
    if event_type == "ssl.expiring":
        return (
            f"The SSL certificate for '{context.get('domain')}' expires in "
            f"{context.get('days_remaining')} day(s) (on {context.get('expiry_date')}). "
            "Automatic renewal should handle this, but please verify it renews correctly."
        )
    if event_type == "usage.limit.reached":
        return (
            f"Account '{username}' has reached {context.get('threshold_pct')}% of its "
            f"{context.get('resource')} limit."
        )
    if event_type == "login.new":
        return f"A new login to the hosting panel for account '{username}' was just recorded."
    if event_type == "dns.zone_activated":
        return (
            f"The DNS zone '{context.get('zone')}' is now active on Cloudflare. "
            "Its records are served from Cloudflare's network; manage them from the DNS section as before."
        )
    return f"Event: {event_type}"


def _get_settings(session) -> NotificationSettings:
    row = session.get(NotificationSettings, 1)
    if row is None:
        row = NotificationSettings(id=1)
        session.add(row)
        session.flush()
    return row


def _get_prefs(session, account_id: int) -> AccountNotificationPrefs:
    row = session.scalar(select(AccountNotificationPrefs).where(AccountNotificationPrefs.account_id == account_id))
    if row is None:
        row = AccountNotificationPrefs(account_id=account_id)
        session.add(row)
        session.flush()
    return row


def _send_email(sender: str, recipient: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
        smtp.send_message(msg)


def send_direct(account, subject: str, body: str) -> bool:
    """Phase 8 feature 12: send an ad-hoc admin notification to an account's
    contact email (used by the bulk 'notify' action). Uses the global sender;
    returns False (not an error) if no sender or no customer_email is set --
    there is simply nowhere to send it. Raises on an actual SMTP failure so the
    bulk job records it and stops."""
    if account is None:
        return False
    with write_session() as session:
        settings_row = _get_settings(session)
        if not settings_row.sender_address:
            return False
        prefs = _get_prefs(session, account.id)
        if not prefs.customer_email:
            return False
        sender = settings_row.sender_address
        recipient = prefs.customer_email
    _send_email(sender, recipient, subject, body)
    return True


def maybe_send(event_type: str, account, **context) -> bool:
    """Returns True only if an email was actually handed to the local MTA
    -- callers don't need the result today, but tests do, to distinguish
    "correctly skipped" from "silently broken"."""
    if account is None:
        return False
    account_id = account.id
    username = account.username

    with write_session() as session:
        settings_row = _get_settings(session)
        if not settings_row.sender_address:
            return False
        if not settings_row.events.get(event_type, True):
            return False
        prefs = _get_prefs(session, account_id)
        if not prefs.customer_email:
            return False
        if not prefs.events.get(event_type, True):
            return False
        sender = settings_row.sender_address
        recipient = prefs.customer_email
        panel_name = _panel_name(session)

    subject = _subjects(panel_name).get(event_type, f"{panel_name} notification: {event_type}")
    body = _render_body(event_type, username, context) + f"\n\n— {panel_name}"
    try:
        _send_email(sender, recipient, subject, body)
    except (OSError, smtplib.SMTPException):
        logger.exception("failed to send '%s' notification email for account '%s'", event_type, username)
        return False
    return True


# --- admin settings (RPC ops) ----------------------------------------------


def _settings_to_dict(row: NotificationSettings) -> dict:
    return {
        "sender_address": row.sender_address,
        "events": row.events,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _merge_events(current: dict, updates) -> dict:
    if not isinstance(updates, dict):
        raise ValidationError("events must be an object of event_type -> bool")
    merged = dict(current)
    for key, value in updates.items():
        if key not in NOTIFICATION_EVENT_TYPES:
            raise ValidationError(f"unknown event type '{key}' (allowed: {NOTIFICATION_EVENT_TYPES})")
        merged[key] = bool(value)
    return merged


def get_settings(params: dict | None = None) -> dict:
    with write_session() as session:
        return _settings_to_dict(_get_settings(session))


def set_settings(params: dict) -> dict:
    # Real bug found by adversarial review (confirmed with a real, isolated
    # SQLite reproduction, not just reasoned about): the original code
    # unconditionally overwrote sender_address on every call via
    # `params.get("sender_address", "")` -- a caller updating only `events`
    # (the UI's own per-event checkboxes, or a REST client doing a genuine
    # partial PATCH) would silently wipe the sender address to "", which
    # this module's own `maybe_send` treats as "notifications globally
    # disabled" -- a single events-only update would silently turn off
    # every notification for every account. `"sender_address" in params`
    # (not `.get(..., "")`) is what actually distinguishes "not provided,
    # leave alone" from "provided as empty, clear it" -- the same
    # partial-update convention `handlers_account.set_limits`'s own
    # `params.get("cpu_pct", account.cpu_pct)` pattern already establishes
    # elsewhere in this project, just needed here as an explicit `in`
    # check since "" is falsy but still a meaningfully different value
    # from "absent".
    with write_session() as session:
        row = _get_settings(session)
        if "sender_address" in params:
            sender_address = params["sender_address"] or ""
            if sender_address:
                sender_address = validate_email_address(sender_address)
            row.sender_address = sender_address
        events = params.get("events")
        if events is not None:
            row.events = _merge_events(row.events, events)
        session.flush()
        return _settings_to_dict(row)


# --- per-account prefs (RPC ops) --------------------------------------------


def _prefs_to_dict(row: AccountNotificationPrefs) -> dict:
    return {
        "customer_email": row.customer_email,
        "events": row.events,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_prefs(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        return _prefs_to_dict(_get_prefs(session, account.id))


def set_prefs(params: dict) -> dict:
    # Same real bug and same fix as set_settings above (found in the same
    # adversarial review pass): "customer_email" in params, not
    # params.get("customer_email"), so an events-only update doesn't
    # silently clear the customer's email address as a side effect.
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        row = _get_prefs(session, account.id)
        if "customer_email" in params:
            raw_email = params["customer_email"]
            row.customer_email = validate_email_address(raw_email) if raw_email else None
        events = params.get("events")
        if events is not None:
            row.events = _merge_events(row.events, events)
        session.flush()
        return _prefs_to_dict(row)
