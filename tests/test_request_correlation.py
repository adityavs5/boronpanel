from __future__ import annotations

import asyncio

from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, inspect
from starlette.requests import Request

from api.main import _access_log
from shared import models
from shared.db import write_session
from shared.request_context import (
    ContextThreadPoolExecutor,
    current_request_id,
    normalize_request_id,
    request_context,
)


JOB_MODELS = (
    models.BackupJob, models.WordPressJob, models.MalwareScanJob,
    models.RestoreJob, models.AppInstallJob, models.NamespaceMigrationJob,
    models.CpanelImportJob, models.BulkActionJob, models.CommandRun,
    models.ImapMigrationJob, models.UpdateJob, models.SnapshotRun,
    models.SnapshotDownload, models.SnapshotRestore,
    models.SnapshotDestinationOperation, models.PanelConfigJob,
    models.AccountArchiveImportJob, models.StackJob,
    models.DnsClusterOutbox, models.WebhookDelivery,
    models.BackupNotificationDelivery, models.BackupNotificationDigest,
)


def _request(*, host="203.0.113.8", request_id=None):
    headers = [] if request_id is None else [(b"x-request-id", request_id.encode())]
    return Request({
        "type": "http", "method": "GET", "path": "/api/v1/test",
        "headers": headers, "scheme": "https", "server": ("panel.test", 443),
        "client": (host, 12345),
    })


def test_request_id_validation_is_bounded():
    assert normalize_request_id("safe-id_1:2.3") == "safe-id_1:2.3"
    assert normalize_request_id("../unsafe") is None
    assert normalize_request_id("x" * 65) is None


def test_context_executor_carries_request_id():
    with request_context("job-request-1"):
        with ContextThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(current_request_id).result(timeout=2) == "job-request-1"


def test_every_existing_job_model_has_nullable_indexed_request_id_default():
    for model in JOB_MODELS:
        column = model.__table__.c.request_id
        assert column.nullable is True, model.__name__
        assert column.index is True, model.__name__
        assert column.default is not None, model.__name__


def test_additive_migration_adds_job_and_audit_request_indexes(tmp_path):
    from shared.db import _apply_additive_migrations

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    table_names = [models.AuditLog.__tablename__, *(model.__tablename__ for model in JOB_MODELS)]
    with engine.begin() as connection:
        for table in table_names:
            connection.exec_driver_sql(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    _apply_additive_migrations(engine)
    inspector = inspect(engine)
    for table in table_names:
        assert "request_id" in {column["name"] for column in inspector.get_columns(table)}
        assert f"ix_{table}_request_id" in {index["name"] for index in inspector.get_indexes(table)}


def test_job_row_inherits_request_id_when_created(isolated_db):
    with request_context("job-create-request-1"):
        with write_session() as db:
            row = models.PanelConfigJob(
                status="pending", admin_port=2222, customer_port=8443, initiated_by="admin"
            )
            db.add(row)
            db.flush()
            ident = row.id
    with write_session() as db:
        assert db.get(models.PanelConfigJob, ident).request_id == "job-create-request-1"


def test_local_upstream_request_id_is_accepted(monkeypatch, tmp_path):
    from api import logsetup
    monkeypatch.setattr(logsetup.settings, "log_dir", str(tmp_path))
    logsetup.reset()
    request = _request(host="127.0.0.1", request_id="proxy-request-7")

    async def ok(_request):
        return JSONResponse({"ok": True})

    response = asyncio.run(_access_log(request, ok))
    assert response.headers["x-boron-request-id"] == "proxy-request-7"


def test_remote_upstream_request_id_is_replaced_and_direct_error_is_extended(monkeypatch, tmp_path):
    from api import logsetup
    monkeypatch.setattr(logsetup.settings, "log_dir", str(tmp_path))
    logsetup.reset()
    request = _request(request_id="attacker-chosen")

    async def denied(_request):
        return JSONResponse({"detail": "admin role required"}, status_code=403)

    response = asyncio.run(_access_log(request, denied))
    assert response.headers["x-boron-request-id"] != "attacker-chosen"
    assert response.headers["x-boron-error-reference"]
    body = response.body
    assert response.headers["x-boron-error-reference"].encode() in body
