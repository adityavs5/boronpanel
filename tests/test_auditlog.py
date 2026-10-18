import asyncio
import csv
import io
import pytest
from fastapi import HTTPException

from api import security as sec
from api.routers import auditlog
from daemon import audit as daemon_audit
from shared.db import write_session
from shared.models import AuditLog
from shared.request_context import request_context


def _seed(isolated_db):
    with write_session() as session:
        session.add_all(
            [
                AuditLog(actor="admin1", role="admin", op="account.create", target="demo1", params={}, result="ok", detail=""),
                AuditLog(actor="admin1", role="admin", op="account.terminate", target="demo1", params={}, result="ok", detail=""),
                AuditLog(actor="cust1", role="customer", op="ftp.create", target="demo2", params={}, result="failed", detail="bad password"),
                AuditLog(actor="cust2", role="customer", op="files.write", target="demo3", params={}, result="ok", detail=""),
            ]
        )


def test_query_rows_no_filters_returns_all(isolated_db):
    _seed(isolated_db)
    rows, total = auditlog._query_rows("", "", "", "", "", 1, 50)
    assert total == 4
    assert len(rows) == 4


def test_daemon_audit_caps_untrusted_target(isolated_db):
    daemon_audit.record("anonymous", "anonymous", "auth.login.begin", "x" * 500, {}, "ok")
    with write_session() as session:
        row = session.query(AuditLog).one()
        assert row.target == "x" * 128


def test_daemon_audit_captures_request_id(isolated_db):
    with request_context("request-audit-1"):
        daemon_audit.record("admin", "admin", "account.test", "demo", {}, "failed", "reference: 0123456789abcdef")
    with write_session() as session:
        row = session.query(AuditLog).one()
        assert row.request_id == "request-audit-1"
    payload = auditlog._row_dict(row)
    assert payload["request_id"] == "request-audit-1"
    assert payload["reference"] == "0123456789abcdef"


def test_query_rows_filters_by_actor(isolated_db):
    _seed(isolated_db)
    rows, total = auditlog._query_rows("admin1", "", "", "", "", 1, 50)
    assert total == 2
    assert all(r.actor == "admin1" for r in rows)


def test_query_rows_filters_by_op_substring(isolated_db):
    _seed(isolated_db)
    rows, total = auditlog._query_rows("", "account.", "", "", "", 1, 50)
    assert total == 2


def test_query_rows_filters_by_result(isolated_db):
    _seed(isolated_db)
    rows, total = auditlog._query_rows("", "", "failed", "", "", 1, 50)
    assert total == 1
    assert rows[0].actor == "cust1"


def test_query_rows_filters_by_target(isolated_db):
    _seed(isolated_db)
    rows, total = auditlog._query_rows("", "", "", "demo2", "", 1, 50)
    assert total == 1


def test_query_rows_general_search_matches_any_field(isolated_db):
    _seed(isolated_db)
    rows, total = auditlog._query_rows("", "", "", "", "bad password", 1, 50)
    assert total == 1
    assert rows[0].detail == "bad password"


def test_query_rows_pagination(isolated_db):
    _seed(isolated_db)
    rows_page1, total = auditlog._query_rows("", "", "", "", "", 1, 2)
    rows_page2, _ = auditlog._query_rows("", "", "", "", "", 2, 2)
    assert len(rows_page1) == 2
    assert len(rows_page2) == 2
    assert total == 4
    assert {r.id for r in rows_page1}.isdisjoint({r.id for r in rows_page2})


def test_query_rows_newest_first(isolated_db):
    _seed(isolated_db)
    rows, _ = auditlog._query_rows("", "", "", "", "", 1, 50)
    ids = [r.id for r in rows]
    assert ids == sorted(ids, reverse=True)


def test_page_size_capped_at_max(isolated_db):
    _seed(isolated_db)
    rows, total = auditlog._query_rows("", "", "", "", "", 1, 100000)
    assert total == 4  # didn't error, just capped internally


def test_list_audit_log_requires_admin(isolated_db):
    _seed(isolated_db)
    customer_identity = sec.Identity(1, "cust1", "customer", 5, "session")
    with pytest.raises(HTTPException) as exc_info:
        auditlog.list_audit_log(identity=customer_identity)
    assert exc_info.value.status_code == 403


def test_list_audit_log_returns_shaped_entries(isolated_db):
    _seed(isolated_db)
    admin_identity = sec.Identity(1, "admin1", "admin", None, "session")
    result = auditlog.list_audit_log(identity=admin_identity)
    assert result["total"] == 4
    assert len(result["entries"]) == 4
    assert "created_at" in result["entries"][0]


def test_export_csv_requires_admin(isolated_db):
    _seed(isolated_db)
    customer_identity = sec.Identity(1, "cust1", "customer", 5, "session")
    with pytest.raises(HTTPException) as exc_info:
        auditlog.export_csv(identity=customer_identity)
    assert exc_info.value.status_code == 403


def test_export_csv_contains_all_rows(isolated_db):
    _seed(isolated_db)
    admin_identity = sec.Identity(1, "admin1", "admin", None, "session")
    response = auditlog.export_csv(identity=admin_identity)
    body = _body(response)
    assert "actor" in body.splitlines()[0]  # header row
    assert body.count("\n") >= 4  # header + 4 data rows (trailing newline tolerant)
    assert "admin1" in body
    assert "cust1" in body


def test_export_csv_respects_filters(isolated_db):
    _seed(isolated_db)
    admin_identity = sec.Identity(1, "admin1", "admin", None, "session")
    response = auditlog.export_csv(actor="admin1", identity=admin_identity)
    body = _body(response)
    assert "cust1" not in body
    assert "cust2" not in body


def test_export_csv_has_download_headers(isolated_db):
    _seed(isolated_db)
    admin_identity = sec.Identity(1, "admin1", "admin", None, "session")
    response = auditlog.export_csv(identity=admin_identity)
    assert "attachment" in response.headers["content-disposition"]
    assert response.media_type == "text/csv"


def test_no_delete_route_exists():
    """Goal: audit log 'cannot delete via UI' -- verified structurally by
    the complete absence of any DELETE/mutating route in this router."""
    all_methods = set()
    for route in list(auditlog.api_router.routes) + list(auditlog.ui_router.routes):
        all_methods |= set(route.methods)
    assert "DELETE" not in all_methods
    assert "PUT" not in all_methods
    assert "PATCH" not in all_methods


def _body(response):
    async def collect():
        return ''.join([chunk async for chunk in response.body_iterator])
    return asyncio.run(collect())


@pytest.mark.parametrize('value', ['=1+1', '+1+1', '-1+1', '@SUM(A1)', ' \t=1+1', '\n=1+1'])
def test_csv_formula_values_are_literal(value):
    assert auditlog._csv_cell(value) == "'" + value
    assert auditlog._csv_cell('normal text') == 'normal text'


def test_csv_stream_pages_without_duplicates_and_preserves_quoting(isolated_db):
    from shared.models import AuditLog
    from shared.db import write_session
    with write_session() as db:
        for index in range(501):
            db.add(AuditLog(actor='customer', role='customer', op='test',
                           target='=1+1', result='failed', detail='line one,\nline two'))
    identity = sec.Identity(1, 'admin', 'admin', None, 'session')
    response = auditlog.export_csv(identity=identity)
    rows = list(csv.DictReader(io.StringIO(_body(response))))
    assert len(rows) == len({row['id'] for row in rows}) == 501
    assert all(row['target'] == "'=1+1" and row['detail'] == 'line one,\nline two' for row in rows)
    assert response.headers['cache-control'] == 'no-store'


def test_error_alias_and_historical_read_visibility(isolated_db):
    _seed(isolated_db)
    with write_session() as db:
        db.add(AuditLog(actor='admin1',role='admin',op='bandwidth.ranking',target='',params={},result='ok',detail=''))
        db.add(AuditLog(actor='admin1',role='admin',op='bandwidth.ranking',target='',params={},result='failed',detail='failed read'))
    rows,total=auditlog._query_rows('', '', '', '', '', 1, 50)
    assert total == 5
    assert not any(row.op=='bandwidth.ranking' and row.result=='ok' for row in rows)
    assert auditlog._query_rows('', '', '', '', '', 1, 50, include_reads=True)[1] == 6
    errors,total=auditlog._query_rows('', '', 'error', '', '', 1, 50)
    assert total==2 and all(row.result=='failed' for row in errors)


def test_inventory_read_filter_and_export_preserve_failures_and_token_policy(isolated_db):
    from shared.token_policy import READ_ONLY_OPERATIONS

    inventories = auditlog.AUDIT_READ_OPERATIONS - READ_ONLY_OPERATIONS
    assert {"resources.overview", "namespace.overview", "snapshot.catalog.accounts"} <= inventories
    assert inventories.isdisjoint(READ_ONLY_OPERATIONS)
    with write_session() as db:
        for operation in inventories:
            for result in ("ok", "failed"):
                db.add(AuditLog(actor="admin", role="admin", op=operation, result=result))
        db.add(AuditLog(actor="admin", role="admin", op="resources.policy.account", result="ok"))
    rows, total = auditlog._query_rows("", "", "", "", "", 1, 50)
    assert total == len(inventories) + 1
    assert all(row.result == "failed" or row.op == "resources.policy.account" for row in rows)
    assert auditlog._query_rows("", "", "", "", "", 1, 100, include_reads=True)[1] == len(inventories) * 2 + 1
    identity = sec.Identity(1, "admin", "admin", None, "session")
    exported = list(csv.DictReader(io.StringIO(_body(auditlog.export_csv(identity=identity)))))
    assert len(exported) == len(inventories) + 1
    assert all(row["result"] == "failed" or row["op"] == "resources.policy.account" for row in exported)


def test_display_read_catalogue_preserves_mutating_and_credential_consuming_reads():
    from shared.token_policy import CREDENTIAL_CONSUMING_OPERATIONS

    assert auditlog.AUDIT_READ_OPERATIONS.isdisjoint(CREDENTIAL_CONSUMING_OPERATIONS)
    assert auditlog.AUDIT_READ_OPERATIONS.isdisjoint({
        "db.health",  # Optional repair=True changes database tables.
        "cf.zone_status",  # Optional check_now requests an activation check.
        "snapshot.destination.recovery_key", "support_bundle.consume", "taskops.reveal",
    })
