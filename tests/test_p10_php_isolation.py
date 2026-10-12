import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from daemon import handlers_php_ini, inode_quotas, nsisolation, ols, php_lifecycle, site_health, stack_manager, waf
from shared.db import write_session
from shared.models import Account, Domain, IsolationTestResult, PhpLifecycleSettings, UsageSnapshot, WafSettings, utcnow
from shared.validation import ValidationError


def _account(username="demo1", *, uid=5001, domain="example.test", private_sessions=False):
    with write_session() as session:
        account = Account(
            username=username, uid=uid, gid=uid, status="active",
            primary_domain=domain, php_version="8.1",
            php_private_sessions=private_sessions,
        )
        session.add(account)
        session.flush()
        session.add(Domain(
            account_id=account.id, domain=domain, kind="primary",
            docroot=f"/home/{username}/public_html",
        ))
        return account.id


def test_php_lifecycle_and_new_domain_policy(isolated_db, monkeypatch):
    assert php_lifecycle.lifecycle("8.1", dt.date(2026, 1, 1))["is_eol"] is True
    assert php_lifecycle.lifecycle("8.5", dt.date(2026, 1, 1))["is_eol"] is False
    with write_session() as session:
        session.add(PhpLifecycleSettings(id=1, hide_eol_for_new_domains_after="2020-01-01"))
    assert "8.1" not in php_lifecycle.versions_for_new_domain(["8.1", "8.5"])
    assert php_lifecycle.versions_for_new_domain(["8.1", "8.5"]) == ["8.5"]


def test_php_remove_preview_lists_and_blocks_in_use_runtime(isolated_db, monkeypatch):
    _account()
    monkeypatch.setattr(stack_manager, "_package_catalog", lambda *_: (["lsphp81"], []))
    preview = stack_manager.preview({"component": "php", "action": "remove", "target": "8.1"})
    assert preview["usage"]["accounts"] == [{"username": "demo1", "primary_domain": "example.test"}]
    assert any("still assigned" in blocker for blocker in preview["blockers"])


def test_private_session_migration_is_explicit_and_idempotent(isolated_db, monkeypatch):
    _account("legacy", uid=5001, domain="legacy.test", private_sessions=False)
    _account("modern", uid=5002, domain="modern.test", private_sessions=True)
    created, recycled, refreshed = [], [], []
    monkeypatch.setattr(php_lifecycle.sysops, "ensure_php_session_dir", lambda username: created.append(username))
    monkeypatch.setattr(php_lifecycle.sysops, "recycle_php_workers", lambda username: recycled.append(username))
    monkeypatch.setattr(php_lifecycle.ols, "refresh_all_vhosts", lambda: refreshed.append(True))
    assert php_lifecycle.session_migration_preview({})["accounts"] == ["legacy"]
    with pytest.raises(ValidationError, match="confirm"):
        php_lifecycle.migrate_sessions({"confirm": False})
    result = php_lifecycle.migrate_sessions({"confirm": True})
    assert result["migrated"] == ["legacy"]
    assert created == ["legacy"] and recycled == ["legacy"] and refreshed == [True]
    assert php_lifecycle.migrate_sessions({"confirm": True})["status"] == "unchanged"


def test_private_session_path_is_rendered_only_for_opted_in_account():
    domain = {"domain": "example.test", "docroot": "/home/demo1/public_html"}
    enabled = SimpleNamespace(username="demo1", php_version="8.3", php_private_sessions=True)
    legacy = SimpleNamespace(username="demo1", php_version="8.3", php_private_sessions=False)
    assert 'php_admin_value session.save_path "/home/demo1/tmp/sessions"' in ols.render_vhost_conf(enabled, domain, suspended=False)
    assert "session.save_path" not in ols.render_vhost_conf(legacy, domain, suspended=False)


def test_effective_php_config_reads_rendered_vhost(isolated_db, monkeypatch, tmp_path):
    _account()
    config = tmp_path / "vhconf.conf"
    config.write_text(
        'php_admin_value open_basedir "/home/demo1:/tmp"\n'
        'php_admin_value disable_functions "exec,system"\n'
        'php_admin_value upload_tmp_dir "/home/demo1/tmp"\n'
        'php_admin_value upload_max_filesize "64M"\n'
        'php_admin_value session.save_path "/home/demo1/tmp/sessions"\n'
        'php_admin_value error_log "/home/demo1/logs/php-error.log"\n'
    )
    monkeypatch.setattr(handlers_php_ini.ols, "_vhost_conf_path", lambda _name: str(config))
    result = handlers_php_ini.effective_domain_config({"username": "demo1", "domain": "example.test"})
    assert result["source"] == "rendered_vhost"
    assert result["values"]["session.save_path"] == "/home/demo1/tmp/sessions"
    assert result["values"]["disable_functions"] == "exec,system"


def test_inode_quota_warning_then_enforcement(isolated_db, monkeypatch):
    account_id = _account()
    with write_session() as session:
        account = session.get(Account, account_id)
        inode_quotas.schedule(account, 450_000, 500_000, warning_days=7)
        assert inode_quotas.enforcement_state(account) == "warning"
        account.inode_enforce_at = utcnow() - dt.timedelta(seconds=1)
    calls = []
    monkeypatch.setattr(inode_quotas.sysops, "set_quota", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert inode_quotas.enforce_due({}) == {"applied": ["demo1"], "failed": [], "checked": 1}
    assert calls == [(('demo1', 5120, 6144), {"inode_soft": 450000, "inode_hard": 500000})]
    assert inode_quotas.enforce_due({})["checked"] == 0


def test_isolation_self_test_persists_and_stales_on_config_change(isolated_db, monkeypatch, tmp_path):
    _account()
    resource = tmp_path / "user-5001.slice"
    resource.mkdir()
    monkeypatch.setattr(nsisolation, "_account_uid", lambda _username: 5001)
    monkeypatch.setattr(nsisolation, "get_status", lambda _username: {"enabled": True})
    monkeypatch.setattr(nsisolation, "_account_process_rows", lambda _uid: [])
    monkeypatch.setattr(nsisolation, "_bubblewrap_server_configured", lambda: True)
    monkeypatch.setattr(nsisolation.cgroups, "_cgroup_path", lambda *_args, **_kwargs: resource)
    monkeypatch.setattr(nsisolation.pwd, "getpwuid", lambda _uid: SimpleNamespace(pw_name="demo1"))
    result = nsisolation.self_test({
        "username": "demo1",
        "file_manager_check": {"status": "passed", "explanation": "Proxy opened."},
    })
    assert result["status"] == "incomplete"
    assert next(row for row in result["checks"] if row["name"] == "File Manager path")["status"] == "passed"
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        saved = nsisolation._saved_test(session, account, True)
        assert saved["stale"] is False
        account.cpu_pct = 75
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        assert nsisolation._saved_test(session, account, True)["stale"] is True


def test_waf_detect_trial_never_protects_early(isolated_db, monkeypatch):
    monkeypatch.setattr(waf, "is_available", lambda: True)
    monkeypatch.setattr(waf, "bootstrap_rules", lambda: None)
    monkeypatch.setattr(waf.ols, "refresh_main_config", lambda: None)
    monkeypatch.setattr(waf, "list_blocked_requests", lambda _params: {"events": []})
    started = waf.start_detect_trial({})
    assert started["mode"] == "detect" and started["detect_trial"]["active"] is True
    with pytest.raises(ValidationError, match="after the detection review"):
        waf.finish_detect_trial({"action": "enable_protection", "confirm": True})
    with write_session() as session:
        session.get(WafSettings, 1).detect_review_due_at = utcnow() - dt.timedelta(seconds=1)
    completed = waf.finish_detect_trial({"action": "enable_protection", "confirm": True})
    assert completed["mode"] == "protect"
    assert completed["detect_trial"]["active"] is False


def test_site_health_is_account_scoped_and_read_only(isolated_db, monkeypatch):
    account_id = _account()
    _account("other1", uid=5002, domain="other.test")
    with write_session() as session:
        session.add(UsageSnapshot(account_id=account_id, disk_home_bytes=1024, inode_count=5))
    monkeypatch.setattr(site_health.ipmanager, "address_for_account", lambda _id: "192.0.2.10")
    monkeypatch.setattr(site_health, "_dns_check", lambda domain, expected: site_health._finding("DNS", "pass", "high", f"{domain} {expected}", "None"))
    monkeypatch.setattr(site_health, "_certificate_check", lambda _domain: site_health._finding("Certificate", "pass", "high", "ok", "None"))
    monkeypatch.setattr(site_health, "_web_php_check", lambda *_: site_health._finding("Web and PHP", "pass", "high", "ok", "None"))
    monkeypatch.setattr(site_health, "_error_check", lambda *_: site_health._finding("Recent errors", "pass", "medium", "ok", "None"))
    monkeypatch.setattr(site_health, "_cgroup_check", lambda *_: site_health._finding("Resource throttling", "unknown", "low", "idle", "Retry"))
    monkeypatch.setattr(site_health, "_mail_queue_check", lambda domains: site_health._finding("Mail queue", "pass", "medium", str(domains), "None"))
    result = site_health.check({"username": "demo1", "domain": "example.test"})
    assert result["read_only"] is True
    assert result["domains"] == ["example.test"]
    assert all(set(row) == {"name", "status", "confidence", "explanation", "next_step"} for row in result["findings"])
    with pytest.raises(ValidationError, match="does not belong"):
        site_health.check({"username": "demo1", "domain": "other.test"})
