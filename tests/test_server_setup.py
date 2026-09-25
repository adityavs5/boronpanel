import tomllib
from pathlib import Path

import pytest

from daemon import panel_config, server_setup
from shared.db import write_session
from shared.models import Account, DnsZone, ServerSetupState
from shared.validation import ValidationError


def test_render_setup_values_preserves_unrelated_config_and_comments():
    source = '# operator note\napi_bind_port = 2222\npanel_hostname = "old.example.test" # keep\n'
    result = panel_config.render_setup_values(source, {
        "panel_hostname": "panel.example.test", "server_public_ip": "192.0.2.20",
        "letsencrypt_email": "admin@example.test",
    })
    parsed = tomllib.loads(result)
    assert parsed["api_bind_port"] == 2222
    assert parsed["panel_hostname"] == "panel.example.test"
    assert parsed["server_public_ip"] == "192.0.2.20"
    assert '# operator note' in result and '# keep' in result


def test_wizard_step_state_is_durable_and_excludes_secrets(isolated_db, monkeypatch):
    monkeypatch.setattr(server_setup.panel_config, "apply_setup_values", lambda values, **kwargs: {"status": "completed"})
    monkeypatch.setattr(server_setup, "status", lambda _params=None: {"current_step": 2})
    result = server_setup.run_step({
        "step": 1, "panel_hostname": "panel.example.test", "server_public_ip": "192.0.2.20",
        "contact_email": "admin@example.test", "api_token": "must-not-persist",
    })
    assert result["step"] == 1
    with write_session() as db:
        row = db.get(ServerSetupState, 1)
        assert row.current_step == 2
        assert row.draft["panel_hostname"] == "panel.example.test"
        assert "must-not-persist" not in repr(row.draft)
        assert row.step_results["1"]["state"] == "completed"


def test_dns_preview_does_not_replace_completed_step_result(isolated_db, monkeypatch):
    server_setup._record(2, "completed", {"mode": "local"}, draft={"dns_mode": "local"})
    monkeypatch.setattr(server_setup, "status", lambda _params=None: {})
    result = server_setup.run_step({
        "step": 2, "mode": "local", "local_nameservers": ["ns1.example.test", "ns2.example.test"],
        "confirm": False,
    })
    assert result["state"] == "preview"
    with write_session() as db:
        assert db.get(ServerSetupState, 1).step_results["2"]["state"] == "completed"


def test_service_record_preview_marks_external_and_conflicting_records(isolated_db, monkeypatch):
    with write_session() as db:
        account = Account(username="dnsowner", status="active", primary_domain="example.test")
        db.add(account); db.flush(); db.add(DnsZone(account_id=account.id, zone="example.test"))
    monkeypatch.setattr(server_setup.dnsprovider, "list_records", lambda zone: [
        {"name": "panel.example.test", "type": "A", "ttl": 300, "values": ["192.0.2.99"]},
    ])
    plan = server_setup.service_records_preview({
        "panel_hostname": "panel.example.test", "webmail_hostname": "mail.external.test",
        "server_public_ip": "192.0.2.20",
    })
    panel = next(row for row in plan["records"] if row["purpose"] == "panel_hostname")
    external = next(row for row in plan["records"] if row["purpose"] == "webmail_hostname")
    assert panel["state"] == "conflict" and panel["proxied"] is False
    assert external["state"] == "external" and external["zone"] is None
    assert plan["panel_dns_only"] is True


def test_apply_service_records_requires_explicit_conflict_replacement(isolated_db, monkeypatch):
    with write_session() as db:
        account = Account(username="dnsowner", status="active", primary_domain="example.test")
        db.add(account); db.flush(); db.add(DnsZone(account_id=account.id, zone="example.test"))
    monkeypatch.setattr(server_setup.dnsprovider, "list_records", lambda zone: [
        {"name": "panel.example.test", "type": "A", "ttl": 300, "values": ["192.0.2.99"]},
    ])
    params = {"panel_hostname": "panel.example.test", "server_public_ip": "192.0.2.20"}
    with pytest.raises(ValidationError, match="conflict"):
        server_setup._apply_service_records(params)
    calls = []
    monkeypatch.setattr(server_setup.dnsprovider, "upsert_record", lambda *args, **kwargs: calls.append((args, kwargs)))
    result = server_setup._apply_service_records({**params, "replace_conflicts": True})
    assert result["applied"] == ["panel.example.test"]
    assert calls[0][1]["proxied"] is False


def test_dns_verification_waiting_state_is_resumable(isolated_db, monkeypatch):
    monkeypatch.setattr(server_setup, "_resolve_host", lambda hostname, rtype: [])
    monkeypatch.setattr(server_setup, "status", lambda _params=None: {"current_step": 1})
    result = server_setup.run_step({
        "step": 5, "panel_hostname": "panel.example.test", "server_public_ip": "192.0.2.20",
    })
    assert result["state"] == "waiting_for_external_dns" and result["ready"] is False
    with write_session() as db:
        row = db.get(ServerSetupState, 1)
        assert row.step_results["5"]["state"] == "waiting_for_external_dns"


def test_maxmind_skip_completes_without_a_license_or_database(isolated_db, monkeypatch):
    monkeypatch.setattr(server_setup.geoip, "is_configured", lambda: False)
    monkeypatch.setattr(server_setup, "status", lambda _params=None: {})
    result = server_setup.run_step({"step": 7, "skip": True})
    assert result["skipped"] is True
    with write_session() as db:
        row = db.get(ServerSetupState, 1)
        assert row.maxmind_skipped is True
        assert row.step_results["7"]["state"] == "completed"


def test_finish_refuses_incomplete_wizard(isolated_db):
    with pytest.raises(ValidationError, match="Complete setup steps"):
        server_setup._step8({})


def test_public_result_filter_redacts_nested_credentials():
    assert server_setup._public({
        "name": "provider", "api_token": "secret", "nested": {"password": "secret", "ok": True},
    }) == {"name": "provider", "nested": {"ok": True}}
