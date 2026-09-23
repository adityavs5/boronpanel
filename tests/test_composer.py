"""Phase 8 feature 9: Composer UI (+ the shared cmdjobs runner)."""
import types

import pytest

from daemon import cmdjobs, composerui
from daemon.procutil import ProcResult
from shared.db import write_session
from shared.models import Account, CommandRun
from shared.validation import ValidationError
from sqlalchemy import select


def _account(username="demo1", status="active"):
    with write_session() as db:
        account = Account(username=username, status=status, uid=5001, gid=5001)
        db.add(account)
        db.flush()
        return account.id


# --- package validation ----------------------------------------------------


def test_validate_package_ok():
    assert composerui._validate_package("monolog/monolog") == "monolog/monolog"
    assert composerui._validate_package("symfony/console:^6.0") == "symfony/console:^6.0"


def test_validate_package_rejects_injection():
    for bad in ["evil; rm -rf /", "no-slash", "a/b && curl x", "../etc/passwd"]:
        with pytest.raises(ValidationError):
            composerui._validate_package(bad)


# --- run_composer (mocked deps) --------------------------------------------


def _mock_run_env(monkeypatch, tmp_path):
    monkeypatch.setattr(composerui, "ensure_composer", lambda: "/usr/bin/composer")
    monkeypatch.setattr(composerui, "_resolve_app_dir", lambda u, d: str(tmp_path / "app"))
    captured = {}
    monkeypatch.setattr(composerui.cmdjobs, "submit", lambda *a, **k: captured.update(args=a) or {"id": 1})
    return captured


def test_run_composer_install(isolated_db, tmp_path, monkeypatch):
    _account()
    captured = _mock_run_env(monkeypatch, tmp_path)
    composerui.run_composer({"username": "demo1", "command": "install", "app_dir": "app"})
    username, kind, target, argv, display = captured["args"]
    assert kind == "composer"
    assert argv[1] == "/usr/bin/composer"
    assert "install" in argv and "--no-interaction" in argv
    assert display == "composer install"


def test_run_composer_require_validates_package(isolated_db, tmp_path, monkeypatch):
    _account()
    _mock_run_env(monkeypatch, tmp_path)
    with pytest.raises(ValidationError):
        composerui.run_composer({"username": "demo1", "command": "require", "app_dir": "app", "package": "bad;pkg"})


def test_run_composer_unknown_command(isolated_db, tmp_path, monkeypatch):
    _account()
    _mock_run_env(monkeypatch, tmp_path)
    with pytest.raises(ValidationError):
        composerui.run_composer({"username": "demo1", "command": "nuke", "app_dir": "app"})


# --- cmdjobs runner (executor + run mocked to run synchronously) -----------


def _sync_executor(monkeypatch):
    class _Sync:
        def submit(self, fn, *args, **kwargs):
            fn(*args, **kwargs)
    monkeypatch.setattr(cmdjobs, "_executor", _Sync())


def test_cmdjobs_submit_runs_and_records(isolated_db, tmp_path, monkeypatch):
    account_id = _account()
    _sync_executor(monkeypatch)
    monkeypatch.setattr(cmdjobs.pwd, "getpwnam", lambda u: types.SimpleNamespace(pw_dir="/home/demo1"))
    monkeypatch.setattr(cmdjobs, "run", lambda argv, **k: ProcResult(argv, 0, "did the thing", ""))

    result = cmdjobs.submit("demo1", "composer", "/home/demo1/app", ["/usr/bin/composer", "install"], "composer install")
    job_id = result["id"]
    fetched = cmdjobs.get_run({"username": "demo1", "job_id": job_id})
    assert fetched["status"] == "completed"
    assert fetched["exit_code"] == 0
    assert fetched["stdout"] == "did the thing"


def test_cmdjobs_records_failure(isolated_db, tmp_path, monkeypatch):
    _account()
    _sync_executor(monkeypatch)
    monkeypatch.setattr(cmdjobs.pwd, "getpwnam", lambda u: types.SimpleNamespace(pw_dir="/home/demo1"))
    monkeypatch.setattr(cmdjobs, "run", lambda argv, **k: ProcResult(argv, 1, "", "boom"))
    result = cmdjobs.submit("demo1", "wpcli", "/home/demo1/pub", ["wp", "core", "update"], "wp core update")
    fetched = cmdjobs.get_run({"username": "demo1", "job_id": result["id"]})
    assert fetched["status"] == "failed"
    assert fetched["error"] == "boom"


def test_cmdjobs_reveals_secret_once(isolated_db, tmp_path, monkeypatch):
    _account()
    _sync_executor(monkeypatch)
    monkeypatch.setattr(cmdjobs.pwd, "getpwnam", lambda u: types.SimpleNamespace(pw_dir="/home/demo1"))
    monkeypatch.setattr(cmdjobs, "run", lambda argv, **k: ProcResult(argv, 0, "ok", ""))
    result = cmdjobs.submit("demo1", "wpcli", "/home/demo1/pub", ["wp"], "wp user update", revealed_secret="Secret123!")
    first = cmdjobs.get_run({"username": "demo1", "job_id": result["id"]})
    assert first["revealed_secret"] == "Secret123!"
    second = cmdjobs.get_run({"username": "demo1", "job_id": result["id"]})
    assert second["revealed_secret"] is None  # cleared after first read


def test_cmdjobs_get_run_rejects_foreign_account(isolated_db, tmp_path, monkeypatch):
    _account("demo1")
    _account("demo2", )
    _sync_executor(monkeypatch)
    monkeypatch.setattr(cmdjobs.pwd, "getpwnam", lambda u: types.SimpleNamespace(pw_dir="/home/demo1"))
    monkeypatch.setattr(cmdjobs, "run", lambda argv, **k: ProcResult(argv, 0, "ok", ""))
    result = cmdjobs.submit("demo1", "composer", "/home/demo1/app", ["/usr/bin/composer", "install"], "composer install")
    with pytest.raises(RuntimeError, match="does not belong"):
        cmdjobs.get_run({"username": "demo2", "job_id": result["id"]})


def test_cmdjobs_worker_rechecks_account_status_before_running(isolated_db, tmp_path, monkeypatch):
    account_id = _account()
    queued = []

    class _Queued:
        def submit(self, fn, *args, **kwargs):
            queued.append((fn, args, kwargs))

    monkeypatch.setattr(cmdjobs, "_executor", _Queued())
    monkeypatch.setattr(cmdjobs.pwd, "getpwnam", lambda u: types.SimpleNamespace(pw_dir="/home/demo1"))
    ran = []
    monkeypatch.setattr(cmdjobs, "run", lambda argv, **k: ran.append(argv) or ProcResult(argv, 0, "ok", ""))

    result = cmdjobs.submit("demo1", "composer", "/home/demo1/app", ["/usr/bin/composer", "install"], "composer install")
    with write_session() as db:
        db.get(Account, account_id).status = "suspended"

    fn, args, kwargs = queued.pop()
    fn(*args, **kwargs)

    job = cmdjobs.get_run({"username": "demo1", "job_id": result["id"]})
    assert job["status"] == "failed"
    assert "suspended" in job["error"]
    assert ran == []
