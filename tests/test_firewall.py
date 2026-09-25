import pytest

from daemon import firewall
from daemon.procutil import ProcResult
from shared.config import settings
from shared.validation import ValidationError

SHOW_ADDED_SAMPLE = """Added user rules (see 'ufw status' for running firewall):
ufw allow 22/tcp
ufw allow from 203.0.113.5 to any port 8443 proto tcp comment 'admin-office'
ufw deny 9999/udp comment 'blocked-scanner'
ufw allow from 198.51.100.42 comment 'boron-full-access-bypass-office'
"""

SHOW_ADDED_EMPTY = "Added user rules (see 'ufw status' for running firewall):\n(None)\n"


_REAL_SSH_PORTS = firewall._ssh_ports


@pytest.fixture(autouse=True)
def fixed_ssh_port(monkeypatch):
    # Deterministic across environments -- don't depend on this sandbox's
    # real /etc/ssh/sshd_config contents.
    monkeypatch.setattr(firewall, "_ssh_ports", lambda: {22})
    monkeypatch.setattr(firewall, "_sync_fail2ban_bypass", lambda *args, **kwargs: None)


def test_protected_ports_includes_ssh_panel_web_mail():
    ports = firewall.protected_ports()
    assert 22 in ports
    assert settings.api_bind_port in ports
    assert {80, 443, 25, 587, 993} <= ports


def test_parse_added_rules_plain_and_scoped_and_deny():
    rules = firewall._parse_added_rules(SHOW_ADDED_SAMPLE)
    assert len(rules) == 3
    plain = next(r for r in rules if r["port"] == 22)
    assert plain["action"] == "allow"
    assert plain["from"] == "any"
    assert plain["protocol"] == "tcp"

    scoped = next(r for r in rules if r["port"] == 8443)
    assert scoped["from"] == "203.0.113.5"
    assert scoped["protocol"] == "tcp"
    assert scoped["comment"] == "admin-office"

    deny = next(r for r in rules if r["port"] == 9999)
    assert deny["action"] == "deny"
    assert deny["protocol"] == "udp"


def test_parse_and_create_outbound_rule(monkeypatch):
    output = (
        "Added user rules (see 'ufw status' for running firewall):\n"
        "ufw deny out to 203.0.113.0/24 port 443 proto tcp comment 'restricted-api'\n"
    )
    parsed = firewall._parse_added_rules(output)
    assert parsed[0]["direction"] == "out"
    assert parsed[0]["to"] == "203.0.113.0/24"
    assert "backups" in parsed[0]["affected_services"]

    calls = []
    monkeypatch.setattr(firewall, "run", lambda args, timeout=20: calls.append(args) or ProcResult(args, 0, "", ""))
    result = firewall.add_rule({
        "action": "allow", "direction": "out", "port": 443,
        "protocol": "tcp", "to_addr": "203.0.113.0/24",
    })
    assert result["direction"] == "out"
    assert calls == [["ufw", "allow", "out", "to", "203.0.113.0/24", "port", "443", "proto", "tcp"]]


def test_outbound_rule_rejects_inbound_source_field():
    with pytest.raises(ValidationError, match="from_addr"):
        firewall.add_rule({
            "action": "allow", "direction": "out", "port": 443,
            "from_addr": "198.51.100.1",
        })


def test_parse_full_access_bypass_is_separate_from_port_rules():
    assert len(firewall._parse_added_rules(SHOW_ADDED_SAMPLE)) == 3
    assert firewall._parse_bypass_rules(SHOW_ADDED_SAMPLE) == [{
        "bypass_id": firewall._bypass_id("198.51.100.42"),
        "address": "198.51.100.42",
        "label": "office",
    }]


def test_add_and_delete_full_access_bypass(monkeypatch):
    added = "Added user rules (see 'ufw status' for running firewall):\nufw allow from 198.51.100.42 comment 'boron-full-access-bypass-office'\n"
    calls = []

    def fake_run(args, timeout=20):
        calls.append(args)
        output = added if args[:3] == ["ufw", "show", "added"] and len(calls) > 2 else SHOW_ADDED_EMPTY
        return ProcResult(args=args, returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(firewall, "run", fake_run)
    result = firewall.add_bypass({"address": "198.51.100.42", "label": "office"})
    assert result["address"] == "198.51.100.42"
    assert ["ufw", "insert", "1", "allow", "from", "198.51.100.42", "comment", "boron-full-access-bypass-office"] in calls
    firewall.delete_bypass({"bypass_id": result["bypass_id"]})
    assert ["ufw", "--force", "delete", "allow", "from", "198.51.100.42", "comment", "boron-full-access-bypass-office"] in calls


def test_bypass_rejects_global_network_and_duplicate(monkeypatch):
    with pytest.raises(ValidationError, match="global network"):
        firewall.add_bypass({"address": "0.0.0.0/0"})
    monkeypatch.setattr(
        firewall, "run",
        lambda args, timeout=20: ProcResult(
            args=args, returncode=0,
            stdout="Added user rules (see 'ufw status' for running firewall):\nufw allow from 198.51.100.42 comment 'boron-full-access-bypass'\n",
            stderr="",
        ),
    )
    with pytest.raises(ValidationError, match="already has"):
        firewall.add_bypass({"address": "198.51.100.42"})


def test_parse_added_rules_empty():
    assert firewall._parse_added_rules(SHOW_ADDED_EMPTY) == []


def test_parse_added_rules_marks_protected_ports():
    rules = firewall._parse_added_rules("Added user rules (see 'ufw status' for running firewall):\nufw allow 22/tcp\nufw allow 8080/tcp\n")
    protected = {r["port"]: r["protected"] for r in rules}
    assert protected[22] is True
    assert protected[8080] is False


def test_add_rule_rejects_deny_on_protected_port(monkeypatch):
    monkeypatch.setattr(firewall, "run", lambda args, timeout=20: ProcResult(args=args, returncode=0, stdout="", stderr=""))
    with pytest.raises(ValidationError):
        firewall.add_rule({"action": "deny", "port": 22})
    with pytest.raises(ValidationError):
        firewall.add_rule({"action": "deny", "port": 443})


def test_add_rule_allows_deny_on_unprotected_port(monkeypatch):
    calls = []

    def fake_run(args, timeout=20):
        calls.append(args)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(firewall, "run", fake_run)
    result = firewall.add_rule({"action": "deny", "port": 31337, "protocol": "tcp"})
    assert result["action"] == "deny"
    assert calls == [["ufw", "deny", "31337/tcp"]]


def test_add_rule_allows_allow_on_protected_port(monkeypatch):
    monkeypatch.setattr(firewall, "run", lambda args, timeout=20: ProcResult(args=args, returncode=0, stdout="", stderr=""))
    result = firewall.add_rule({"action": "allow", "port": 22, "protocol": "tcp"})
    assert result["action"] == "allow"


def test_add_rule_with_scoped_source_builds_correct_args(monkeypatch):
    calls = []

    def fake_run(args, timeout=20):
        calls.append(args)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(firewall, "run", fake_run)
    firewall.add_rule({"action": "allow", "port": 8443, "protocol": "tcp", "from_addr": "203.0.113.5", "comment": "office"})
    assert calls == [["ufw", "allow", "from", "203.0.113.5", "to", "any", "port", "8443", "proto", "tcp", "comment", "office"]]


def test_add_rule_rejects_invalid_port():
    with pytest.raises(ValidationError):
        firewall.add_rule({"action": "allow", "port": 99999})
    with pytest.raises(ValidationError):
        firewall.add_rule({"action": "allow", "port": "not-a-port"})


def test_add_rule_rejects_bad_protocol():
    with pytest.raises(ValidationError):
        firewall.add_rule({"action": "allow", "port": 8080, "protocol": "icmp"})


def test_add_rule_rejects_bad_from_addr():
    with pytest.raises(ValidationError):
        firewall.add_rule({"action": "allow", "port": 8080, "from_addr": "not-an-ip"})


def test_add_rule_rejects_comment_with_shell_metacharacters():
    with pytest.raises(ValidationError):
        firewall.add_rule({"action": "allow", "port": 8080, "comment": "hi; rm -rf /"})


def test_delete_rule_rejects_last_allow_for_protected_port(monkeypatch):
    monkeypatch.setattr(
        firewall, "run",
        lambda args, timeout=20: ProcResult(args=args, returncode=0, stdout=SHOW_ADDED_SAMPLE if args[1] == "show" else "", stderr=""),
    )
    plain_rule_id = firewall._rule_id("allow", 22, "any", "any")
    with pytest.raises(ValidationError):
        firewall.delete_rule({"rule_id": plain_rule_id})


def test_delete_rule_allows_when_another_allow_remains(monkeypatch):
    two_ssh_rules = (
        "Added user rules (see 'ufw status' for running firewall):\n"
        "ufw allow 22/tcp\n"
        "ufw allow 22/udp\n"
    )
    calls = []

    def fake_run(args, timeout=20):
        calls.append(args)
        if args[:2] == ["ufw", "show"]:
            return ProcResult(args=args, returncode=0, stdout=two_ssh_rules, stderr="")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(firewall, "run", fake_run)
    rule_id = firewall._rule_id("allow", 22, "tcp", "any")
    result = firewall.delete_rule({"rule_id": rule_id})
    assert result["status"] == "deleted"


def test_delete_rule_unknown_id_raises(monkeypatch):
    monkeypatch.setattr(firewall, "run", lambda args, timeout=20: ProcResult(args=args, returncode=0, stdout=SHOW_ADDED_EMPTY, stderr=""))
    with pytest.raises(ValidationError):
        firewall.delete_rule({"rule_id": "nonexistent"})


def test_delete_rule_allows_deleting_unprotected_rule(monkeypatch):
    calls = []

    def fake_run(args, timeout=20):
        calls.append(args)
        if args[:2] == ["ufw", "show"]:
            return ProcResult(args=args, returncode=0, stdout=SHOW_ADDED_SAMPLE, stderr="")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(firewall, "run", fake_run)
    rule_id = firewall._rule_id("deny", 9999, "udp", "any")
    result = firewall.delete_rule({"rule_id": rule_id})
    assert result["status"] == "deleted"
    assert ["ufw", "--force", "delete", "deny", "9999/udp"] in calls


def test_enable_firewall_requires_confirm(monkeypatch):
    monkeypatch.setattr(firewall, "run", lambda args, timeout=20: ProcResult(args=args, returncode=0, stdout="Status: active\n", stderr=""))
    with pytest.raises(ValidationError):
        firewall.enable_firewall({})


def test_enable_firewall_ensures_baseline_rules_first(monkeypatch):
    calls = []

    def fake_run(args, timeout=20):
        calls.append(args)
        if args[:2] == ["ufw", "show"]:
            return ProcResult(args=args, returncode=0, stdout=SHOW_ADDED_EMPTY, stderr="")
        if args[:2] == ["ufw", "status"]:
            return ProcResult(args=args, returncode=0, stdout="Status: active\n", stderr="")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(firewall, "run", fake_run)
    result = firewall.enable_firewall({"confirm": True})
    assert result["active"] is True
    baseline_calls = [c for c in calls if c[:2] == ["ufw", "allow"]]
    baseline_ports = {int(c[2]) for c in baseline_calls}
    assert firewall.protected_ports() <= baseline_ports
    assert ["ufw", "--force", "enable"] in calls


def test_enable_firewall_skips_already_covered_ports(monkeypatch):
    already_covered = "Added user rules (see 'ufw status' for running firewall):\nufw allow 22/tcp\n"
    calls = []

    def fake_run(args, timeout=20):
        calls.append(args)
        if args[:2] == ["ufw", "show"]:
            return ProcResult(args=args, returncode=0, stdout=already_covered, stderr="")
        if args[:2] == ["ufw", "status"]:
            return ProcResult(args=args, returncode=0, stdout="Status: active\n", stderr="")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(firewall, "run", fake_run)
    firewall.enable_firewall({"confirm": True})
    baseline_calls = [c for c in calls if c[:2] == ["ufw", "allow"]]
    baseline_ports = [int(c[2]) for c in baseline_calls]
    assert baseline_ports.count(22) == 0  # already covered, not re-added


def test_outbound_allow_does_not_satisfy_inbound_protected_baseline(monkeypatch):
    outbound_only = (
        "Added user rules (see 'ufw status' for running firewall):\n"
        "ufw allow out to any port 22 proto tcp\n"
    )
    calls = []

    def fake_run(args, timeout=20):
        calls.append(args)
        if args[:2] == ["ufw", "show"]:
            return ProcResult(args=args, returncode=0, stdout=outbound_only, stderr="")
        if args[:2] == ["ufw", "status"]:
            return ProcResult(args=args, returncode=0, stdout="Status: active\n", stderr="")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(firewall, "run", fake_run)
    firewall.enable_firewall({"confirm": True})
    assert ["ufw", "allow", "22", "comment", "boron-baseline-protected-port"] in calls


def test_disable_firewall_requires_confirm(monkeypatch):
    monkeypatch.setattr(firewall, "run", lambda args, timeout=20: ProcResult(args=args, returncode=0, stdout="Status: inactive\n", stderr=""))
    with pytest.raises(ValidationError):
        firewall.disable_firewall({})


def test_disable_firewall_with_confirm(monkeypatch):
    monkeypatch.setattr(firewall, "run", lambda args, timeout=20: ProcResult(args=args, returncode=0, stdout="Status: inactive\n", stderr=""))
    result = firewall.disable_firewall({"confirm": True})
    assert result["active"] is False


def test_get_status_parses_active_vs_inactive(monkeypatch):
    monkeypatch.setattr(firewall, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout="Status: active\n", stderr=""))
    assert firewall.get_status({})["active"] is True

    monkeypatch.setattr(firewall, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout="Status: inactive\n", stderr=""))
    assert firewall.get_status({})["active"] is False


def test_add_rule_raises_on_ufw_failure(monkeypatch):
    monkeypatch.setattr(firewall, "run", lambda args, timeout=20: ProcResult(args=args, returncode=1, stdout="", stderr="ERROR"))
    with pytest.raises(RuntimeError):
        firewall.add_rule({"action": "allow", "port": 8080})



def test_failed_baseline_rule_prevents_firewall_enable(monkeypatch):
    calls = []
    def failed(args, **kwargs):
        calls.append(args)
        if args[:2] == ['ufw', 'allow']:
            return ProcResult(args, 1, '', 'baseline rule failed')
        return ProcResult(args, 0, '', '')
    monkeypatch.setattr(firewall, 'run', failed)
    with pytest.raises(RuntimeError, match='baseline rule failed'):
        firewall.enable_firewall({'confirm': True})
    assert not any('enable' in args for args in calls)


def test_failed_cf_restore_does_not_remove_existing_scoped_rules(monkeypatch):
    calls = []
    def failed(args, **kwargs):
        calls.append(args)
        assert args[:2] == ['ufw', 'allow']
        return ProcResult(args, 1, '', 'restore failed')
    monkeypatch.setattr(firewall, 'run', failed)
    with pytest.raises(RuntimeError, match='restore failed'):
        firewall.remove_cf_lockdown()
    assert len(calls) == 1



def test_effective_ssh_ports_preserve_multiple_configured_listeners(monkeypatch):
    from daemon import procutil
    monkeypatch.setattr(procutil, 'run', lambda args, **kwargs: ProcResult(args, 0, 'port 22222\nport 2200\n', ''))
    assert _REAL_SSH_PORTS() == {22222, 2200}


def test_unknown_effective_ssh_ports_fail_closed(monkeypatch):
    from daemon import procutil
    monkeypatch.setattr(procutil, 'run', lambda args, **kwargs: ProcResult(args, 1, '', 'invalid config'))
    with pytest.raises(RuntimeError, match='effective SSH'):
        _REAL_SSH_PORTS()


@pytest.fixture()
def isolated_firewall_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "firewall_state_dir", str(tmp_path / "firewall"))
    monkeypatch.setattr(firewall, "_arm_rollback", lambda state: None)
    monkeypatch.setattr(firewall, "_verify_protected_access", lambda: None)
    firewall._clear_pending()
    yield
    firewall._clear_pending()


def test_transaction_keeps_confirmation_secret_out_of_journal(
    isolated_firewall_journal, monkeypatch
):
    monkeypatch.setattr(
        firewall,
        "add_rule",
        lambda params: {"rule_id": "new-rule", "action": params["action"]},
    )
    result = firewall.transactional_add_rule(
        {"action": "allow", "port": 8443, "protocol": "tcp"}
    )
    token = result["pending_change"]["confirmation_token"]
    journal = firewall._state_file().read_text()
    assert token not in journal
    assert "token_sha256" in journal

    with pytest.raises(ValidationError, match="invalid"):
        firewall.confirm_change({"confirmation_token": "wrong"})
    assert firewall._state_file().exists()

    assert firewall.confirm_change({"confirmation_token": token})["status"] == "confirmed"
    assert not firewall._state_file().exists()


def test_transaction_reverts_explicitly_and_on_daemon_restart(
    isolated_firewall_journal, monkeypatch
):
    undone = []
    monkeypatch.setattr(firewall, "add_rule", lambda params: {"rule_id": "new-rule"})
    monkeypatch.setattr(firewall, "_undo_one", lambda step: undone.append(step))

    firewall.transactional_add_rule({"action": "allow", "port": 8443})
    result = firewall.rollback_change({"reason": "administrator requested"})
    assert result["status"] == "reverted"
    assert undone[-1]["kind"] == "delete_rule"

    firewall.transactional_add_rule({"action": "allow", "port": 9443})
    result = firewall.recover_pending_changes()
    assert result["status"] == "reverted"
    assert len(undone) == 2
    assert not firewall._state_file().exists()


def test_failed_transaction_is_rolled_back_and_does_not_block_retry(
    isolated_firewall_journal, monkeypatch
):
    undone = []
    monkeypatch.setattr(firewall, "_undo_one", lambda step: undone.append(step))
    monkeypatch.setattr(
        firewall,
        "add_rule",
        lambda params: (_ for _ in ()).throw(RuntimeError("ufw failed")),
    )
    with pytest.raises(RuntimeError, match="ufw failed"):
        firewall.transactional_add_rule({"action": "allow", "port": 8443})
    assert undone and undone[0]["kind"] == "delete_rule"
    assert not firewall._state_file().exists()


def test_only_one_unconfirmed_change_can_exist(isolated_firewall_journal, monkeypatch):
    monkeypatch.setattr(firewall, "add_rule", lambda params: {"rule_id": "new-rule"})
    firewall.transactional_add_rule({"action": "allow", "port": 8443})
    with pytest.raises(ValidationError, match="pending firewall change"):
        firewall.transactional_add_rule({"action": "allow", "port": 9443})


def test_temporary_ban_refuses_bypass_and_current_admin(monkeypatch):
    monkeypatch.setattr(
        firewall, "run",
        lambda args, timeout=15: ProcResult(args, 0, (
            "Added user rules (see 'ufw status' for running firewall):\n"
            "ufw allow from 198.51.100.0/24 comment 'boron-full-access-bypass-office'\n"
        ), ""),
    )
    with pytest.raises(ValidationError, match="overlaps"):
        firewall.assert_address_can_be_banned("198.51.100.42")
    with pytest.raises(ValidationError, match="administrator"):
        firewall.assert_address_can_be_banned("203.0.113.0/24", "203.0.113.8")


def test_temporary_ban_is_durable_and_removable(isolated_db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        firewall, "run",
        lambda args, timeout=20: calls.append(args) or ProcResult(args, 0, SHOW_ADDED_EMPTY, ""),
    )
    added = firewall.add_temporary_ban({
        "value": "203.0.113.44", "duration_minutes": 15,
        "reason": "test", "actor": "admin", "actor_ip": "192.0.2.4",
    })
    assert added["value"] == "203.0.113.44"
    assert any(call[:6] == ["ufw", "insert", "1", "deny", "from", "203.0.113.44"] for call in calls)
    result = firewall.delete_temporary_ban({"id": added["id"]})
    assert result["status"] == "unbanned"


def test_firewall_configuration_round_trip_and_protected_port_validation(monkeypatch):
    protected = sorted(firewall.protected_ports())
    current = "Added user rules (see 'ufw status' for running firewall):\n" + "".join(
        f"ufw allow {port}/tcp comment 'baseline'\n" for port in protected
    )
    monkeypatch.setattr(
        firewall, "run",
        lambda args, timeout=15: ProcResult(args, 0, current if args[:3] == ["ufw", "show", "added"] else "", ""),
    )
    exported = firewall.export_configuration({})
    assert exported["format"] == "boron-firewall" and exported["version"] == 1
    preview = firewall.preview_configuration_import({"configuration": exported, "replace": True})
    assert preview["summary"] == {
        "rules_to_add": 0, "rules_to_delete": 0, "bypass_to_add": 0, "bypass_to_delete": 0,
    }
    exported["rules"] = []
    with pytest.raises(ValidationError, match="protected ports"):
        firewall.preview_configuration_import({"configuration": exported, "replace": True})
