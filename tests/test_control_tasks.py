import json

from daemon import control_tasks, jobcredentials, tasks
from shared.db import write_session
from shared.models import Account, AuditLog, ControlTask


def _identity_params(**values):
    return {
        **values,
        "_authenticated_username": "administrator",
        "_authenticated_role": "admin",
        "_authenticated_account_id": None,
        "_source_ip": "192.0.2.10",
    }


def _quiet_audit(monkeypatch):
    monkeypatch.setattr(control_tasks.audit, "begin_action_trace", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(control_tasks.audit, "end_action_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(control_tasks.audit, "record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(control_tasks.audit, "record_account_event", lambda *_args, **_kwargs: None)


def test_account_create_task_encrypts_input_and_reveals_result_once(isolated_db, monkeypatch):
    submitted = []
    monkeypatch.setattr(control_tasks._executor, "submit", lambda fn, task_id: submitted.append((fn, task_id)))
    _quiet_audit(monkeypatch)
    seen = {}
    supplied_secret = "".join(("Supplied", "-pass-42!"))
    generated_secret = "".join(("Generated", "-pass-42!"))

    def execute(row, params):
        seen.update(params)
        with write_session() as db:
            account = Account(username="taskacct")
            db.add(account)
            db.flush()
            account_id = account.id
        return (
            {"id": account_id, "username": "taskacct"},
            generated_secret,
            account_id,
            "taskacct",
        )

    monkeypatch.setattr(control_tasks, "_execute", execute)
    started = control_tasks.start_account_create(_identity_params(
        username="taskacct", password=supplied_secret,
    ))

    with write_session() as db:
        row = db.get(ControlTask, started["id"])
        assert row.status == "queued"
        assert supplied_secret not in json.dumps(row.safe_params)
        assert supplied_secret not in (row.secret_params_enc or "")
        assert jobcredentials.reveal(row.secret_params_enc) == json.dumps({"password": supplied_secret})

    submitted[0][0](submitted[0][1])
    assert seen["password"] == supplied_secret
    status = control_tasks.get_task({"task_id": started["id"]})
    assert status["status"] == "completed"
    assert "initial_password" not in status["result"]
    revealed = control_tasks.reveal_result({"task_id": started["id"]})
    assert revealed["initial_password"] == generated_secret
    assert "initial_password" not in control_tasks.reveal_result({"task_id": started["id"]})


def test_failed_control_task_has_safe_reference_and_emits_event(isolated_db, monkeypatch):
    submitted = []
    events = []
    monkeypatch.setattr(control_tasks._executor, "submit", lambda fn, task_id: submitted.append((fn, task_id)))
    monkeypatch.setattr(control_tasks, "_execute", lambda *_args: (_ for _ in ()).throw(RuntimeError("secret vendor output")))
    monkeypatch.setattr(control_tasks.events, "emit", lambda *args, **kwargs: events.append((args, kwargs)))
    _quiet_audit(monkeypatch)
    started = control_tasks.start_account_create(_identity_params(username="failedacct"))

    submitted[0][0](submitted[0][1])

    status = control_tasks.get_task({"task_id": started["id"]})
    assert status["status"] == "failed"
    assert len(status["error_reference"]) == 16
    assert "secret vendor output" not in json.dumps(status)
    assert events[0][0][0] == "task.failed"
    assert events[0][1]["reference"] == status["error_reference"]


def test_control_task_is_part_of_unified_history(isolated_db, monkeypatch):
    monkeypatch.setattr(control_tasks._executor, "submit", lambda *_args, **_kwargs: None)
    started = control_tasks.start_account_create(_identity_params(username="queuedacct"))

    history = tasks.list_tasks({"_authenticated_role": "admin", "limit": 100})

    row = next(item for item in history["tasks"] if item["id"] == started["id"] and item["kind"] == "control-operation")
    assert row["label"] == "Create account"
    assert row["status"] == "queued"


def test_restart_marks_inflight_control_task_failed_without_replaying(isolated_db, monkeypatch):
    with write_session() as db:
        row = ControlTask(
            operation="account.rename", username="oldname", status="running",
            progress_pct=15, progress_message="Renaming", safe_params={"username": "oldname", "new_username": "newname"},
            initiated_by="administrator", initiated_role="admin",
        )
        db.add(row)
        db.flush()
        task_id = row.id
    monkeypatch.setattr(control_tasks._executor, "submit", lambda *_args, **_kwargs: None)

    recovered = control_tasks.recover_pending()

    assert recovered["interrupted"] == [task_id]
    status = control_tasks.get_task({"task_id": task_id})
    assert status["status"] == "failed"
    assert status["error_reference"]


def test_control_task_audit_never_stores_input_password(isolated_db, monkeypatch):
    submitted = []
    monkeypatch.setattr(
        control_tasks._executor,
        "submit",
        lambda fn, task_id: submitted.append((fn, task_id)),
    )

    def execute(_row, params):
        with write_session() as db:
            account = Account(username="auditacct")
            db.add(account)
            db.flush()
            account_id = account.id
        return ({"id": account_id, "username": "auditacct"}, None, account_id, "auditacct")

    monkeypatch.setattr(control_tasks, "_execute", execute)
    secret = "".join(("Never-store-this", "-password-42!"))
    started = control_tasks.start_account_create(
        _identity_params(username="auditacct", password=secret)
    )

    submitted[0][0](started["id"])

    with write_session() as db:
        row = db.query(AuditLog).filter_by(op="account.create").one()
        serialized = json.dumps({"params": row.params, "changes": row.changes})
        assert row.params["password"] == "***"
        assert secret not in serialized
