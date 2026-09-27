import pwd

import pytest

from daemon import account_exec
from shared.db import write_session
from shared.models import Account


def test_wrap_places_command_in_uid_slice_before_exec(isolated_db, monkeypatch):
    with write_session() as session:
        account = Account(username="demo1", status="active", uid=5001, gid=5001)
        session.add(account)
        session.flush()
        account_id = account.id
    fake_pw = pwd.struct_passwd(("demo1", "x", 5001, 5001, "", "/home/demo1", "/bin/bash"))
    monkeypatch.setattr(account_exec, "_getpwnam", lambda username: fake_pw)
    applied = []
    monkeypatch.setattr(account_exec.resource_manager, "apply_account", lambda value: applied.append(value))

    result = account_exec.wrap("demo1", ["/usr/bin/id"], token="job-12", cwd="/home/demo1")

    assert applied == [account_id]
    assert "--slice=user-5001.slice" in result
    assert "--uid=5001" in result and "--gid=5001" in result
    assert "--working-directory=/home/demo1" in result
    assert result[-2:] == ["--", "/usr/bin/id"]


def test_wrap_rejects_identity_mismatch_before_launch(isolated_db, monkeypatch):
    with write_session() as session:
        session.add(Account(username="demo1", status="active", uid=5001, gid=5001))
    fake_pw = pwd.struct_passwd(("demo1", "x", 5002, 5002, "", "/home/demo1", "/bin/bash"))
    monkeypatch.setattr(account_exec, "_getpwnam", lambda username: fake_pw)
    with pytest.raises(RuntimeError, match="does not match"):
        account_exec.wrap("demo1", ["/usr/bin/id"])
