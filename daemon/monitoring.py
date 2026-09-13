"""Run A feature 5: service health monitoring + admin alert emails.

A */5 cron (scripts/monitoring_check.py) runs check_services(): one
`systemctl is-active` per monitored unit, one ServiceCheck history row
per (service, pass), and up<->down transition alerts emailed to the
admin. Reuses servicemgr.SERVICE_REGISTRY's real unit names (the module
that already owns "which systemd unit is 'postfix' actually") plus
fail2ban, which the goal monitors but servicemgr deliberately doesn't
manage (it protects the panel itself -- not something an admin should
stop from the UI, but its health is still worth watching).

Alert semantics (documented decisions, not guesses):
- A down->up recovery always attempts an email; a service that STAYS down
  re-alerts only after the cooldown (default 30 min) since the last
  successfully-sent alert for that service.
- `last_alert_sent_at` advances ONLY on a successful SMTP handoff. This
  is load-bearing for the "Postfix itself is down" case: the down alert's
  send fails (the local MTA is the thing that died), stays un-consumed,
  and the recovery email after Postfix returns is what actually reaches
  the admin -- carrying the downtime window, so the admin still learns
  both facts from one delivered message.
"""
from __future__ import annotations

import datetime as dt
import logging
import smtplib

from sqlalchemy import delete, select

from shared.db import write_session
from shared.models import MonitoringAlertState, MonitoringSettings, ServiceCheck, utcnow
from shared.validation import ValidationError, validate_email_address

from daemon import notifications, servicemgr
from daemon.procutil import run

logger = logging.getLogger("borond.monitoring")

# servicemgr's registry + fail2ban (monitored, not manageable -- see module
# docstring). Key order is the display order in the admin UI.
MONITORED_SERVICES = {**servicemgr.SERVICE_REGISTRY, "fail2ban": "fail2ban.service", "clock": "clock-sync"}

# Same retention posture as HealthSnapshot (daemon/health.py): 24h of UI
# data plus margin; this table has no natural cleanup event.
RETENTION_HOURS = 48

MIN_COOLDOWN_MINUTES = 1
MAX_COOLDOWN_MINUTES = 24 * 60


def _get_settings(session) -> MonitoringSettings:
    row = session.get(MonitoringSettings, 1)
    if row is None:
        row = MonitoringSettings(id=1)
        session.add(row)
        session.flush()
    return row


def _settings_to_dict(row: MonitoringSettings) -> dict:
    return {
        "enabled": row.enabled,
        "admin_email": row.admin_email,
        "cooldown_minutes": row.cooldown_minutes,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_settings(params: dict | None = None) -> dict:
    with write_session() as session:
        return _settings_to_dict(_get_settings(session))


def set_settings(params: dict) -> dict:
    with write_session() as session:
        row = _get_settings(session)
        if "enabled" in params:
            row.enabled = bool(params["enabled"])
        if "admin_email" in params:
            raw = params["admin_email"]
            row.admin_email = validate_email_address(raw) if raw else None
        if "cooldown_minutes" in params:
            try:
                cooldown = int(params["cooldown_minutes"])
            except (TypeError, ValueError):
                raise ValidationError("cooldown_minutes must be an integer") from None
            if not (MIN_COOLDOWN_MINUTES <= cooldown <= MAX_COOLDOWN_MINUTES):
                raise ValidationError(
                    f"cooldown_minutes must be between {MIN_COOLDOWN_MINUTES} and {MAX_COOLDOWN_MINUTES}"
                )
            row.cooldown_minutes = cooldown
        session.flush()
        return _settings_to_dict(row)


def _is_active(unit: str) -> bool:
    if unit=="clock-sync":
        from daemon.clock_health import get_status
        return get_status(refresh=True)["status"]=="healthy"
    result = run(["systemctl", "is-active", unit], timeout=10)
    return result.stdout.strip() == "active"


def _get_state(session, service: str) -> MonitoringAlertState:
    row = session.scalar(select(MonitoringAlertState).where(MonitoringAlertState.service == service))
    if row is None:
        row = MonitoringAlertState(service=service)
        session.add(row)
        session.flush()
    return row


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    # SQLite round-trips DateTime(timezone=True) as naive UTC -- same
    # normalization api/security.py already does for Session.expires_at.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


def _send_alert(sender: str, recipient: str, subject: str, body: str) -> bool:
    try:
        notifications._send_email(sender, recipient, subject, body)
        return True
    except (OSError, smtplib.SMTPException):
        logger.exception("monitoring alert email failed (will retry next pass)")
        return False


def _format_duration(delta: dt.timedelta) -> str:
    total = int(delta.total_seconds())
    hours, rem = divmod(total, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def check_services(params: dict | None = None) -> dict:
    """One monitoring pass. Returns per-service status plus which alerts
    were sent -- the cron script logs it, tests assert on it."""
    now = utcnow()
    results: dict[str, bool] = {unit_key: _is_active(unit) for unit_key, unit in MONITORED_SERVICES.items()}

    sent: list[str] = []
    with write_session() as session:
        settings_row = _get_settings(session)
        # Sender reuses the notifications feature's configured address --
        # monitoring is another consumer of the same outbound-mail identity,
        # not a second one to configure/drift separately.
        sender = notifications._get_settings(session).sender_address or ""
        recipient = settings_row.admin_email or ""
        cooldown = dt.timedelta(minutes=settings_row.cooldown_minutes)
        can_email = settings_row.enabled and bool(sender) and bool(recipient)

        for service, active in results.items():
            session.add(ServiceCheck(service=service, active=active, checked_at=now))
            state = _get_state(session, service)
            last_sent = _aware(state.last_alert_sent_at)
            cooled_down = last_sent is None or (now - last_sent) >= cooldown

            if not active and not state.is_down:
                # up -> down transition
                state.is_down = True
                state.down_since = now
                if can_email and cooled_down:
                    ok = _send_alert(
                        sender, recipient,
                        f"[monitoring] {service} is DOWN",
                        f"Service '{service}' ({MONITORED_SERVICES[service]}) is not active as of {now.isoformat()}.",
                    )
                    if ok:
                        state.last_alert_sent_at = now
                        sent.append(f"{service}:down")
            elif not active and state.is_down:
                # still down -- re-alert only after cooldown
                if can_email and cooled_down:
                    down_since = _aware(state.down_since) or now
                    ok = _send_alert(
                        sender, recipient,
                        f"[monitoring] {service} is still DOWN",
                        f"Service '{service}' has been down since {down_since.isoformat()} "
                        f"({_format_duration(now - down_since)}).",
                    )
                    if ok:
                        state.last_alert_sent_at = now
                        sent.append(f"{service}:down")
            elif active and state.is_down:
                # down -> up recovery: always attempted (not cooldown-gated --
                # the "it's fixed" signal is exactly what the admin is waiting
                # for after a down alert).
                down_since = _aware(state.down_since) or now
                state.is_down = False
                state.down_since = None
                if can_email:
                    ok = _send_alert(
                        sender, recipient,
                        f"[monitoring] {service} recovered",
                        f"Service '{service}' is active again as of {now.isoformat()} "
                        f"(was down for {_format_duration(now - down_since)}).",
                    )
                    if ok:
                        state.last_alert_sent_at = now
                        sent.append(f"{service}:recovery")

        session.execute(delete(ServiceCheck).where(ServiceCheck.checked_at < now - dt.timedelta(hours=RETENTION_HOURS)))

    return {"services": results, "alerts_sent": sent}


def get_history(params: dict) -> dict:
    """24h (default) per-service uptime history + current state + last
    alert, for the admin UI."""
    hours = int((params or {}).get("hours", 24) or 24)
    hours = max(1, min(hours, RETENTION_HOURS))
    cutoff = utcnow() - dt.timedelta(hours=hours)

    with write_session() as session:
        checks = session.scalars(
            select(ServiceCheck).where(ServiceCheck.checked_at >= cutoff).order_by(ServiceCheck.checked_at)
        ).all()
        states = {s.service: s for s in session.scalars(select(MonitoringAlertState)).all()}

        services = []
        for service in MONITORED_SERVICES:
            rows = [c for c in checks if c.service == service]
            total = len(rows)
            up = sum(1 for c in rows if c.active)
            state = states.get(service)
            services.append({
                "service": service,
                "unit": MONITORED_SERVICES[service],
                "checks": total,
                "uptime_pct": round(up / total * 100, 2) if total else None,
                "currently_down": bool(state.is_down) if state else False,
                "down_since": state.down_since.isoformat() if state and state.down_since else None,
                "last_alert_sent_at": state.last_alert_sent_at.isoformat() if state and state.last_alert_sent_at else None,
                # Sparkline-friendly series: [ [iso, 0|1], ... ]
                "series": [[c.checked_at.isoformat(), 1 if c.active else 0] for c in rows],
            })
        return {"hours": hours, "services": services}
