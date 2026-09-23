import time

import pytest
from sqlalchemy import select

from daemon import nsisolation
from daemon.procutil import ProcResult
from shared.db import write_session
from shared.models import Account, AuditLog, NamespaceMigrationJob


def make_account(session, **overrides):
    defaults = dict(username="nstest1", status="active", uid=2000, gid=2000)
    defaults.update(overrides)
    account = Account(**defaults)
    session.add(account)
    session.flush()
    return account


@pytest.fixture()
def fake_lsnsctl(monkeypatch):
    """Records every invocation and returns canned responses keyed by the
    subcommand (last positional arg), matching the real CLI's own
    `[--uid N] SUBCOMMAND` shape.

    Matches a real, confirmed-live quirk: lsnsctl writes its `[INFO]`-
    prefixed log lines to stderr, only JSON payloads (list-disabled-uids)
    go to stdout -- responses are split accordingly, not just dumped into
    stdout uniformly, so a test bug that only checks stdout can't mask a
    real parsing bug the way it did once already this phase."""
    calls = []
    responses = {
        "get-min-uid": {"stderr": "2026-07-05 04:07:40.249 [INFO] Minimum UID: 1001\n"},
        "list-disabled-uids": {"stdout": "[]\n"},
        "enable-uid": {"stderr": "2026-07-05 05:54:53.404 [INFO] UID enabled\n"},
        "disable-uid": {"stderr": "2026-07-05 05:54:53.404 [INFO] UID disabled\n"},
        "unmount": {"stderr": ""},
    }

    def fake_run(args, timeout=15):
        calls.append(args)
        subcommand = args[-1]
        streams = responses.get(subcommand, {})
        return ProcResult(args=args, returncode=0, stdout=streams.get("stdout", ""), stderr=streams.get("stderr", ""))

    monkeypatch.setattr(nsisolation, "run", fake_run)
    return calls, responses


def test_get_min_uid_parses_integer(fake_lsnsctl):
    assert nsisolation.get_min_uid() == 1001


def test_list_disabled_uids_parses_json_array_of_strings_as_ints(fake_lsnsctl, monkeypatch):
    """Real lsnsctl output is `["1000", "1005"]` (JSON strings), not
    `[1000, 1005]` -- confirmed live. A mock using unquoted integers here
    once masked a real int-vs-str comparison bug in get_status()."""
    calls, responses = fake_lsnsctl
    responses["list-disabled-uids"] = {"stdout": '["1000", "1005"]'}
    assert nsisolation.list_disabled_uids() == [1000, 1005]


def test_enable_uid_calls_lsnsctl_with_correct_args(fake_lsnsctl, isolated_db):
    calls, _ = fake_lsnsctl
    nsisolation.enable_uid(1001)
    assert calls[-1] == [nsisolation.LSNSCTL_BIN, "--uid", "1001", "enable-uid"]


def test_disable_uid_calls_lsnsctl_with_correct_args(fake_lsnsctl, isolated_db):
    calls, _ = fake_lsnsctl
    nsisolation.disable_uid(1001)
    assert calls[-1] == [nsisolation.LSNSCTL_BIN, "--uid", "1001", "disable-uid"]


def test_unmount_uid_calls_lsnsctl_with_correct_args(fake_lsnsctl, isolated_db):
    calls, _ = fake_lsnsctl
    nsisolation.unmount_uid(1001)
    assert calls[-1] == [nsisolation.LSNSCTL_BIN, "--uid", "1001", "unmount"]


def test_enable_uid_raises_on_failure(monkeypatch, isolated_db):
    def fake_run(args, timeout=15):
        return ProcResult(args=args, returncode=1, stdout="", stderr="Specified uid: 33 < minimum uid: 1001")

    monkeypatch.setattr(nsisolation, "run", fake_run)
    with pytest.raises(nsisolation.NamespaceError, match="< minimum uid"):
        nsisolation.enable_uid(33)


def test_get_status_enabled_when_above_floor_and_not_disabled(fake_lsnsctl, isolated_db):
    with write_session() as session:
        make_account(session, username="nstest1", uid=2000)
    status = nsisolation.get_status("nstest1")
    assert status == {
        "username": "nstest1", "uid": 2000, "min_uid": 1001,
        "eligible": True, "explicitly_disabled": False, "enabled": True,
    }


def test_get_status_not_eligible_below_min_uid(fake_lsnsctl, isolated_db):
    with write_session() as session:
        make_account(session, username="nstest1", uid=1000)
    status = nsisolation.get_status("nstest1")
    assert status["eligible"] is False
    assert status["enabled"] is False


def test_get_status_disabled_even_if_eligible(fake_lsnsctl, isolated_db):
    calls, responses = fake_lsnsctl
    responses["list-disabled-uids"] = {"stdout": '["2000"]'}  # real lsnsctl output: JSON strings, not ints
    with write_session() as session:
        make_account(session, username="nstest1", uid=2000)
    status = nsisolation.get_status("nstest1")
    assert status["eligible"] is True
    assert status["explicitly_disabled"] is True
    assert status["enabled"] is False


def test_enable_namespace_rpc_resolves_username_to_uid(fake_lsnsctl, isolated_db):
    calls, _ = fake_lsnsctl
    with write_session() as session:
        make_account(session, username="nstest1", uid=2000)
    result = nsisolation.enable_namespace({"username": "nstest1"})
    assert calls[0] == [nsisolation.LSNSCTL_BIN, "--uid", "2000", "enable-uid"]
    assert result["enabled"] is True


def test_disable_namespace_rpc_resolves_username_to_uid(fake_lsnsctl, isolated_db):
    calls, _ = fake_lsnsctl
    with write_session() as session:
        make_account(session, username="nstest1", uid=2000)
    nsisolation.disable_namespace({"username": "nstest1"})
    assert calls[0] == [nsisolation.LSNSCTL_BIN, "--uid", "2000", "disable-uid"]


def test_namespace_status_rpc_unknown_account_raises(isolated_db):
    with pytest.raises(nsisolation.NamespaceError, match="not found"):
        nsisolation.namespace_status({"username": "doesnotexist"})


def test_account_with_no_uid_raises(isolated_db):
    with write_session() as session:
        make_account(session, username="nstest1", uid=None)
    with pytest.raises(nsisolation.NamespaceError, match="no uid"):
        nsisolation.get_status("nstest1")


def test_enable_for_account_swallows_below_floor_error(monkeypatch, isolated_db):
    """CREATE_HOOKS entry: a real account below the current min_uid floor
    must not block account creation -- expected/benign until Step 4."""
    def failing_run(args, timeout=15):
        return ProcResult(args=args, returncode=1, stdout="", stderr="Specified uid: 1000 < minimum uid: 1001")

    monkeypatch.setattr(nsisolation, "run", failing_run)
    with write_session() as session:
        account = make_account(session, username="nstest1", uid=1000)
    nsisolation.enable_for_account(account)  # must not raise; matches real usage, called after the session closes


def test_enable_for_account_no_uid_is_noop():
    account = Account(username="nouid", status="active", uid=None, gid=None)
    nsisolation.enable_for_account(account)  # must not raise


def test_teardown_account_calls_unmount_and_disable(fake_lsnsctl, isolated_db):
    calls, _ = fake_lsnsctl
    account = Account(username="nstest1", status="terminating", uid=2000, gid=2000)
    nsisolation.teardown_account(account)
    subcommands = [c[-1] for c in calls]
    assert "unmount" in subcommands
    assert "disable-uid" in subcommands


def test_teardown_account_one_failure_does_not_block_the_other(monkeypatch, isolated_db):
    calls = []

    def fake_run(args, timeout=15):
        calls.append(args[-1])
        if args[-1] == "unmount":
            return ProcResult(args=args, returncode=1, stdout="", stderr="boom")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(nsisolation, "run", fake_run)
    account = Account(username="nstest1", status="terminating", uid=2000, gid=2000)
    nsisolation.teardown_account(account)  # must not raise despite unmount failing
    assert "unmount" in calls
    assert "disable-uid" in calls


def test_teardown_account_no_uid_is_noop():
    account = Account(username="nouid", status="terminating", uid=None, gid=None)
    nsisolation.teardown_account(account)  # must not raise


def _wait_for_job(job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = nsisolation.get_bulk_enable_job({"job_id": job_id})
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.02)
    pytest.fail("bulk-enable job did not finish in time")


def test_trigger_bulk_enable_runs_all_active_accounts(fake_lsnsctl, isolated_db):
    with write_session() as session:
        make_account(session, username="acct1", uid=2001)
        make_account(session, username="acct2", uid=2002)
        make_account(session, username="acct3", status="terminated", uid=2003)  # excluded: not active

    triggered = nsisolation.trigger_bulk_enable({})
    assert triggered["total"] == 2  # terminated account excluded
    job = _wait_for_job(triggered["id"])
    assert job["status"] == "completed"
    assert job["completed_count"] == 2
    assert {r["username"] for r in job["results"]} == {"acct1", "acct2"}
    assert all(r["ok"] for r in job["results"])


def test_trigger_bulk_enable_stops_at_first_failure(monkeypatch, isolated_db):
    with write_session() as session:
        make_account(session, username="acct1", uid=2001)
        make_account(session, username="acct2", uid=1000)  # below min_uid -- will fail
        make_account(session, username="acct3", uid=2003)  # must never be attempted

    def fake_run(args, timeout=15):
        subcommand = args[-1]
        if subcommand == "get-min-uid":
            return ProcResult(args=args, returncode=0, stdout="", stderr="[INFO] Minimum UID: 1001")
        if subcommand == "list-disabled-uids":
            return ProcResult(args=args, returncode=0, stdout="[]", stderr="")
        if subcommand == "enable-uid" and "1000" in args:
            return ProcResult(args=args, returncode=1, stdout="", stderr="Specified uid: 1000 < minimum uid: 1001")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(nsisolation, "run", fake_run)

    triggered = nsisolation.trigger_bulk_enable({})
    assert triggered["total"] == 3
    job = _wait_for_job(triggered["id"])
    assert job["status"] == "failed"
    assert "acct2" in job["error"]
    usernames_attempted = [r["username"] for r in job["results"]]
    assert usernames_attempted == ["acct1", "acct2"]  # acct3 never reached
    assert job["results"][0]["ok"] is True
    assert job["results"][1]["ok"] is False


def test_bulk_enable_rechecks_account_status_before_lsnsctl(fake_lsnsctl, monkeypatch, isolated_db):
    calls, _responses = fake_lsnsctl
    submitted = []
    monkeypatch.setattr(nsisolation._executor, "submit", lambda fn, *args: submitted.append((fn, args)))
    with write_session() as session:
        account = make_account(session, username="acct1", uid=2001)

    triggered = nsisolation.trigger_bulk_enable({})

    with write_session() as session:
        session.get(Account, account.id).status = "terminated"

    fn, args = submitted[0]
    fn(*args)

    with write_session() as session:
        job = session.get(NamespaceMigrationJob, triggered["id"])
        assert job.status == "failed"
        assert "no longer active" in job.error
    assert not any(call[-1] == "enable-uid" for call in calls)


def test_get_bulk_enable_job_unknown_id_raises(isolated_db):
    with pytest.raises(nsisolation.NamespaceError, match="not found"):
        nsisolation.get_bulk_enable_job({"job_id": 999999})


def test_enable_uid_writes_audit_log_entry(fake_lsnsctl, isolated_db):
    nsisolation.enable_uid(2000)
    with write_session() as session:
        entries = session.scalars(select(AuditLog).where(AuditLog.op == "lsnsctl.enable-uid")).all()
    assert len(entries) == 1
    assert entries[0].actor == "system"
    assert entries[0].role == "daemon"
    assert entries[0].target == "2000"
    assert entries[0].result == "ok"


def test_disable_uid_failure_writes_failed_audit_entry(monkeypatch, isolated_db):
    def fake_run(args, timeout=15):
        return ProcResult(args=args, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(nsisolation, "run", fake_run)
    with pytest.raises(nsisolation.NamespaceError):
        nsisolation.disable_uid(2000)
    with write_session() as session:
        entries = session.scalars(select(AuditLog).where(AuditLog.op == "lsnsctl.disable-uid")).all()
    assert len(entries) == 1
    assert entries[0].result == "failed"
    assert "boom" in entries[0].detail


def test_unmount_uid_writes_audit_log_entry(fake_lsnsctl, isolated_db):
    nsisolation.unmount_uid(2000)
    with write_session() as session:
        entries = session.scalars(select(AuditLog).where(AuditLog.op == "lsnsctl.unmount")).all()
    assert len(entries) == 1
    assert entries[0].result == "ok"


def test_get_min_uid_does_not_write_audit_log(fake_lsnsctl, isolated_db):
    """Read-only lookups aren't audit-worthy events, matching this
    project's existing account.get/db.list convention."""
    nsisolation.get_min_uid()
    with write_session() as session:
        entries = session.scalars(select(AuditLog)).all()
    assert len(entries) == 0


def test_health_summary_counts_and_anomalies(fake_lsnsctl, isolated_db):
    calls, responses = fake_lsnsctl
    responses["list-disabled-uids"] = {"stdout": '["2001"]'}  # eligible but disabled -- anomaly
    with write_session() as session:
        make_account(session, username="enabled1", uid=2000)
        make_account(session, username="anomaly1", uid=2001)
        make_account(session, username="belowfloor", uid=500)  # not eligible
        make_account(session, username="notactive", uid=2002, status="suspended")  # excluded

    summary = nsisolation.health_summary({})
    assert summary["min_uid"] == 1001
    assert summary["total_active_accounts"] == 3  # suspended excluded
    assert summary["enabled_count"] == 1
    assert summary["not_yet_eligible_count"] == 1
    assert summary["anomaly_count"] == 1
    assert summary["anomalies"] == [{"username": "anomaly1", "uid": 2001}]


def test_health_summary_no_anomalies_when_all_enabled(fake_lsnsctl, isolated_db):
    with write_session() as session:
        make_account(session, username="acct1", uid=2000)
        make_account(session, username="acct2", uid=2001)

    summary = nsisolation.health_summary({})
    assert summary["anomaly_count"] == 0
    assert summary["anomalies"] == []
    assert summary["enabled_count"] == 2
