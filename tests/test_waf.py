import pytest

from daemon import waf
from daemon.procutil import ProcResult
from shared.db import write_session
from shared.models import Account, Domain, WafCustomRule, WafDomainOverride, WafException, WafSettings
from shared.validation import ValidationError


@pytest.fixture(autouse=True)
def isolated_crs_files(tmp_path, monkeypatch):
    setup=tmp_path/'crs/crs-setup.conf';setup.parent.mkdir();setup.write_text('# setup\n')
    rules=tmp_path/'rules';rules.mkdir();(rules/'rule.conf').write_text('# rule\n')
    monkeypatch.setattr(waf,'CRS_SETUP',setup)
    monkeypatch.setattr(waf,'CRS_RULES',rules)
    monkeypatch.setattr(waf,'WAF_RULES_FILE',str(tmp_path/'modsec_includes.conf'))

# Captured live from this server's own real ModSecurity audit log (a
# genuine blocked XSS + SQLi probe against a real vhost,
# docs/CHECKPOINT-phase5-7-waf.md) -- parsed against the real format.
REAL_AUDIT_LOG_SAMPLE = """---QFUgjGHE---A--
[03/Jul/2026:18:13:40 +0000] 178310242058.385654 104.234.179.64 59672 webmail.104-234-179-64.sslip.io 443
---QFUgjGHE---B--
GET /?q=<script>alert(1)</script> HTTP/1.1
accept: */*
host: webmail.104-234-179-64.sslip.io
user-agent: curl/8.5.0

---QFUgjGHE---F--
HTTP/1.1 403
content-type: text/html

---QFUgjGHE---H--
ModSecurity: Warning. detected XSS using libinjection. [id "941100"] [msg "XSS Attack Detected via libinjection"]
ModSecurity: Access denied with code 403 (phase 2). Matched "Operator `Ge' with parameter `5' against variable `TX:ANOMALY_SCORE' (Value: `15' ) [id "949110"] [msg "Inbound Anomaly Score Exceeded (Total Score: 15)"]

---QFUgjGHE---J--

---QFUgjGHE---K--

---QFUgjGHE---Z--

---AvoGc53z---A--
[03/Jul/2026:18:14:17 +0000] 17831024570.671526 104.234.179.64 51874 webmail.104-234-179-64.sslip.io 443
---AvoGc53z---B--
GET /?id=1%27+UNION+SELECT HTTP/1.1
accept: */*
host: another.example.com
user-agent: curl/8.5.0

---AvoGc53z---F--
HTTP/1.1 403

---AvoGc53z---H--
ModSecurity: Warning. detected SQLi using libinjection. [id "942100"] [msg "SQL Injection Attack Detected via libinjection"]
ModSecurity: Access denied with code 403 (phase 2). Matched "Operator `Ge' with parameter `5' against variable `TX:ANOMALY_SCORE' (Value: `20' ) [id "949110"] [msg "Inbound Anomaly Score Exceeded (Total Score: 20)"]

---AvoGc53z---J--

---AvoGc53z---K--

---AvoGc53z---Z--
"""


def _add_domain(name="example.com"):
    with write_session() as session:
        account = Account(username="wafowner", primary_domain=name)
        session.add(account)
        session.flush()
        session.add(Domain(account_id=account.id, domain=name, kind="primary", docroot="/home/wafowner/public_html"))


def test_parse_audit_log_extracts_both_transactions():
    events = waf._parse_audit_log(REAL_AUDIT_LOG_SAMPLE)
    assert len(events) == 2
    first = events[0]
    assert first["client_ip"] == "104.234.179.64"
    assert first["host"] == "webmail.104-234-179-64.sslip.io"
    assert first["method"] == "GET"
    assert first["status"] == 403
    assert "Anomaly Score Exceeded" in first["message"]


def test_parse_audit_log_second_transaction_different_host():
    events = waf._parse_audit_log(REAL_AUDIT_LOG_SAMPLE)
    assert events[1]["host"] == "another.example.com"


def test_list_blocked_requests_filters_by_domain(monkeypatch):
    monkeypatch.setattr(waf, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=REAL_AUDIT_LOG_SAMPLE, stderr=""))
    result = waf.list_blocked_requests({"domain": "another.example.com"})
    assert len(result["events"]) == 1
    assert result["events"][0]["host"] == "another.example.com"


def test_list_blocked_requests_newest_first(monkeypatch):
    monkeypatch.setattr(waf, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=REAL_AUDIT_LOG_SAMPLE, stderr=""))
    result = waf.list_blocked_requests({})
    assert len(result["events"]) == 2
    assert result["events"][0]["host"] == "another.example.com"  # last in log, first in output


def test_is_available_checks_real_module_path(monkeypatch, tmp_path):
    fake_module = tmp_path / "mod_security.so"
    monkeypatch.setattr(waf, "MODSEC_MODULE_PATH", str(fake_module))
    assert waf.is_available() is False
    fake_module.write_text("x")
    assert waf.is_available() is True


def test_get_status_default_disabled(isolated_db, monkeypatch):
    monkeypatch.setattr(waf, "is_available", lambda: True)
    status = waf.get_status({})
    assert status["available"] is True
    assert status["enabled"] is False
    assert status["domain_overrides"] == []
    assert status["custom_rules"] == []


def test_set_enabled_requires_module_available(isolated_db, monkeypatch):
    monkeypatch.setattr(waf, "is_available", lambda: False)
    with pytest.raises(ValidationError):
        waf.set_enabled({"enabled": True})


def test_set_enabled_persists_and_refreshes_config(isolated_db, monkeypatch):
    monkeypatch.setattr(waf, "is_available", lambda: True)
    calls = []
    monkeypatch.setattr(waf.ols, "refresh_main_config", lambda: calls.append("refreshed"))

    result = waf.set_enabled({"enabled": True})
    assert result["enabled"] is True
    assert calls == ["refreshed"]

    with write_session() as session:
        row = session.get(WafSettings, 1)
        assert row.enabled is True


def test_set_domain_override_adds_and_removes(isolated_db, monkeypatch):
    monkeypatch.setattr(waf.ols, "refresh_main_config", lambda: None)
    _add_domain()
    waf.set_domain_override({"domain": "example.com", "disabled": True})
    with write_session() as session:
        assert session.query(WafDomainOverride).filter_by(domain="example.com").count() == 1

    waf.set_domain_override({"domain": "example.com", "disabled": False})
    with write_session() as session:
        assert session.query(WafDomainOverride).filter_by(domain="example.com").count() == 0


def test_set_domain_override_rejects_bad_domain(isolated_db, monkeypatch):
    monkeypatch.setattr(waf.ols, "refresh_main_config", lambda: None)
    with pytest.raises(ValidationError):
        waf.set_domain_override({"domain": "not a domain!!", "disabled": True})


def test_add_custom_rule_rejects_unlisted_target(isolated_db, monkeypatch):
    monkeypatch.setattr(waf.ols, "refresh_main_config", lambda: None)
    with pytest.raises(ValidationError):
        waf.add_custom_rule({"domain": "example.com", "target": "REMOTE_ADDR", "pattern": "x"})


@pytest.mark.parametrize("bad_pattern", ["has`backtick", 'has"quote', "has\nnewline", "x" * 301])
def test_add_custom_rule_rejects_injection_characters(isolated_db, monkeypatch, bad_pattern):
    monkeypatch.setattr(waf.ols, "refresh_main_config", lambda: None)
    with pytest.raises(ValidationError):
        waf.add_custom_rule({"domain": "example.com", "target": "ARGS", "pattern": bad_pattern})


def test_add_custom_rule_accepts_valid_input(isolated_db, monkeypatch):
    calls = []
    monkeypatch.setattr(waf.ols, "refresh_main_config", lambda: calls.append("refreshed"))
    _add_domain()
    result = waf.add_custom_rule({"domain": "example.com", "target": "ARGS", "pattern": "badbot"})
    assert result["domain"] == "example.com"
    assert calls == ["refreshed"]
    with write_session() as session:
        assert session.query(WafCustomRule).filter_by(domain="example.com").count() == 1


def test_delete_custom_rule_removes_row(isolated_db, monkeypatch):
    monkeypatch.setattr(waf.ols, "refresh_main_config", lambda: None)
    _add_domain()
    created = waf.add_custom_rule({"domain": "example.com", "target": "ARGS", "pattern": "badbot"})
    result = waf.delete_custom_rule({"rule_id": created["id"]})
    assert result["status"] == "deleted"
    with write_session() as session:
        assert session.get(WafCustomRule, created["id"]) is None


def test_delete_custom_rule_rejects_unknown_id(isolated_db, monkeypatch):
    monkeypatch.setattr(waf.ols, "refresh_main_config", lambda: None)
    with pytest.raises(ValidationError):
        waf.delete_custom_rule({"rule_id": 999999})



def test_bootstrap_creates_libmodsecurity_compatible_include():
    from pathlib import Path
    waf.bootstrap_rules()
    content=Path(waf.WAF_RULES_FILE).read_text()
    assert 'IncludeOptional' not in content
    assert f'Include {waf.CRS_SETUP}' in content
    assert f'Include {waf.CRS_RULES}/*.conf' in content


def test_bootstrap_preserves_existing_operator_include():
    from pathlib import Path
    path=Path(waf.WAF_RULES_FILE);path.write_text('# operator custom rules\n')
    waf.bootstrap_rules()
    assert path.read_text()=='# operator custom rules\n'


def test_missing_crs_fails_before_enabling(isolated_db, monkeypatch):
    monkeypatch.setattr(waf,'is_available',lambda: True)
    waf.CRS_SETUP.unlink()
    with pytest.raises(RuntimeError,match='package files are missing'):
        waf.set_enabled({'enabled':True})
    with write_session() as session:
        assert session.get(WafSettings,1) is None


def test_unknown_domain_policy_is_rejected(isolated_db, monkeypatch):
    monkeypatch.setattr(waf.ols, "refresh_main_config", lambda: None)
    with pytest.raises(ValidationError, match="active Boron virtual host"):
        waf.set_domain_policy({"domain": "unknown.example", "mode": "disabled"})


def test_waf_modes_and_thresholds_persist(isolated_db, monkeypatch):
    monkeypatch.setattr(waf, "is_available", lambda: True)
    monkeypatch.setattr(waf.ols, "refresh_main_config", lambda: None)
    result = waf.update_settings({
        "mode": "protect", "paranoia_level": 2, "anomaly_threshold": 10,
        "wp_login_limit": 20, "wp_xmlrpc_limit": 3, "wp_rate_window_seconds": 120,
    })
    assert result["mode"] == "protect"
    status = waf.get_status({})
    assert status["mode"] == "protect"
    assert status["paranoia_level"] == 2
    assert status["wp_rate_window_seconds"] == 120


def test_narrow_timed_exception_lifecycle(isolated_db, monkeypatch):
    monkeypatch.setattr(waf.ols, "refresh_main_config", lambda: None)
    _add_domain()
    created = waf.add_exception({
        "domain": "example.com", "rule_id": 941100,
        "uri_prefix": "/wp-admin/", "parameter": "content",
        "duration_hours": 2, "reason": "editor false positive",
    })
    assert created["active"] is True
    assert created["rule_id"] == 941100
    with write_session() as session:
        assert session.get(WafException, created["id"]) is not None
    assert waf.delete_exception({"exception_id": created["id"]})["status"] == "deleted"


def test_detect_only_incident_has_rule_details():
    event = waf._parse_audit_log(REAL_AUDIT_LOG_SAMPLE)[0]
    assert event["action"] == "blocked"
    assert event["rule_id"] == 941100
    assert event["verified_client_ip"] == "104.234.179.64"
    assert event["findings"][0]["message"] == "XSS Attack Detected via libinjection"


def test_failed_ols_validation_restores_previous_waf_settings(isolated_db, monkeypatch):
    monkeypatch.setattr(waf, "is_available", lambda: True)
    calls = []
    def refresh():
        calls.append("refresh")
        if len(calls) == 1:
            raise RuntimeError("invalid generated config")
    monkeypatch.setattr(waf.ols, "refresh_main_config", refresh)
    with pytest.raises(RuntimeError, match="invalid generated config"):
        waf.update_settings({"mode": "protect"})
    with write_session() as session:
        assert session.get(WafSettings, 1) is None
    assert calls == ["refresh", "refresh"]
