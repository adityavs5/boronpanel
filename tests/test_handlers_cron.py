import pytest

from daemon import handlers_account as ha
from daemon import handlers_cron as hc
from daemon.procutil import ProcResult


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)


@pytest.fixture()
def fake_crontab(monkeypatch):
    state: dict[str, str] = {}

    def fake_run(args, input_text=None, timeout=30.0, check=False):
        username = args[2]
        if args[3] == "-l":
            content = state.get(username)
            if content is None:
                return ProcResult(args=args, returncode=1, stdout="", stderr=f"no crontab for {username}")
            return ProcResult(args=args, returncode=0, stdout=content, stderr="")
        elif args[3] == "-":
            state[username] = input_text or ""
            return ProcResult(args=args, returncode=0, stdout="", stderr="")
        elif args[3] == "-r":
            if username not in state:
                return ProcResult(args=args, returncode=1, stdout="", stderr=f"no crontab for {username}")
            del state[username]
            return ProcResult(args=args, returncode=0, stdout="", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(hc.cron, "run", fake_run)
    return state


def test_add_list_delete_via_handlers(isolated_db, stub_sysops, fake_crontab):
    ha.create_account({"username": "demo1"})
    result = hc.add_cron_job({"username": "demo1", "schedule": "*/10 * * * *", "command": "/bin/true", "label": "x"})
    assert result["schedule"] == "*/10 * * * *"

    listed = hc.list_cron_jobs({"username": "demo1"})
    assert len(listed["jobs"]) == 1

    hc.delete_cron_job({"username": "demo1", "job_id": result["id"]})
    assert hc.list_cron_jobs({"username": "demo1"})["jobs"] == []


def test_update_via_handlers(isolated_db, stub_sysops, fake_crontab):
    ha.create_account({"username": "demo1"})
    result = hc.add_cron_job({"username": "demo1", "schedule": "0 0 * * *", "command": "/bin/a"})
    updated = hc.update_cron_job(
        {"username": "demo1", "job_id": result["id"], "schedule": "0 1 * * *", "command": "/bin/b"}
    )
    assert updated["schedule"] == "0 1 * * *"
    assert updated["command"] == "/bin/b"


def test_cron_ops_reject_unknown_account(isolated_db, stub_sysops, fake_crontab):
    with pytest.raises(RuntimeError):
        hc.list_cron_jobs({"username": "ghost"})
    with pytest.raises(RuntimeError):
        hc.add_cron_job({"username": "ghost", "schedule": "* * * * *", "command": "/bin/true"})


def test_cron_ops_reject_terminated_account(isolated_db, stub_sysops, fake_crontab):
    ha.create_account({"username": "demo1"})
    ha.terminate_account({"username": "demo1"})
    with pytest.raises(RuntimeError):
        hc.add_cron_job({"username": "demo1", "schedule": "* * * * *", "command": "/bin/true"})


def test_terminate_account_cron_hook_clears_crontab(isolated_db, stub_sysops, fake_crontab):
    ha.create_account({"username": "demo1"})
    hc.add_cron_job({"username": "demo1", "schedule": "* * * * *", "command": "/bin/true"})

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import Account

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        hc.terminate_account_cron(account)

    assert "demo1" not in fake_crontab


def test_mailto_get_set_via_handlers(isolated_db, stub_sysops, fake_crontab):
    ha.create_account({"username": "demo1"})
    assert hc.get_cron_mailto({"username": "demo1"})["mailto"] == ""

    result = hc.set_cron_mailto({"username": "demo1", "mailto": "alerts@example.com"})
    assert result["mailto"] == "alerts@example.com"
    assert hc.get_cron_mailto({"username": "demo1"})["mailto"] == "alerts@example.com"


def test_mailto_set_rejects_root(isolated_db, stub_sysops, fake_crontab):
    from shared.validation import ValidationError

    ha.create_account({"username": "demo1"})
    with pytest.raises(ValidationError):
        hc.set_cron_mailto({"username": "demo1", "mailto": "root"})


def test_mailto_ops_reject_unknown_account(isolated_db, stub_sysops, fake_crontab):
    with pytest.raises(RuntimeError):
        hc.get_cron_mailto({"username": "ghost"})
    with pytest.raises(RuntimeError):
        hc.set_cron_mailto({"username": "ghost", "mailto": "a@b.com"})
