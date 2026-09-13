from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from daemon import monitoring
from daemon import notifications
from shared.db import write_session
from shared.models import MonitoringAlertState, ServiceCheck, utcnow


@pytest.fixture()
def mail_spy(monkeypatch):
    """Capture outbound alert emails; individual tests can flip .fail to
    simulate the local MTA being down (the Postfix-is-the-down-service
    case)."""
    spy = type("Spy", (), {"sent": [], "fail": False})()

    def fake_send(sender, recipient, subject, body):
        if spy.fail:
            raise OSError("connection refused (MTA down)")
        spy.sent.append({"sender": sender, "recipient": recipient, "subject": subject, "body": body})

    monkeypatch.setattr(notifications, "_send_email", fake_send)
    return spy


@pytest.fixture()
def configured(isolated_db, mail_spy):
    notifications.set_settings({"sender_address": "panel@example.com"})
    monitoring.set_settings({"admin_email": "admin@example.com", "enabled": True})
    return mail_spy


def _stub_status(monkeypatch, down: set[str] = frozenset()):
    from daemon import clock_health
    monkeypatch.setattr(clock_health,"get_status",lambda refresh=False:{"status":"critical" if "clock" in down else "healthy"})
    def fake_run(argv, timeout=10):
        unit = argv[-1]
        service = next((k for k, u in monitoring.MONITORED_SERVICES.items() if u == unit), None)
        active = service not in down
        return type("R", (), {"stdout": "active\n" if active else "inactive\n", "ok": active})()

    monkeypatch.setattr(monitoring, "run", fake_run)


# --- settings ----------------------------------------------------------------


def test_settings_defaults(isolated_db):
    data = monitoring.get_settings({})
    assert data["enabled"] is True
    assert data["admin_email"] is None
    assert data["cooldown_minutes"] == 30


def test_settings_partial_update_and_validation(isolated_db):
    monitoring.set_settings({"admin_email": "ops@example.com"})
    data = monitoring.get_settings({})
    assert data["admin_email"] == "ops@example.com"
    assert data["cooldown_minutes"] == 30  # untouched

    with pytest.raises(Exception):
        monitoring.set_settings({"admin_email": "not-an-email"})
    with pytest.raises(Exception):
        monitoring.set_settings({"cooldown_minutes": 0})
    with pytest.raises(Exception):
        monitoring.set_settings({"cooldown_minutes": 100000})


# --- check pass + alerts -----------------------------------------------------


def test_all_up_writes_history_no_alerts(configured, monkeypatch):
    _stub_status(monkeypatch)
    result = monitoring.check_services()
    assert all(result["services"].values())
    assert result["alerts_sent"] == []
    with write_session() as session:
        rows = session.scalars(select(ServiceCheck)).all()
        assert len(rows) == len(monitoring.MONITORED_SERVICES)
        assert all(r.active for r in rows)


def test_down_transition_sends_alert(configured, monkeypatch):
    _stub_status(monkeypatch, down={"dovecot"})
    result = monitoring.check_services()
    assert result["services"]["dovecot"] is False
    assert result["alerts_sent"] == ["dovecot:down"]
    assert len(configured.sent) == 1
    assert "DOWN" in configured.sent[0]["subject"]
    assert configured.sent[0]["recipient"] == "admin@example.com"


def test_still_down_within_cooldown_no_realert(configured, monkeypatch):
    _stub_status(monkeypatch, down={"dovecot"})
    monitoring.check_services()
    result = monitoring.check_services()  # immediately again -- inside cooldown
    assert result["alerts_sent"] == []
    assert len(configured.sent) == 1


def test_still_down_after_cooldown_realerts(configured, monkeypatch):
    _stub_status(monkeypatch, down={"dovecot"})
    monitoring.check_services()
    # Age the last-sent timestamp past the cooldown.
    with write_session() as session:
        state = session.scalar(select(MonitoringAlertState).where(MonitoringAlertState.service == "dovecot"))
        state.last_alert_sent_at = utcnow() - dt.timedelta(minutes=31)
    result = monitoring.check_services()
    assert result["alerts_sent"] == ["dovecot:down"]
    assert len(configured.sent) == 2
    assert "still DOWN" in configured.sent[1]["subject"]


def test_recovery_sends_email_with_duration(configured, monkeypatch):
    _stub_status(monkeypatch, down={"dovecot"})
    monitoring.check_services()
    _stub_status(monkeypatch)  # back up
    result = monitoring.check_services()
    assert result["alerts_sent"] == ["dovecot:recovery"]
    assert "recovered" in configured.sent[-1]["subject"]
    assert "was down for" in configured.sent[-1]["body"]
    with write_session() as session:
        state = session.scalar(select(MonitoringAlertState).where(MonitoringAlertState.service == "dovecot"))
        assert state.is_down is False
        assert state.down_since is None


def test_failed_send_does_not_consume_cooldown(configured, monkeypatch):
    """The Postfix-is-down case: the down alert's SMTP handoff fails, so the
    next pass retries instead of being silently cooled down."""
    _stub_status(monkeypatch, down={"postfix"})
    configured.fail = True
    result = monitoring.check_services()
    assert result["alerts_sent"] == []  # send failed
    configured.fail = False
    result = monitoring.check_services()  # still down, retry succeeds
    assert result["alerts_sent"] == ["postfix:down"]


def test_recovery_after_failed_down_alert_still_delivers(configured, monkeypatch):
    """Postfix down -> alert undeliverable -> Postfix back up -> the recovery
    email (with the downtime window) is what actually reaches the admin."""
    _stub_status(monkeypatch, down={"postfix"})
    configured.fail = True
    monitoring.check_services()
    configured.fail = False
    _stub_status(monkeypatch)
    result = monitoring.check_services()
    assert result["alerts_sent"] == ["postfix:recovery"]
    assert len(configured.sent) == 1
    assert "recovered" in configured.sent[0]["subject"]


def test_disabled_monitoring_records_history_but_never_emails(configured, monkeypatch):
    monitoring.set_settings({"enabled": False})
    _stub_status(monkeypatch, down={"mariadb"})
    result = monitoring.check_services()
    assert result["alerts_sent"] == []
    assert configured.sent == []
    with write_session() as session:
        assert session.scalars(select(ServiceCheck)).all()  # history still written


def test_no_admin_email_never_sends(isolated_db, mail_spy, monkeypatch):
    notifications.set_settings({"sender_address": "panel@example.com"})
    _stub_status(monkeypatch, down={"ols"})
    result = monitoring.check_services()
    assert result["alerts_sent"] == []
    assert mail_spy.sent == []


# --- history -----------------------------------------------------------------


def test_history_uptime_pct_and_state(configured, monkeypatch):
    _stub_status(monkeypatch, down={"dovecot"})
    monitoring.check_services()
    _stub_status(monkeypatch)
    monitoring.check_services()

    data = monitoring.get_history({"hours": 24})
    by_service = {s["service"]: s for s in data["services"]}
    assert by_service["dovecot"]["checks"] == 2
    assert by_service["dovecot"]["uptime_pct"] == 50.0
    assert by_service["dovecot"]["currently_down"] is False
    assert by_service["dovecot"]["last_alert_sent_at"] is not None
    assert by_service["ols"]["uptime_pct"] == 100.0
    assert len(by_service["dovecot"]["series"]) == 2


def test_history_includes_services_and_clock(isolated_db, monkeypatch):
    _stub_status(monkeypatch)
    monitoring.check_services()
    data = monitoring.get_history({})
    assert {s["service"] for s in data["services"]} == set(monitoring.MONITORED_SERVICES)
    assert len(data["services"]) == 8


def test_retention_prunes_old_checks(configured, monkeypatch):
    _stub_status(monkeypatch)
    with write_session() as session:
        session.add(ServiceCheck(service="ols", active=True, checked_at=utcnow() - dt.timedelta(hours=49)))
    monitoring.check_services()
    with write_session() as session:
        old = session.scalars(
            select(ServiceCheck).where(ServiceCheck.checked_at < utcnow() - dt.timedelta(hours=48))
        ).all()
        assert old == []


def test_clock_failure_and_recovery_use_alert_cooldown(configured,monkeypatch):
    _stub_status(monkeypatch,down={'clock'})
    assert monitoring.check_services()['alerts_sent']==['clock:down']
    assert monitoring.check_services()['alerts_sent']==[]
    _stub_status(monkeypatch)
    assert monitoring.check_services()['alerts_sent']==['clock:recovery']
    assert len(configured.sent)==2
