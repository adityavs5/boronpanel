from __future__ import annotations

import datetime as dt
import time

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from starlette.requests import Request

from api.routers import metrics
from daemon import handlers_auth, monitoring, retention, totp
from shared.config import settings
from shared.db import write_session
from shared.models import (
    Account,
    CommandRun,
    LoginFailureEvent,
    PanelUser,
    SecurityPolicySettings,
    Session,
    TotpCredential,
    UsageMonthlyAggregate,
    UsageSnapshot,
    utcnow,
)
from shared.passwords import hash_password
from shared.session_ids import session_digest
from shared.validation import ValidationError
from scripts import reset_admin_2fa


def _account(db, username="p8acct"):
    row = Account(username=username, uid=7100, gid=7100)
    db.add(row)
    db.flush()
    return row


def test_retention_protected_minimum_hold_and_monthly_rollup(isolated_db):
    old = utcnow() - dt.timedelta(days=45)
    with write_session() as db:
        account = _account(db)
        db.add_all([
            UsageSnapshot(account_id=account.id, taken_at=old, disk_home_bytes=20, inode_count=3, process_count=2),
            UsageSnapshot(account_id=account.id, taken_at=utcnow(), disk_home_bytes=5, inode_count=1, process_count=1),
            CommandRun(
                account_id=account.id, kind="wpcli", target="public_html", command_display="wp core version",
                status="completed", started_at=old, completed_at=old,
            ),
        ])
    with pytest.raises(ValidationError, match="audit_days"):
        retention.set_settings({"audit_days": retention.MINIMUMS["audit_days"] - 1})

    retention.set_settings({"usage_raw_days": 30, "task_output_days": 30, "investigation_hold": True})
    assert retention.prune({"batch_size": 25})["held"] is True
    with write_session() as db:
        assert db.scalar(select(func.count()).select_from(UsageSnapshot)) == 2

    retention.set_settings({"investigation_hold": False})
    result = retention.prune({"batch_size": 25})
    assert result["removed"]["usage_samples"] == 1
    assert result["removed"]["command_runs"] == 1
    with write_session() as db:
        samples = db.scalars(select(UsageSnapshot)).all()
        aggregate = db.scalar(select(UsageMonthlyAggregate))
        assert len(samples) == 1
        assert aggregate.sample_count == 1
        assert aggregate.disk_peak_bytes == 20


def test_retention_delete_transaction_is_bounded(isolated_db):
    old = utcnow() - dt.timedelta(days=60)
    with write_session() as db:
        db.add_all(LoginFailureEvent(bucket=f"bucket-{index}", occurred_at=old) for index in range(150))
    started = time.monotonic()
    removed = retention._delete_batch(
        LoginFailureEvent, LoginFailureEvent.occurred_at < utcnow(), batch_size=50,
    )
    elapsed = time.monotonic() - started
    assert removed == 50
    assert elapsed < 0.2
    with write_session() as db:
        assert db.scalar(select(func.count()).select_from(LoginFailureEvent)) == 100


def test_host_alert_cooldown_and_recovery(isolated_db, monkeypatch):
    monitoring.set_settings({"enabled": True, "channels": ["email"], "cooldown_minutes": 30})
    monkeypatch.setattr(monitoring, "_is_active", lambda _unit: True)
    state = {"problem": True}

    def host_metrics(_settings, _now, _db):
        return {"host.disk": {"problem": state["problem"], "event": "host.disk", "message": "disk state", "value": 4.0}}

    delivered = []
    monkeypatch.setattr(monitoring, "_host_metrics", host_metrics)
    monkeypatch.setattr(monitoring.events, "emit", lambda event, account, **context: delivered.append((event, context)) or {"email": True})

    assert "host.disk:down" in monitoring.check_services()["alerts_sent"]
    assert monitoring.check_services()["alerts_sent"] == []
    state["problem"] = False
    assert "host.disk:recovery" in monitoring.check_services()["alerts_sent"]
    assert [item[0] for item in delivered] == ["host.disk", "host.service_recovered"]


def test_monitoring_setup_warning_and_threshold_validation(isolated_db):
    current = monitoring.get_settings()
    assert current["delivery_ready"] is False
    assert current["setup_warning"]
    with pytest.raises(ValidationError):
        monitoring.set_settings({"memory_used_pct": 40})
    updated = monitoring.set_settings({"channels": ["email", "webhook"], "memory_used_pct": 90})
    assert updated["channels"] == ["email", "webhook"]
    assert updated["memory_used_pct"] == 90
    disabled = monitoring.set_settings({"channels": []})
    assert disabled["channels"] == []
    assert disabled["delivery_ready"] is False
    assert disabled["setup_warning"] == "Choose at least one alert channel. Host alerts are currently disabled."


def test_active_sessions_revoke_one_and_all_others(isolated_db):
    with write_session() as db:
        user = PanelUser(username="p8admin", password_hash="unused", role="admin")
        db.add(user)
        db.flush()
        user_id = user.id
    current = handlers_auth.create_session({"panel_user_id": user_id, "source_ip": "127.0.0.1", "user_agent": "Current browser"})
    other = handlers_auth.create_session({"panel_user_id": user_id, "source_ip": "192.0.2.10", "user_agent": "Other browser"})
    current_hash = session_digest(current["session_id"])
    listing = handlers_auth.list_sessions({"panel_user_id": user_id, "_current_session_hash": current_hash})
    assert len(listing["sessions"]) == 2
    assert next(row for row in listing["sessions"] if row["current"])["device"] == "Current browser"
    other_id = next(row["id"] for row in listing["sessions"] if not row["current"])
    handlers_auth.revoke_managed_session({"panel_user_id": user_id, "session_row_id": other_id, "_current_session_hash": current_hash})
    with pytest.raises(ValidationError, match="current session"):
        current_id = next(row["id"] for row in listing["sessions"] if row["current"])
        handlers_auth.revoke_managed_session({"panel_user_id": user_id, "session_row_id": current_id, "_current_session_hash": current_hash})
    replacement = handlers_auth.create_session({"panel_user_id": user_id})
    result = handlers_auth.revoke_other_sessions({"panel_user_id": user_id, "_current_session_hash": current_hash})
    assert result["count"] == 1
    with write_session() as db:
        assert db.scalar(select(Session.revoked).where(Session.session_id == session_digest(replacement["session_id"]))) is True


def test_admin_2fa_policy_cannot_lock_out_only_protected_admin(isolated_db):
    login_value = "Strong" + "Panel123!"
    with write_session() as db:
        user = PanelUser(username="owner", password_hash=hash_password(login_value), role="admin")
        db.add(user)
        db.flush()
        user_id = user.id
    with pytest.raises(ValidationError, match="at least one"):
        handlers_auth.security_policy_set({"require_admin_2fa": True})
    with write_session() as db:
        db.add(TotpCredential(panel_user_id=user_id, secret="encrypted-placeholder", enabled=True))
    policy = handlers_auth.security_policy_set({"require_admin_2fa": True, "admin_2fa_grace_days": 7})
    assert policy["require_admin_2fa"] is True
    with pytest.raises(ValidationError, match="at least one active administrator"):
        totp.disable_totp({"panel_user_id": user_id})


def test_admin_2fa_grace_and_root_break_glass(isolated_db, monkeypatch):
    owner_login = "Owner" + "Pass123!"
    peer_login = "Peer" + "Pass123!"
    with write_session() as db:
        owner = PanelUser(username="owner", password_hash=hash_password(owner_login), role="admin")
        peer = PanelUser(username="peer", password_hash=hash_password(peer_login), role="admin")
        db.add_all([owner, peer])
        db.flush()
        owner_id = owner.id
        db.add(TotpCredential(panel_user_id=owner.id, secret="encrypted-placeholder", enabled=True))
    handlers_auth.security_policy_set({"require_admin_2fa": True, "admin_2fa_grace_days": 1})
    during = handlers_auth.login_begin({"username": "peer", "password": peer_login})
    assert during["valid"] is True
    assert during["two_factor_setup_required"] is True
    with write_session() as db:
        policy = db.get(SecurityPolicySettings, 1)
        policy.admin_2fa_enabled_at = utcnow() - dt.timedelta(days=2)
    after = handlers_auth.login_begin({"username": "peer", "password": peer_login})
    assert after == {"valid": False, "two_factor_setup_required": True}

    monkeypatch.setattr(reset_admin_2fa.os, "geteuid", lambda: 0)
    monkeypatch.setattr(reset_admin_2fa.sys, "argv", ["reset_admin_2fa.py", "owner", "--confirm"])
    assert reset_admin_2fa.main() == 0
    with write_session() as db:
        assert db.scalar(select(TotpCredential).where(TotpCredential.panel_user_id == owner_id)) is None
        assert db.get(SecurityPolicySettings, 1).require_admin_2fa is False


def _request(peer: str) -> Request:
    return Request({"type": "http", "method": "GET", "scheme": "http", "path": "/metrics", "headers": [], "client": (peer, 1234), "server": ("localhost", 2222)})


def test_openmetrics_is_disabled_and_localhost_only(isolated_db, monkeypatch):
    monkeypatch.setattr(settings, "openmetrics_enabled", False)
    with pytest.raises(HTTPException) as disabled:
        metrics.openmetrics(_request("127.0.0.1"))
    assert disabled.value.status_code == 404
    monkeypatch.setattr(settings, "openmetrics_enabled", True)
    with pytest.raises(HTTPException) as remote:
        metrics.openmetrics(_request("192.0.2.20"))
    assert remote.value.status_code == 403
    response = metrics.openmetrics(_request("::1"))
    body = response.body.decode()
    assert "boron_host_memory_used_ratio" in body
    assert "username=" not in body
    assert "account=" not in body
