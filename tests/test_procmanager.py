"""Phase 8 feature 10: process manager (strict uid scoping). The live listing/
kill path was verified against real processes on this server; these cover the
scoping guards."""
import types

import pytest

from daemon import procmanager
from shared.db import write_session
from shared.models import Account


def _account(username="demo1", uid=5001):
    with write_session() as db:
        db.add(Account(username=username, status="active", uid=uid, gid=uid))


def test_account_uid_ok(isolated_db):
    _account(uid=5001)
    assert procmanager._account_uid("demo1") == 5001


def test_account_uid_rejects_missing(isolated_db):
    with pytest.raises(RuntimeError, match="not found"):
        procmanager._account_uid("nope")


def test_account_uid_rejects_system_uid(isolated_db):
    _account(uid=100)  # below MIN_ACCOUNT_UID
    with pytest.raises(RuntimeError, match="system uid"):
        procmanager._account_uid("demo1")


def test_kill_rejects_process_not_owned(isolated_db, monkeypatch):
    _account(uid=5001)
    fake_proc = types.SimpleNamespace()
    monkeypatch.setattr(procmanager.psutil, "Process", lambda pid: fake_proc)
    # The process is owned by a DIFFERENT uid (0/root) -> must refuse.
    monkeypatch.setattr(procmanager, "_proc_uid", lambda p: 0)
    with pytest.raises(RuntimeError, match="not owned"):
        procmanager.kill_process({"username": "demo1", "pid": 1})


def test_kill_owned_process_terminates(isolated_db, monkeypatch):
    _account(uid=5001)
    calls = {}

    class FakeProc:
        def terminate(self): calls["terminate"] = True
        def wait(self, timeout=None): return 0
        def kill(self): calls["kill"] = True

    monkeypatch.setattr(procmanager.psutil, "Process", lambda pid: FakeProc())
    monkeypatch.setattr(procmanager, "_proc_uid", lambda p: 5001)  # owned by the account
    monkeypatch.setattr(procmanager.pwd, 'getpwnam', lambda name: types.SimpleNamespace(pw_uid=5001,pw_gid=5001))
    def signal(argv, **kwargs):
        assert argv == ['/bin/kill','-TERM','--','4242']
        assert kwargs['uid'] == kwargs['gid'] == 5001
        calls['terminate'] = True
        return types.SimpleNamespace(ok=True)
    monkeypatch.setattr(procmanager, 'run', signal)
    result = procmanager.kill_process({"username": "demo1", "pid": 4242})
    assert result == {"pid": 4242, "status": "killed"}
    assert calls.get("terminate") is True


def test_process_uid_change_cannot_bypass_kernel_permissions(isolated_db, monkeypatch):
    _account(uid=5001)
    monkeypatch.setattr(procmanager.psutil,'Process',lambda pid: types.SimpleNamespace(is_running=lambda:True))
    monkeypatch.setattr(procmanager,'_proc_uid',lambda p:5001)
    monkeypatch.setattr(procmanager.pwd,'getpwnam',lambda name:types.SimpleNamespace(pw_uid=5001,pw_gid=5001))
    def denied(argv,**kwargs):
        assert kwargs['uid']==5001
        return types.SimpleNamespace(ok=False)
    monkeypatch.setattr(procmanager,'run',denied)
    with pytest.raises(RuntimeError,match='could not signal'):
        procmanager.kill_process({'username':'demo1','pid':4242})
