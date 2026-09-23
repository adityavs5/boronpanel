"""Tests for the FileBrowser Quantum integration (daemon/filebrowser.py).

Covers config generation, the fb.add_source / fb.remove_source lifecycle ops
(incl. the ownership-mitigation ACL and validation/idempotency), fb.open, and
fb.status. Subprocess calls (setfacl / systemctl) are mocked on the module's own
`run` binding, matching this project's convention; the home-dir jail runs
against a real tmp filesystem.
"""
from __future__ import annotations

import collections

import pytest
import yaml

from daemon import filebrowser as fb
from daemon.procutil import ProcResult
from shared.validation import ValidationError


@pytest.fixture()
def fb_env(tmp_path, monkeypatch):
    """A tmp home base with one account home + a captured `run`."""
    home_base = tmp_path / "home"
    (home_base / "demo1").mkdir(parents=True)
    monkeypatch.setattr(fb.settings, "home_base", str(home_base))
    monkeypatch.setattr(fb.settings, "filebrowser_data_dir", str(tmp_path / "fbdata"))
    monkeypatch.setattr(fb.settings, "filebrowser_account_data_dir", str(tmp_path / "accounts"))
    monkeypatch.setattr(fb.settings, "filebrowser_runtime_dir", str(tmp_path / "runtime"))
    monkeypatch.setattr(fb.settings, "filebrowser_config", str(tmp_path / "filebrowser.yaml"))

    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        out = ""
        if "is-active" in args:
            out = "active"
        elif "is-enabled" in args:
            out = "enabled"
        return ProcResult(args=list(args), returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(fb, "run", fake_run)
    from daemon import filebrowser_accounts
    monkeypatch.setattr(filebrowser_accounts, "run", fake_run)
    monkeypatch.setattr(filebrowser_accounts, "start", lambda username, home: None)
    monkeypatch.setattr(filebrowser_accounts, "TEMPLATE_PATH", str(tmp_path / "account.service"))
    return {"home_base": home_base, "calls": calls}


# --- config generation -----------------------------------------------------


def test_build_config_single_home_source_with_createuserdir(fb_env):
    cfg = fb.build_config()
    src = cfg["server"]["sources"]
    assert len(src) == 1, "isolation model is a single shared /home source"
    assert src[0]["name"] == "home"
    assert src[0]["path"] == str(fb_env["home_base"])
    assert src[0]["config"]["createUserDir"] is True
    assert src[0]["config"]["defaultUserScope"] == "/"
    assert src[0]["config"]["defaultEnabled"] is True


def test_build_config_created_files_not_world_readable(fb_env):
    # Security-critical: FB Quantum runs as root and would otherwise chmod new
    # files to 0644 (world-readable), a cross-tenant leak under 711 homes. These
    # keys make it create 0660 files / 0770 dirs (other = none). Found by live
    # testing; see docs/CHECKPOINT-filebrowser-quantum.md.
    fs = fb.build_config()["server"]["filesystem"]
    assert fs["createFilePermission"] == "660"
    assert fs["createDirectoryPermission"] == "770"


def test_build_config_proxy_auth_loopback_and_no_password(fb_env):
    cfg = fb.build_config()
    assert cfg["server"]["listen"] == fb.settings.filebrowser_bind_host  # loopback
    assert cfg["server"]["baseURL"] == fb.settings.filebrowser_base_url
    assert cfg["auth"]["methods"]["password"]["enabled"] is False
    proxy = cfg["auth"]["methods"]["proxy"]
    assert proxy["enabled"] is True
    assert proxy["header"] == fb.settings.filebrowser_header  # X-Fb-User
    assert cfg["frontend"]["name"] == fb.settings.filebrowser_brand
    # no sharing (multi-tenant box); realtime stays ON — it's SSE over plain
    # HTTP and with it off the SPA loops on a 403 showing error toasts.
    perms = cfg["userDefaults"]["permissions"]
    assert perms["share"] is False and perms["realtime"] is True and perms["admin"] is False
    assert perms["modify"] and perms["create"] and perms["delete"] and perms["download"]


def test_render_config_writes_parseable_yaml_0640(fb_env):
    import os

    path = fb.render_config()
    text = open(path).read()
    parsed = yaml.safe_load(text)
    assert parsed["server"]["sources"][0]["name"] == "home"
    assert oct(os.stat(path).st_mode & 0o777) == "0o640"


# --- add_source ------------------------------------------------------------


def test_add_source_returns_scope_and_applies_acl(fb_env):
    result = fb.add_source({"username": "demo1"})
    assert result["username"] == "demo1"
    assert result["source"] == "home"
    assert result["scope"] == "/demo1"
    assert result["path"].endswith("/home/demo1")
    # the ownership-mitigation setfacl call: recursive, default + access, own user
    setfacl_calls = [c for c in fb_env["calls"] if c and c[0] == "setfacl"]
    assert len(setfacl_calls) == 1
    call = setfacl_calls[0]
    assert "-R" in call
    assert "u:demo1:rwX,d:u:demo1:rwX" in call
    assert call[-1].endswith("/home/demo1")


def test_add_source_idempotent(fb_env):
    fb.add_source({"username": "demo1"})
    # second call must not raise
    fb.add_source({"username": "demo1"})


def test_add_source_missing_home_raises(fb_env):
    # valid username but no home dir on disk
    with pytest.raises(fb.FileBrowserError):
        fb.add_source({"username": "nohome"})


def test_add_source_invalid_username_raises(fb_env):
    with pytest.raises(ValidationError):
        fb.add_source({"username": "BAD-Name"})


def test_add_source_acl_failure_is_best_effort(fb_env, monkeypatch):
    """setfacl failing must not raise — account creation must not fail because
    of the ownership-mitigation ACL."""
    def failing_run(args, **kwargs):
        return ProcResult(args=list(args), returncode=1, stdout="", stderr="setfacl: nope")

    monkeypatch.setattr(fb, "run", failing_run)
    result = fb.add_source({"username": "demo1"})  # should not raise
    assert result["username"] == "demo1"


# --- remove_source ---------------------------------------------------------


def test_remove_source_idempotent_and_validated(fb_env):
    assert fb.remove_source({"username": "demo1"})["status"] == "removed"
    # idempotent: works even with no home on disk (terminated account)
    assert fb.remove_source({"username": "gone"})["status"] == "removed"


def test_remove_source_invalid_username_raises(fb_env):
    with pytest.raises(ValidationError):
        fb.remove_source({"username": "../etc"})


# --- open (audited access) -------------------------------------------------


def test_open_access_returns_scope(fb_env):
    result = fb.open_access({"username": "demo1"})
    assert result["scope"] == "/demo1"
    assert result["path"].endswith("/home/demo1")


def test_open_access_missing_home_raises(fb_env):
    with pytest.raises(fb.FileBrowserError):
        fb.open_access({"username": "nohome"})


# --- status ----------------------------------------------------------------


def test_status_reports_service_state(fb_env):
    st = fb.status({})
    assert st["service"] == "boron-filebrowser@.service"
    assert st["active"] == "on-demand"
    assert st["enabled"] == "on-demand"
    assert st["header"] == fb.settings.filebrowser_header
    assert st["source_path"].endswith("/home")


# --- backfill (refresh_all) ------------------------------------------------


def test_refresh_all_sources_applies_to_active_accounts_with_homes(isolated_db, fb_env):
    from shared.db import write_session
    from shared.models import Account

    # demo1 active (has home); demo2 suspended (keep source, has home); gone terminated
    (fb_env["home_base"] / "demo2").mkdir()
    with write_session() as db:
        db.add(Account(username="demo1", uid=6001, gid=6001, status="active"))
        db.add(Account(username="demo2", uid=6002, gid=6002, status="suspended"))
        db.add(Account(username="gone", uid=6003, gid=6003, status="terminated"))

    result = fb.refresh_all_sources({})
    ok = {r["username"] for r in result["results"] if r["ok"]}
    assert ok == {"demo1", "demo2"}  # active + suspended both kept
    assert "gone" not in {r["username"] for r in result["results"]}  # terminated skipped
    # setfacl applied for each kept account
    setfacl = [c for c in fb_env["calls"] if c and c[0] == "setfacl"]
    assert len(setfacl) == 2


# --- lifecycle wrappers ----------------------------------------------------


def test_lifecycle_wrappers(fb_env):
    Account = collections.namedtuple("Account", ["username"])
    fb.add_source_for_account(Account(username="demo1"))
    fb.remove_source_for_account(Account(username="demo1"))
    setfacl_calls = [c for c in fb_env["calls"] if c and c[0] == "setfacl"]
    assert setfacl_calls, "add_source_for_account should apply the ACL"


# --- network isolation (Audit 3 A3-7) ---------------------------------------
#
# FileBrowser Quantum's own proxy-auth trusts ANY X-Fb-User header with no
# authentication of its own (confirmed live) -- so restricting which local
# uid may even reach the loopback backend port is the actual boundary once
# the proxy's header-injection is bypassed. These tests cover
# restrict_backend_access()'s idempotent iptables rule installation.

_FakePasswd = collections.namedtuple("_FakePasswd", ["pw_uid"])


def test_restrict_backend_access_installs_accept_then_reject(fb_env, monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if args[1] == "-C":
            return ProcResult(args=list(args), returncode=1, stdout="", stderr="Bad rule")  # not present yet
        return ProcResult(args=list(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fb, "run", fake_run)

    monkeypatch.setattr(fb.pwd, "getpwnam", lambda name: _FakePasswd(pw_uid=996))

    fb.restrict_backend_access()

    inserted = [c for c in calls if c[1] == "-I"]
    assert len(inserted) == 2, "one ACCEPT rule for the api uid, one REJECT rule for everyone else"
    accept, reject = inserted
    assert "--uid-owner" in accept and "996" in accept and "ACCEPT" in accept
    assert "REJECT" in reject and "--uid-owner" not in reject
    # Must be inserted (not appended) -- ufw's own ufw-before-output chain
    # unconditionally accepts all loopback traffic near the top of OUTPUT,
    # so anything appended to the end is never reached (confirmed live).
    # Position 1 must be ACCEPT, position 2 REJECT, or legitimate
    # boron-api traffic gets silently rejected too.
    assert accept[3] == "1" and accept[2] == "OUTPUT"
    assert reject[3] == "2" and reject[2] == "OUTPUT"
    assert calls.index(accept) < calls.index(reject)
    assert all(str(fb.settings.filebrowser_bind_port) in c for c in inserted)


def test_restrict_backend_access_idempotent_when_rules_already_present(fb_env, monkeypatch):
    calls = []
    monkeypatch.setattr(fb, "run", lambda args, **kw: calls.append(list(args)) or ProcResult(args=list(args), returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(fb.pwd, "getpwnam", lambda name: _FakePasswd(pw_uid=996))

    fb.restrict_backend_access()

    assert calls, "should still check"
    assert all(c[1] == "-C" for c in calls), "rules already present -- never re-appended"


def test_restrict_backend_access_missing_api_user_does_not_raise(fb_env, monkeypatch):
    def _missing(name):
        raise KeyError(name)

    monkeypatch.setattr(fb.pwd, "getpwnam", _missing)
    calls = []
    monkeypatch.setattr(fb, "run", lambda args, **kw: calls.append(args))

    fb.restrict_backend_access()  # must not raise -- daemon startup can't be blocked by this

    assert calls == []


def test_bootstrap_disables_shared_root_backend(fb_env, monkeypatch):
    monkeypatch.setattr("os.path.exists", lambda p: True)
    result = fb.bootstrap({})
    assert result["active"] == "on-demand"
    assert ["systemctl", "disable", "--now", "boron-filebrowser.service"] in fb_env["calls"]
    assert not any("enable" in call for call in fb_env["calls"])
