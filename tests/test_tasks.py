import datetime as dt

import pytest

from daemon import tasks
from shared.db import write_session
from shared.models import (
    Account,
    ActionStep,
    AuditLog,
    BackupDestination,
    BackupJob,
    UpdateJob,
    utcnow,
)
from shared.validation import ValidationError


def _seed():
    with write_session() as db:
        first = Account(username="taskone", status="active")
        second = Account(username="tasktwo", status="active")
        destination = BackupDestination(name="local", kind="local", local_path="/tmp")
        db.add_all([first, second, destination])
        db.flush()
        one = BackupJob(
            account_id=first.id, kind="full", status="completed",
            destination_id=destination.id, progress_message="Backup ready",
            request_id="req-task-one", completed_at=utcnow(),
        )
        two = BackupJob(
            account_id=second.id, kind="full", status="failed",
            destination_id=destination.id,
            error="vendor leaked token=must-not-appear (reference: abcdef0123456789)",
            request_id="req-task-two", completed_at=utcnow(),
        )
        update = UpdateJob(
            kind="update", status="failed", from_version="3.1.0", to_version="3.2.0",
            initiated_by="admin", error="internal failure (reference: 0123456789abcdef)",
            request_id="req-update", completed_at=utcnow(),
        )
        db.add_all([one, two, update])
        db.flush()
        audit = AuditLog(
            actor="admin", role="admin", op="backup.job.trigger", target="taskone",
            result="ok", request_id="req-task-one",
        )
        db.add(audit)
        db.flush()
        db.add(ActionStep(
            audit_id=audit.id, name="create archive", started_at=utcnow(),
            duration_ms=12, result="ok", stdout_tail="redacted output",
        ))
        return first.id, second.id, one.id, two.id


def test_customer_task_history_is_strictly_account_scoped(isolated_db):
    first_id, _second_id, first_job, _second_job = _seed()

    result = tasks.list_tasks({
        "_authenticated_role": "customer",
        "_authenticated_account_id": first_id,
        "limit": 100,
    })

    assert [row["key"] for row in result["tasks"]] == [f"backup:{first_job}"]
    assert result["tasks"][0]["feature_url"] == "/backups"


def test_admin_sees_account_and_server_tasks_without_raw_job_errors(isolated_db):
    _first_id, _second_id, _first_job, second_job = _seed()

    result = tasks.list_tasks({"_authenticated_role": "admin", "limit": 100})
    keys = {row["key"] for row in result["tasks"]}
    assert f"backup:{second_job}" in keys
    assert any(row["kind"] == "update" and row["account"] is None for row in result["tasks"])
    failed = next(row for row in result["tasks"] if row["key"] == f"backup:{second_job}")
    assert failed["error_reference"] == "abcdef0123456789"
    assert "must-not-appear" not in failed["detail"]


def test_task_detail_contains_request_timeline(isolated_db):
    first_id, _second_id, first_job, _second_job = _seed()

    detail = tasks.get_task({
        "kind": "backup", "task_id": first_job,
        "_authenticated_role": "customer", "_authenticated_account_id": first_id,
    })

    assert detail["request_id"] == "req-task-one"
    assert detail["timeline"] == [{
        "name": "create archive",
        "started_at": detail["timeline"][0]["started_at"],
        "duration_ms": 12,
        "result": "ok",
        "exit_code": None,
        "stdout": "redacted output",
        "stderr": None,
    }]


def test_customer_cannot_fetch_another_accounts_task(isolated_db):
    first_id, _second_id, _first_job, second_job = _seed()

    with pytest.raises(ValidationError, match="Task not found"):
        tasks.get_task({
            "kind": "backup", "task_id": second_job,
            "_authenticated_role": "customer", "_authenticated_account_id": first_id,
        })


def test_task_filters_and_unsupported_retry(isolated_db):
    _first_id, _second_id, _first_job, _second_job = _seed()
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    result = tasks.list_tasks({
        "_authenticated_role": "admin", "status": "failed", "kind": "backup",
        "date_from": today, "date_to": today, "limit": 100,
    })
    assert len(result["tasks"]) == 1
    with pytest.raises(ValidationError, match="does not support retry"):
        tasks.retry_task({"kind": "backup", "task_id": result["tasks"][0]["id"]})


def test_invalid_customer_scope_is_rejected(isolated_db):
    _seed()
    with pytest.raises(ValidationError, match="Customer account"):
        tasks.list_tasks({"_authenticated_role": "customer", "limit": 100})
