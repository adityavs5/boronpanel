"""Round 2 cross-area requests: regressions for the backend items other
areas could not change in their own files."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import struct
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, ResellerAccount, ResellerPlan, ResellerProfile
from shared.validation import NotFoundError, ValidationError


# --- RPC transport ----------------------------------------------------------


def test_unencodable_result_is_an_opaque_internal_failure(monkeypatch):
    """A handler result json cannot carry (a date) used to come back as
    internal_error with the encoder's text, 'Object of type date ...'."""
    from daemon import server

    monkeypatch.setattr(server.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=4242))
    monkeypatch.setattr(server.socket, "SO_PEERCRED", getattr(server.socket, "SO_PEERCRED", 17), raising=False)

    class Sock:
        def getsockopt(self, *_args):
            return struct.pack("3i", 1, 4242, 4242)

    written = []

    class Writer:
        def get_extra_info(self, key, default=None):
            return Sock() if key == "socket" else default

        def write(self, data):
            written.append(data)

        async def drain(self):
            return None

        def close(self):
            return None

    async def read_frame(_reader):
        return {"op": "mail.autoresponder.get", "params": {}, "protocol": None}

    async def dispatch(_op, _params, _credential):
        return {"start_date": dt.date(2026, 12, 1)}

    monkeypatch.setattr(server, "read_frame", read_frame)
    monkeypatch.setattr(server, "validate_peer_protocol", lambda *a, **k: None)
    monkeypatch.setattr(server, "dispatch", dispatch)
    asyncio.run(server.handle_client(object(), Writer()))
    body = json.loads(written[0][4:])
    assert body["ok"] is False
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["message"].startswith("internal operation failure (reference: ")
    assert "date" not in body["error"]["message"]
    assert body["error"]["diagnostic"]["error_type"] == "TypeError"


# --- 404s instead of 502/400 ---------------------------------------------------


def test_php_function_override_lookups_are_not_found(isolated_db):
    from daemon import phpfunctions
    from daemon.server import rpc_error_code

    with write_session() as session:
        with pytest.raises(NotFoundError) as missing_account:
            phpfunctions._get_account(session, "ghost")
        session.add(Account(username="alice", status="active"))
    assert rpc_error_code(missing_account.value) == "not_found"
    with pytest.raises(NotFoundError, match="domain 'nope.example' not found"):
        phpfunctions.set_override({"username": "alice", "domain": "nope.example", "disable_functions": []})
    with pytest.raises(NotFoundError, match="no PHP function override"):
        phpfunctions.delete_override({"username": "alice"})


def test_resetting_resources_of_an_unknown_account_is_not_found(isolated_db):
    from daemon import resource_manager

    for call in (resource_manager.reset_account, resource_manager.save_account_policy):
        with pytest.raises(NotFoundError, match="Account not found"):
            call({"username": "ghost"})


# --- Account and plan read models ---------------------------------------------


def test_account_reports_whether_an_account_resource_override_wins(isolated_db):
    from daemon.handlers_account import _effective_limits
    from shared.models import ResourcePolicy

    with write_session() as session:
        account = Account(username="alice", status="active")
        session.add(account)
        session.flush()
        limits = _effective_limits(account)
        assert limits["limits_source"] == "account" and limits["resource_override"] is False
        session.add(ResourcePolicy(scope_type="account", scope_id=account.id, nproc=50))
        session.flush()
        limits = _effective_limits(account)
        assert limits["limits_source"] == "account" and limits["resource_override"] is True


def test_plans_report_their_live_account_count(isolated_db):
    from daemon import plans
    from shared.models import Plan

    with write_session() as session:
        limits = dict(cpu_pct=100, mem_mb=1024, io_mb=50, pids_max=100, quota_soft_mb=1000, quota_hard_mb=1200)
        basic, empty = Plan(name="basic", **limits), Plan(name="empty", **limits)
        session.add_all([basic, empty])
        session.flush()
        session.add_all([
            Account(username="alice", status="active", plan_id=basic.id),
            Account(username="bob", status="suspended", plan_id=basic.id),
            Account(username="gone", status="terminated", plan_id=basic.id),
        ])
    listed = {row["name"]: row["account_count"] for row in plans.list_plans({})["plans"]}
    assert listed == {"basic": 2, "empty": 0}


# --- Reseller task history ------------------------------------------------------


def _reseller_with(account_ids):
    from daemon import handlers_auth

    user = handlers_auth.create_panel_user({"username": "seller", "password": "ResellerPass123!", "role": "reseller"})
    with write_session() as db:
        plan = ResellerPlan(name="seller-plan")
        db.add(plan)
        db.flush()
        profile = ResellerProfile(panel_user_id=user["id"], plan_id=plan.id)
        db.add(profile)
        db.flush()
        for account_id in account_ids:
            db.add(ResellerAccount(reseller_id=profile.id, account_id=account_id))
        return user["id"], profile.id


def _task_rows():
    from shared.models import BackupDestination, BackupJob, UpdateJob, utcnow

    with write_session() as db:
        own, other = Account(username="ownedacct", status="active"), Account(username="otheracct", status="active")
        destination = BackupDestination(name="local", kind="local", local_path="/tmp")
        db.add_all([own, other, destination])
        db.flush()
        own_job = BackupJob(account_id=own.id, kind="full", status="completed", destination_id=destination.id,
                            completed_at=utcnow())
        other_job = BackupJob(account_id=other.id, kind="full", status="completed", destination_id=destination.id,
                              completed_at=utcnow())
        update = UpdateJob(kind="update", status="failed", from_version="3.3.1", to_version="3.3.2",
                           initiated_by="admin", completed_at=utcnow())
        db.add_all([own_job, other_job, update])
        db.flush()
        return own.id, own_job.id, other_job.id, update.id


def test_reseller_task_history_shows_only_its_accounts(isolated_db):
    from daemon import tasks

    own_id, own_job, other_job, update_id = _task_rows()
    _user_id, reseller_id = _reseller_with([own_id])
    scope = {"_authenticated_role": "reseller", "_authenticated_reseller_id": reseller_id}
    listed = tasks.list_tasks({**scope, "limit": 100})
    assert [row["key"] for row in listed["tasks"]] == [f"backup:{own_job}"]
    assert tasks.get_task({**scope, "kind": "backup", "task_id": own_job})["id"] == own_job
    for kind, task_id in (("backup", other_job), ("update", update_id)):
        with pytest.raises(ValidationError, match="Task not found"):
            tasks.get_task({**scope, "kind": kind, "task_id": task_id})
    with pytest.raises(ValidationError, match="Reseller is unavailable"):
        tasks.list_tasks({"_authenticated_role": "reseller", "limit": 100})


def test_reseller_task_authorization_follows_account_ownership(isolated_db):
    from daemon.rpc_authority import AuthorizationError, Principal, authorize

    own_id, own_job, other_job, update_id = _task_rows()
    user_id, reseller_id = _reseller_with([own_id])
    reseller = Principal("reseller", "seller", None, user_id, "session", reseller_id=reseller_id)
    authorize("tasks.list", {}, reseller)
    for op in ("tasks.get", "tasks.retry", "tasks.cancel"):
        authorize(op, {"kind": "backup", "task_id": own_job}, reseller)
        for kind, task_id in (("backup", other_job), ("update", update_id)):
            with pytest.raises(AuthorizationError, match="task ownership"):
                authorize(op, {"kind": kind, "task_id": task_id}, reseller)
    with pytest.raises(AuthorizationError, match="reseller required"):
        authorize("tasks.list", {}, Principal("reseller", "seller", None, user_id, "session"))


# --- Redirect patterns ----------------------------------------------------------


@pytest.mark.parametrize("stored,pattern", [
    ("/old-page", "/old-page"),
    ("/old.html", "/old\\.html"),
    ("/caf%C3%A9", "/café"),
    ("/a%20b", "/a\\x20b"),
    ("/50%25", "/50\\x25"),
    ("/%2e%2e/x", "/\\.\\./x"),
    ("/%0a", "/\\x0a"),
    ("/%FF", "/\\xff"),
    ("/%E2%80%A8", "/\\xe2\\x80\\xa8"),  # a line separator is never written as itself
])
def test_redirect_patterns_match_the_decoded_request_path(stored, pattern):
    """OpenLiteSpeed matches RewriteRule patterns against the decoded path,
    so a percent escape in a stored redirect path never matched."""
    from daemon import ols

    assert ols._redirect_regex_path(stored) == pattern


def test_render_time_rewrite_check_uses_the_shared_message():
    from daemon import ols
    from shared.validation import REWRITE_EXPANSION_MESSAGE

    with pytest.raises(ValidationError) as refused:
        ols.literal_rewrite_target("https://example.com/%{HTTP_COOKIE}")
    assert str(refused.value) == REWRITE_EXPANSION_MESSAGE


# --- Panel and mail certificates -----------------------------------------------


def test_panel_challenge_route_is_one_locked_ols_transaction(isolated_db, tmp_path, monkeypatch):
    from daemon import ols, panel_tls

    webroot = (tmp_path / "acme").resolve()
    monkeypatch.setattr(settings, "panel_hostname", "panel.example")
    monkeypatch.setattr(settings, "webmail_hostname", "")
    monkeypatch.setattr(settings, "pma_hostname", "")
    monkeypatch.setattr(settings, "panel_acme_webroot", str(webroot))
    monkeypatch.setattr(settings, "vhost_conf_dir", str(tmp_path / "vhosts"))
    monkeypatch.setattr(panel_tls, "_challenge_owner", lambda: SimpleNamespace(pw_uid=0, pw_gid=0))
    monkeypatch.setattr(panel_tls.os, "chown", lambda *a: None)
    state = {"held": False}

    class Lock:
        def __enter__(self):
            state["held"] = True

        def __exit__(self, *exc):
            state["held"] = False

    monkeypatch.setattr(ols, "_CONFIG_LOCK", Lock())

    def read(name, value):
        def reader(*_a, **_k):
            assert state["held"], f"{name} read outside the OLS config lock"
            return value
        return reader

    monkeypatch.setattr(ols, "_all_active_vhosts", read("vhosts", ([], [])))
    monkeypatch.setattr(ols, "waf_template_context", read("waf", {}))
    monkeypatch.setattr(ols, "_ols_settings_from_session", read("ols settings", {"tuned": True}))
    rendered = {}
    monkeypatch.setattr(ols, "render_httpd_config",
                        lambda vhosts, procs, waf=None, ols_settings=None, infrastructure=None:
                        rendered.update(settings=ols_settings, infrastructure=infrastructure) or "main")

    class Writer:
        def __init__(self, **_kwargs):
            pass

        def apply(self, content):
            assert state["held"], "main config applied outside the OLS config lock"
            rendered["content"] = content
            return SimpleNamespace(ok=True)

    monkeypatch.setattr(panel_tls, "ConfigWriterMulti", Writer)
    try:
        assert panel_tls.bootstrap_challenge()["hostname"] == "panel.example"
    finally:
        webroot.chmod(0o755)  # bootstrap makes it read-only; let the workspace be pruned
    assert rendered["settings"] == {"tuned": True}
    assert rendered["infrastructure"] == frozenset({"panel"})
    assert rendered["content"]["main"] == "main"


@pytest.mark.parametrize("webmail,mail,deployed", [("", "", True), ("webmail.example", "", False)])
def test_panel_certificate_is_the_mail_certificate_without_a_webmail_hostname(
        isolated_db, monkeypatch, webmail, mail, deployed):
    from daemon import panel_tls, procutil
    from scripts import ssl_deploy_hook

    monkeypatch.setattr(settings, "panel_hostname", "panel.example")
    monkeypatch.setattr(settings, "webmail_hostname", webmail)
    monkeypatch.setattr(settings, "mail_hostname", mail)
    monkeypatch.setattr(panel_tls, "bootstrap_challenge", lambda: {"hostname": "panel.example", "webroot": "/var/www/a"})
    monkeypatch.setattr(procutil, "run", lambda argv, **kw: SimpleNamespace(raise_if_failed=lambda message: None))
    copies = []
    monkeypatch.setattr(ssl_deploy_hook, "_deploy_mail_service_certificate", copies.append)
    panel_tls.issue_certificate("admin@example.com")
    assert copies == (["panel.example"] if deployed else [])


@pytest.mark.parametrize("config,called", [
    ('panel_hostname = "panel.example"\n', True),
    ('panel_hostname = "panel.example"\nwebmail_hostname = "webmail.example"\n', False),
])
def test_panel_deploy_hook_copies_the_mail_certificate(tmp_path, monkeypatch, config, called):
    from scripts import panel_ssl_deploy as hook

    path = tmp_path / "boron.toml"
    path.write_text(config)
    monkeypatch.setattr(hook, "CONFIG_PATH", path)
    runs = []
    monkeypatch.setattr(hook.subprocess, "run",
                        lambda argv, env, check, timeout: runs.append((argv, env)) or SimpleNamespace(returncode=0))
    assert hook.deploy_mail_service("panel.example", "/etc/letsencrypt/live/panel.example") is True
    assert bool(runs) is called
    if called:
        argv, env = runs[0]
        assert argv[-1].endswith("ssl_deploy_hook.py")
        assert env["RENEWED_DOMAINS"] == "panel.example"
        assert env["RENEWED_LINEAGE"] == "/etc/letsencrypt/live/panel.example"


def test_separate_mail_hostname_uses_dns01_or_says_why_it_cannot(monkeypatch):
    from daemon import ssl as fssl

    monkeypatch.setattr(fssl.settings, "webmail_hostname", "webmail.example.com")
    monkeypatch.setattr(fssl.settings, "mail_hostname", "mail.example.com")
    monkeypatch.setattr(fssl, "_dns01_plan", lambda domain: ["--authenticator", "dns-powerdns"])
    assert fssl._challenge_plan("mail.example.com") == ("dns-01", ["--authenticator", "dns-powerdns"])
    monkeypatch.setattr(fssl, "_dns01_plan", lambda domain: None)
    with pytest.raises(fssl.SslError, match="needs a DNS-01 check"):
        fssl._challenge_plan("mail.example.com")


# --- Server Setup ---------------------------------------------------------------


def test_step4_reports_a_held_back_phpmyadmin_hostname(isolated_db, monkeypatch):
    from daemon import panel_config, server_setup

    held = {"status": "installing", "hostname": "pma.example"}
    monkeypatch.setattr(server_setup, "_service_hosts", lambda params: {"pma_hostname": "pma.example"})
    monkeypatch.setattr(panel_config, "apply_setup_values",
                        lambda hosts, reload_services: {"status": "completed", "updated": [], "phpmyadmin": held})
    monkeypatch.setattr(server_setup, "_apply_service_records", lambda params: {"records": []})
    result, _draft = server_setup._step4({"confirm": True})
    assert result["phpmyadmin"] == held


def test_step6_explains_phpmyadmin_installing_and_issues_a_separate_mail_certificate(isolated_db, monkeypatch):
    from daemon import pma, server_setup

    server_setup._record(5, "completed", {})
    monkeypatch.setattr(settings, "pma_hostname", "")
    monkeypatch.setattr(settings, "panel_hostname", "panel.example")
    monkeypatch.setattr(settings, "webmail_hostname", "webmail.example")
    monkeypatch.setattr(settings, "mail_hostname", "mail.example")
    monkeypatch.setattr(pma, "provisioning_status", lambda *_a: {"provisioning": "installing"})
    with pytest.raises(ValidationError, match="still being installed"):
        server_setup._step6({"email": "admin@example.com", "services": ["phpmyadmin"]})
    issued = []
    monkeypatch.setattr(server_setup.ssl_ops, "issue_certificate",
                        lambda params: issued.append(params["domain"]) or {"status": "issued"})
    result, _draft = server_setup._step6({"email": "admin@example.com", "services": ["mail"]})
    assert issued == ["mail.example"] and result["certificates"][0]["service"] == "mail"
    monkeypatch.setattr(settings, "mail_hostname", "webmail.example")  # covered by the webmail certificate
    with pytest.raises(ValidationError, match="Unknown or unconfigured certificate service: mail"):
        server_setup._step6({"email": "admin@example.com", "services": ["mail"]})


# --- Restores and backups -------------------------------------------------------


def test_restore_recreates_parked_domains_after_their_target(isolated_db, monkeypatch):
    from daemon import backup, handlers_domain, parked

    with write_session() as db:
        db.add(Account(username="alice", status="active"))
    calls = []
    monkeypatch.setattr(handlers_domain, "add_domain", lambda params: calls.append(("add", params["domain"])))
    monkeypatch.setattr(parked, "add_parked_domain",
                        lambda params: calls.append(("park", params["parked_domain"], params.get("target_domain"))))
    manifest = {"domains": [
        {"domain": "alias.example", "kind": "parked", "docroot": "/home/alice/shop/public_html"},
        {"domain": "alice.example", "kind": "primary", "docroot": "/home/alice/public_html"},
        {"domain": "shop.example", "kind": "addon", "docroot": "/home/alice/shop/public_html"},
        {"domain": "lost.example", "kind": "parked", "docroot": "/home/alice/gone"},
    ]}
    summary = {"not_restored": []}
    recreated = backup._recreate_domains("alice", manifest, set(), summary)
    assert calls == [("add", "alice.example"), ("add", "shop.example"),
                     ("park", "alias.example", "shop.example"), ("park", "lost.example", None)]
    assert recreated == ["alice.example", "shop.example", "alias.example", "lost.example"]
    assert summary["not_restored"] == []


def test_backing_up_another_accounts_database_is_a_400(isolated_db):
    from daemon import backup
    from daemon.server import rpc_error_code
    from shared.models import DatabaseGrant

    with write_session() as db:
        alice, bob = Account(username="alice", status="active"), Account(username="bob", status="active")
        db.add_all([alice, bob])
        db.flush()
        db.add(DatabaseGrant(account_id=bob.id, db_name="bob_shop", db_user="bob_shop"))
    with pytest.raises(ValidationError, match="choose a database owned by this account") as refused:
        backup.trigger_backup({"username": "alice", "kind": "database", "item_ref": "bob_shop"})
    assert rpc_error_code(refused.value) == "bad_request"


# --- Upgrade --------------------------------------------------------------------


def test_port_465_stays_closed_without_opendkim(tmp_path, monkeypatch):
    from scripts import upgrade_runtime

    master_cf = tmp_path / "master.cf"
    master_cf.write_text("smtp inet n - y - - smtpd\n")
    monkeypatch.setattr(upgrade_runtime, "POSTFIX_MASTER_CONFIG", master_cf)
    monkeypatch.setattr(upgrade_runtime.shutil, "which", lambda name: f"/usr/sbin/{name}" if name in ("ufw", "postconf") else None)
    monkeypatch.setattr(upgrade_runtime.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="Status: active\n"))
    commands = []
    monkeypatch.setattr(upgrade_runtime, "command", lambda args, timeout=900: commands.append(args))
    upgrade_runtime.configure_mail_client_ports()
    assert commands == []


def test_main_config_leaves_out_infrastructure_vhosts_not_written_yet(tmp_path, monkeypatch):
    """Webmail and phpMyAdmin saved in one go: the first bootstrap's main
    config must not reference the other's missing vhconf.conf."""
    from daemon import ols

    monkeypatch.setattr(ols, "OLS_SERVER_BASE", str(tmp_path))
    monkeypatch.setattr(settings, "vhost_conf_dir", str(tmp_path / "conf/vhosts"))
    monkeypatch.setattr(settings, "panel_hostname", "panel.example.com")
    monkeypatch.setattr(settings, "webmail_hostname", "webmail.example.com")
    monkeypatch.setattr(settings, "pma_hostname", "pma.example.com")
    assert ols.infrastructure_in_place(("webmail",)) == frozenset({"webmail"})
    content = ols.render_httpd_config([], [], infrastructure=ols.infrastructure_in_place(("webmail",)))
    assert "roundcube webmail.example.com" in content
    assert "pma.example.com" not in content and "boron_panel_acme" not in content
    pma_conf = tmp_path / "conf/vhosts/phpmyadmin/vhconf.conf"
    pma_conf.parent.mkdir(parents=True)
    pma_conf.write_text("")
    assert ols.infrastructure_in_place() == frozenset({"pma"})
    # Without the argument every configured service is rendered, as before.
    assert "pma.example.com" in ols.render_httpd_config([], [])


@pytest.mark.parametrize("state,reserved", [
    ({"status": "installing", "hostname": "pma.example.com"}, True),
    ({"status": "failed", "hostname": "pma.example.com"}, False),
    ({}, False),
])
def test_phpmyadmin_hostname_waiting_for_publication_is_reserved(monkeypatch, state, reserved):
    from daemon import domain_ownership, pma

    monkeypatch.setattr(settings, "pma_hostname", "")
    monkeypatch.setattr(pma, "provision_state", lambda: dict(state))
    reason = domain_ownership.reserved_reason("pma.example.com")
    assert (reason == "This hostname is reserved for a panel service") is reserved


def test_upgrade_repairs_an_interrupted_dpkg_before_installing_packages(monkeypatch):
    import inspect

    from scripts import upgrade_runtime

    body = inspect.getsource(upgrade_runtime.main)
    assert body.index("repair_interrupted_dpkg()") < body.index('command(["apt-get", "update"])')
    calls = []
    monkeypatch.setattr(upgrade_runtime.subprocess, "run", lambda args, **kw: calls.append((args, kw)))
    upgrade_runtime.repair_interrupted_dpkg()
    assert calls[0][0] == ["dpkg", "--configure", "-a"] and calls[0][1]["check"] is False


@pytest.mark.parametrize("master,expects_465", [
    ("smtp inet n - y - - smtpd\nsubmissions inet n - y - - smtpd\n", True),
    ("smtp inet n - y - - smtpd\n#submissions inet n - y - - smtpd\n", False),
])
def test_port_check_expects_every_mail_client_listener(tmp_path, monkeypatch, master, expects_465):
    from daemon import diagnostics

    master_cf = tmp_path / "master.cf"
    master_cf.write_text(master)
    monkeypatch.setattr(diagnostics, "POSTFIX_MASTER_CONFIG", master_cf)
    monkeypatch.setattr(diagnostics, "_proc_ports", lambda path, tcp: {int(settings.api_bind_port), 53} if tcp else {53})
    explanation = diagnostics._ports()["explanation"]
    for listener in ("Submission (587/tcp)", "POP3 (110/tcp)", "POP3S (995/tcp)"):
        assert listener in explanation
    assert ("SMTPS (465/tcp)" in explanation) is expects_465


def test_rotated_gzip_access_logs_are_counted(tmp_path):
    """OpenLiteSpeed gzips rotated access logs; usage and sitestats read
    them as plain text before, so rotated days lost their traffic."""
    import gzip

    from daemon import usage

    line = '1.2.3.4 - - [01/Jul/2026:06:35:50 +0000] "GET / HTTP/1.1" 200 1000 "-" "curl"\n'
    archive = tmp_path / "demo1-access.log.2026_07_01.gz"
    archive.write_bytes(gzip.compress((line * 3).encode()))
    assert usage._parse_access_log(archive) == {"2026-07-01": 3000}
    damaged = tmp_path / "demo1-access.log.2026_07_02.gz"
    damaged.write_bytes(gzip.compress((line * 2).encode())[:-12])  # cut short
    assert sum(usage._parse_access_log(damaged).values()) <= 2000  # what it held, no error
