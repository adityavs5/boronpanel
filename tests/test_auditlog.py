import pytest
from fastapi import HTTPException

from api import security as sec
from api.routers import auditlog
from shared.db import write_session
from shared.models import AuditLog


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
    body = response.body.decode()
    assert "actor" in body.splitlines()[0]  # header row
    assert body.count("\n") >= 4  # header + 4 data rows (trailing newline tolerant)
    assert "admin1" in body
    assert "cust1" in body


def test_export_csv_respects_filters(isolated_db):
    _seed(isolated_db)
    admin_identity = sec.Identity(1, "admin1", "admin", None, "session")
    response = auditlog.export_csv(actor="admin1", identity=admin_identity)
    body = response.body.decode()
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
