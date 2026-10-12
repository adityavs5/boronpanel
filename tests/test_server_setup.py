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
    server_setup._record(1, "completed", {"status": "completed"})
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
    for step in range(1, 5):
        server_setup._record(step, "completed", {"status": "completed"})
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
    for step in range(1, 7):
        server_setup._record(step, "completed", {"status": "completed"})
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
        server_setup._step13({})


def test_public_result_filter_redacts_nested_credentials():
    assert server_setup._public({
        "name": "provider", "api_token": "secret", "nested": {"password": "secret", "ok": True},
    }) == {"name": "provider", "nested": {"ok": True}}


def test_setup_progress_reconciles_legacy_out_of_order_steps():
    row = ServerSetupState(id=1, current_step=7, completed=False, step_results={'1': {'state': 'completed'}, '2': {'state': 'completed'}, '5': {'state': 'completed'}})
    state = server_setup._state_dict(row)
    assert state['current_step'] == 3
    assert state['completed_steps'] == 3


def test_wizard_blocks_out_of_order_step_and_names_dependency(isolated_db):
    with pytest.raises(ValidationError, match=r"step 1 \(Server identity\)"):
        server_setup.run_step({"step": 2, "mode": "local", "confirm": False})


@pytest.mark.parametrize(
    ("step", "missing_number", "missing_name"),
    [
        (7, 6, "TLS certificates"),
        (8, 7, "MaxMind GeoLite2"),
        (10, 9, "Choose PHP versions"),
        (12, 11, "Notification sender"),
    ],
)
def test_later_wizard_steps_cannot_leave_numbered_gaps(
    isolated_db, step, missing_number, missing_name,
):
    for completed in range(1, missing_number):
        server_setup._record(completed, "completed", {"status": "completed"})
    with pytest.raises(
        ValidationError,
        match=rf"step {missing_number} \({missing_name}\)",
    ):
        server_setup.run_step({"step": step, "skip": True})


def test_completed_legacy_wizard_remains_complete():
    results = {str(step): {"state": "completed"} for step in range(1, 10)}
    row = ServerSetupState(id=1, current_step=9, completed=True, step_results=results)
    state = server_setup._state_dict(row)
    assert state["completed"] is True
    assert state["legacy_completed"] is True
    assert state["current_step"] == 13


def test_completed_legacy_wizard_can_finish_without_new_steps(isolated_db, monkeypatch):
    results = {str(step): {"state": "completed"} for step in range(1, 10)}
    with write_session() as db:
        db.add(ServerSetupState(id=1, current_step=9, completed=True, step_results=results))
    monkeypatch.setattr(server_setup, "status", lambda _params=None: {"completed": True})

    result = server_setup.run_step({"step": 13})

    assert result["legacy_completion_preserved"] is True
    with write_session() as db:
        row = db.get(ServerSetupState, 1)
        assert row.completed is True
        assert row.step_results["13"]["state"] == "completed"


def test_php_step_waits_for_missing_packages(monkeypatch):
    monkeypatch.setattr(server_setup.stack_manager, "inventory", lambda _params: {
        "components": {"php": {"targets": [
            {"target": "8.3", "installed": True},
            {"target": "8.4", "installed": False},
        ]}}
    })
    result, draft = server_setup._step9({"versions": ["8.3", "8.4"], "default": "8.3"})
    assert result["state"] == "waiting_for_packages"
    assert result["missing"] == ["8.4"]
    assert draft["selected_php_versions"] == ["8.3", "8.4"]


def test_php_step_applies_only_installed_supported_versions(monkeypatch):
    monkeypatch.setattr(server_setup.stack_manager, "inventory", lambda _params: {
        "components": {"php": {"targets": [
            {"target": "8.3", "installed": True},
            {"target": "8.4", "installed": True},
        ]}}
    })
    applied = []
    monkeypatch.setattr(server_setup.panel_config, "apply_setup_values", lambda values: applied.append(values) or {"status": "completed"})

    result, _draft = server_setup._step9({"versions": ["8.4", "8.3"], "default": "8.4"})

    assert result["selected"] == ["8.3", "8.4"]
    assert applied == [{"php_versions": ["8.3", "8.4"], "default_php_version": "8.4"}]


def test_tuning_requires_preview_and_confirmation(monkeypatch):
    monkeypatch.setattr(server_setup.resource_tuning, "preview", lambda params: {
        "recommendation": {"memory_gb": 4}, "changes": [], "requires_confirmation": True,
    })
    preview, _draft = server_setup._step10({"confirm": False})
    assert preview["state"] == "preview"
    monkeypatch.setattr(server_setup.resource_tuning, "apply", lambda params: {
        "status": "applied", "recommendation": {"memory_gb": 4},
    })
    applied, draft = server_setup._step10({"confirm": True})
    assert applied["status"] == "applied"
    assert draft["resource_tuning"]["memory_gb"] == 4


def test_optional_notification_and_backup_steps_keep_visible_warnings():
    notification, notification_draft = server_setup._step11({"skip": True})
    backup, backup_draft = server_setup._step12({"skip": True})
    assert notification["skipped"] and "disabled" in notification["warning"]
    assert backup["skipped"] and "not protected" in backup["warning"]
    assert notification_draft["notification_sender_skipped"] is True
    assert backup_draft["backup_destination_skipped"] is True


def test_notification_and_backup_steps_apply_selected_values(monkeypatch):
    monkeypatch.setattr(server_setup.notifications, "set_settings", lambda values: {"sender_address": values["sender_address"]})
    monkeypatch.setattr(server_setup.snapshot_jobs, "initialize_destination", lambda values: {"id": values["id"], "status": "ready"})

    notification, notification_draft = server_setup._step11({"sender_address": "alerts@example.test"})
    backup, backup_draft = server_setup._step12({"destination_id": "42"})

    assert notification["sender_address"] == "alerts@example.test"
    assert notification_draft["notification_sender_configured"] is True
    assert backup == {"destination": {"id": 42, "status": "ready"}, "tested": True}
    assert backup_draft["backup_destination_id"] == 42
