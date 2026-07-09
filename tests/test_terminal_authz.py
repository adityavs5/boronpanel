"""Cross-account authorization for the web terminal (Phase 8 f7). The
terminal grants a real interactive shell as the account's Linux user, so
_authorized() is the gate that must keep one customer out of another's shell.
Previously it had zero test coverage."""
from api.routers import terminal
from api.security import Identity


def _identity(role, account_id):
    return Identity(panel_user_id=1, username="panellogin", role=role, account_id=account_id, auth_method="session")


def _add_account(username, uid):
    from shared.db import write_session
    from shared.models import Account

    with write_session() as db:
        acct = Account(username=username, uid=uid, gid=uid, status="active")
        db.add(acct)
        db.flush()
        return acct.id


def test_admin_authorized_for_any_account(isolated_db):
    _add_account("alice", 6001)
    assert terminal._authorized(_identity("admin", None), "alice") is True


def test_customer_authorized_for_own_account(isolated_db):
    account_id = _add_account("alice", 6001)
    assert terminal._authorized(_identity("customer", account_id), "alice") is True


def test_customer_denied_for_other_account(isolated_db):
    alice_id = _add_account("alice", 6001)
    _add_account("bob", 6002)
    # Alice's session must not be able to open Bob's terminal.
    assert terminal._authorized(_identity("customer", alice_id), "bob") is False


def test_customer_with_no_account_denied(isolated_db):
    _add_account("alice", 6001)
    assert terminal._authorized(_identity("customer", None), "alice") is False


def test_customer_with_stale_account_id_denied(isolated_db):
    # account_id pointing at a row that no longer exists must not authorize.
    assert terminal._authorized(_identity("customer", 999999), "alice") is False
