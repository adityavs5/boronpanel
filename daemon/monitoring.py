"""Service and host health monitoring with deduplicated multi-channel alerts.

A */5 cron (scripts/monitoring_check.py) runs check_services(): one
`systemctl is-active` per monitored unit, one ServiceCheck history row
per (service, pass), and up<->down transition alerts sent through the
selected configured channels. Reuses servicemgr.SERVICE_REGISTRY's real unit names (the module
that already owns "which systemd unit is 'postfix' actually") plus
fail2ban, which the goal monitors but servicemgr deliberately doesn't
manage (it protects the panel itself -- not something an admin should
stop from the UI, but its health is still worth watching).

Alert semantics (documented decisions, not guesses):
- A down->up recovery always attempts delivery; a service that STAYS down
  re-alerts only after the cooldown (default 30 min) since the last
  successfully accepted alert for that service.
- `last_alert_sent_at` advances only when at least one selected provider
  accepts delivery. A missing or broken provider does not consume the
  cooldown, so a later monitoring pass can retry after configuration is fixed.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

import psutil

from sqlalchemy import delete, func, select

from shared.db import write_session
from shared.models import (
    BackupTelegramSettings,
    LoginFailureEvent,
    MonitoringAlertState,
    MonitoringSettings,
    NotificationSettings,
    ServiceCheck,
    Webhook,
    utcnow,
)
from shared.validation import ValidationError, validate_email_address

from daemon import events, servicemgr
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
ALERT_CHANNELS = frozenset({"email", "webhook", "telegram"})
HOST_ALERT_EVENTS = frozenset({
    "host.disk", "host.inodes", "host.memory", "host.load",
    "host.login_burst", "host.service_down", "host.service_recovered",
})

THRESHOLD_RULES = {
    "disk_free_pct": (1, 50, int),
    "inode_free_pct": (1, 50, int),
    "memory_used_pct": (50, 100, int),
    "load_per_cpu": (0.5, 20.0, float),
    "failed_login_threshold": (2, 10_000, int),
    "failed_login_window_minutes": (1, 1_440, int),
}


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
        "channels": list(row.channels if row.channels is not None else ["email"]),
        **{key: getattr(row, key) for key in THRESHOLD_RULES},
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _delivery_status(session, row: MonitoringSettings) -> dict:
    notification = session.get(NotificationSettings, 1)
    telegram = session.get(BackupTelegramSettings, 1)
    enabled_webhooks = session.scalars(
        select(Webhook).where(Webhook.enabled.is_(True))
    ).all()
    configured = {
        "email": bool(notification and notification.sender_address and row.admin_email),
        "webhook": any(HOST_ALERT_EVENTS.intersection(webhook.events or []) for webhook in enabled_webhooks),
        "telegram": bool(
            telegram and telegram.enabled and telegram.token_enc and telegram.chat_id
            and HOST_ALERT_EVENTS.intersection(telegram.events or [])
        ),
    }
    selected = list(row.channels if row.channels is not None else ["email"])
    missing = [channel for channel in selected if not configured.get(channel, False)]
    warning = None
    if not selected:
        warning = "Choose at least one alert channel. Host alerts are currently disabled."
    elif missing:
        warning = "Configure the selected alert channel(s): " + ", ".join(missing) + "."
    return {
        "configured_channels": configured,
        "delivery_ready": any(configured.get(channel, False) for channel in selected),
        "setup_warning": warning,
    }


def get_settings(params: dict | None = None) -> dict:
    with write_session() as session:
        row = _get_settings(session)
        return {**_settings_to_dict(row), **_delivery_status(session, row)}


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
        if "channels" in params:
            channels = params["channels"]
            if not isinstance(channels, list) or any(channel not in ALERT_CHANNELS for channel in channels):
                raise ValidationError("Choose valid alert channels")
            row.channels = list(dict.fromkeys(channels))
        for key, (minimum, maximum, cast) in THRESHOLD_RULES.items():
            if key not in params:
                continue
            try:
                value = cast(params[key])
            except (TypeError, ValueError):
                raise ValidationError(f"{key} must be a number") from None
            if not minimum <= value <= maximum:
                raise ValidationError(f"{key} must be between {minimum} and {maximum}")
            setattr(row, key, value)
        session.flush()
        return {**_settings_to_dict(row), **_delivery_status(session, row)}


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


def _format_duration(delta: dt.timedelta) -> str:
    total = int(delta.total_seconds())
    hours, rem = divmod(total, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _delivered(results: dict[str, object]) -> bool:
    return any(bool(value) for value in results.values())


def _emit(event_type: str, message: str, channels: list[str], **context) -> bool:
    return _delivered(events.emit(
        event_type,
        None,
        _channels=channels,
        message=message,
        **context,
    ))


def _host_metrics(settings_row: MonitoringSettings, now: dt.datetime, session) -> dict[str, dict]:
    disk = psutil.disk_usage("/")
    try:
        stat = os.statvfs("/")
        inode_total = stat.f_files
        inode_free_pct = (stat.f_favail / inode_total * 100) if inode_total else 100.0
    except OSError:
        inode_free_pct = 100.0
    memory = psutil.virtual_memory()
    cpu_count = psutil.cpu_count() or 1
    load_per_cpu = psutil.getloadavg()[0] / cpu_count
    window_start = now - dt.timedelta(minutes=settings_row.failed_login_window_minutes)
    failed_logins = int(session.scalar(
        select(func.count()).select_from(LoginFailureEvent).where(
            LoginFailureEvent.occurred_at >= window_start
        )
    ) or 0)
    disk_free_pct = 100.0 - float(disk.percent)
    return {
        "host.disk": {
            "problem": disk_free_pct < settings_row.disk_free_pct,
            "event": "host.disk",
            "message": f"Root filesystem has {disk_free_pct:.1f}% free; alert threshold is {settings_row.disk_free_pct}%.",
            "value": round(disk_free_pct, 2),
        },
        "host.inodes": {
            "problem": inode_free_pct < settings_row.inode_free_pct,
            "event": "host.inodes",
            "message": f"Root filesystem has {inode_free_pct:.1f}% inodes free; alert threshold is {settings_row.inode_free_pct}%.",
            "value": round(inode_free_pct, 2),
        },
        "host.memory": {
            "problem": float(memory.percent) >= settings_row.memory_used_pct,
            "event": "host.memory",
            "message": f"Memory use is {memory.percent:.1f}%; alert threshold is {settings_row.memory_used_pct}%.",
            "value": round(float(memory.percent), 2),
        },
        "host.load": {
            "problem": load_per_cpu >= settings_row.load_per_cpu,
            "event": "host.load",
            "message": f"One-minute load is {load_per_cpu:.2f} per CPU; alert threshold is {settings_row.load_per_cpu:.2f}.",
            "value": round(load_per_cpu, 3),
        },
        "host.login_burst": {
            "problem": failed_logins >= settings_row.failed_login_threshold,
            "event": "host.login_burst",
            "message": f"{failed_logins} failed sign-in attempts occurred in the last {settings_row.failed_login_window_minutes} minutes; alert threshold is {settings_row.failed_login_threshold}.",
            "value": failed_logins,
        },
    }


def check_services(params: dict | None = None) -> dict:
    """Run one bounded service + host monitoring pass.

    ``last_alert_sent_at`` advances only after at least one selected channel
    accepted the event.  A missing sender/recipient therefore never consumes
    the cooldown and the next cron pass can retry after configuration is
    repaired.
    """
    now = utcnow()
    results: dict[str, bool] = {unit_key: _is_active(unit) for unit_key, unit in MONITORED_SERVICES.items()}

    sent: list[str] = []
    service_transitions: list[tuple[str, str, str, str, str, list[str]]] = []
    with write_session() as session:
        settings_row = _get_settings(session)
        cooldown = dt.timedelta(minutes=settings_row.cooldown_minutes)
        channels = list(settings_row.channels if settings_row.channels is not None else ["email"])

        for service, active in results.items():
            session.add(ServiceCheck(service=service, active=active, checked_at=now))
            state = _get_state(session, service)
            last_sent = _aware(state.last_alert_sent_at)
            cooled_down = last_sent is None or (now - last_sent) >= cooldown

            if not active and not state.is_down:
                # up -> down transition
                state.is_down = True
                state.down_since = now
                if settings_row.enabled and channels and cooled_down:
                    message = f"Service '{service}' ({MONITORED_SERVICES[service]}) is not active as of {now.isoformat()}."
                    service_transitions.append((service, "down", "host.service_down", message, f"[monitoring] {service} is DOWN", channels))
            elif not active and state.is_down:
                # still down -- re-alert only after cooldown
                if settings_row.enabled and channels and cooled_down:
                    down_since = _aware(state.down_since) or now
                    message = (
                        f"Service '{service}' is still DOWN and has been down since {down_since.isoformat()} "
                        f"({_format_duration(now - down_since)})."
                    )
                    service_transitions.append((service, "down", "host.service_down", message, f"[monitoring] {service} is still DOWN", channels))
            elif active and state.is_down:
                # down -> up recovery: always attempted (not cooldown-gated --
                # the "it's fixed" signal is exactly what the admin is waiting
                # for after a down alert).
                down_since = _aware(state.down_since) or now
                state.is_down = False
                state.down_since = None
                if settings_row.enabled and channels:
                    message = (
                        f"Service '{service}' recovered and is active again as of {now.isoformat()} "
                        f"(was down for {_format_duration(now - down_since)})."
                    )
                    service_transitions.append((service, "recovery", "host.service_recovered", message, f"[monitoring] {service} recovered", channels))

        session.execute(delete(ServiceCheck).where(ServiceCheck.checked_at < now - dt.timedelta(hours=RETENTION_HOURS)))

    for service, transition, event_type, message, subject, selected_channels in service_transitions:
        if _emit(event_type, message, selected_channels, service=service, subject_override=subject):
            with write_session() as session:
                _get_state(session, service).last_alert_sent_at = now
            sent.append(f"{service}:{transition}")

    # Host probes are intentionally collected after the service-history
    # transaction.  The failed-login count uses its own short read/write
    # transaction and no network delivery occurs while either DB transaction
    # is held.
    with write_session() as session:
        settings_row = _get_settings(session)
        host_metrics = _host_metrics(settings_row, now, session)
        cooldown = dt.timedelta(minutes=settings_row.cooldown_minutes)
        channels = list(settings_row.channels if settings_row.channels is not None else ["email"])
        host_transitions: list[tuple[str, str, str, dict]] = []
        for key, metric in host_metrics.items():
            state = _get_state(session, key)
            last_sent = _aware(state.last_alert_sent_at)
            cooled_down = last_sent is None or (now - last_sent) >= cooldown
            if metric["problem"] and not state.is_down:
                state.is_down = True
                state.down_since = now
                if settings_row.enabled and channels and cooled_down:
                    host_transitions.append((key, "down", metric["event"], metric))
            elif metric["problem"] and state.is_down and settings_row.enabled and channels and cooled_down:
                host_transitions.append((key, "down", metric["event"], metric))
            elif not metric["problem"] and state.is_down:
                down_since = _aware(state.down_since) or now
                state.is_down = False
                state.down_since = None
                if settings_row.enabled and channels:
                    recovered = dict(metric)
                    recovered["message"] = (
                        f"{key.removeprefix('host.').replace('_', ' ').title()} recovered after "
                        f"{_format_duration(now - down_since)}. Current value: {metric['value']}."
                    )
                    host_transitions.append((key, "recovery", "host.service_recovered", recovered))

    # Dispatch outside the state transaction; mark cooldown only after a
    # provider accepted the event.  A parallel cron pass can at worst produce
    # one duplicate during this tiny window, while never holding SQLite over
    # SMTP/HTTP I/O.
    for key, transition, event_type, metric in host_transitions:
        if _emit(event_type, metric["message"], channels, metric=key, value=metric["value"]):
            with write_session() as session:
                _get_state(session, key).last_alert_sent_at = now
            sent.append(f"{key}:{transition}")

    return {
        "services": results,
        "host": {key: {"problem": value["problem"], "value": value["value"]} for key, value in host_metrics.items()},
        "alerts_sent": sent,
    }


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
