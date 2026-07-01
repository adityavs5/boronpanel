"""Per-account MariaDB database/user CRUD (Phase d)."""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, DatabaseGrant
from shared.validation import ValidationError, validate_db_identifier, validate_password_strength, validate_username

from daemon import mariadb


def _grant_dict(grant: DatabaseGrant) -> dict:
    return {
        "id": grant.id,
        "account_id": grant.account_id,
        "db_name": grant.db_name,
        "db_user": grant.db_user,
        "created_at": grant.created_at.isoformat() if grant.created_at else None,
    }


def _scoped_name(username: str, suffix: str) -> str:
    validate_db_identifier(suffix, max_len=40)
    name = f"{username}_{suffix}"
    return validate_db_identifier(name)


def create_database(params: dict) -> dict:
    username = validate_username(params["username"])
    suffix = params["name"]
    db_name = _scoped_name(username, suffix)
    db_user = db_name  # one DB <-> one DB user in v1, matches CyberPanel's <webuser>_<name> pattern for both

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise RuntimeError(f"cannot add a database to an account in status '{account.status}'")
        existing = session.scalar(select(DatabaseGrant).where(DatabaseGrant.db_name == db_name))
        if existing is not None:
            raise RuntimeError(f"database '{db_name}' already exists")

    if mariadb.database_exists(db_name):
        raise RuntimeError(f"database '{db_name}' already exists in MariaDB")

    password = validate_password_strength(params["password"]) if params.get("password") else mariadb.generate_password()

    mariadb.create_database(db_name)
    try:
        mariadb.create_db_user(db_user, password)
        mariadb.grant_all(db_name, db_user)
    except Exception:
        mariadb.drop_db_user(db_user)
        mariadb.drop_database(db_name)
        raise

    with write_session() as session:
        grant = DatabaseGrant(account_id=account.id, db_name=db_name, db_user=db_user)
        session.add(grant)
        session.flush()
        result = _grant_dict(grant)

    result["password"] = password
    return result


def list_databases(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        grants = session.scalars(select(DatabaseGrant).where(DatabaseGrant.account_id == account.id)).all()
        return {"databases": [_grant_dict(g) for g in grants]}


def drop_database(params: dict) -> dict:
    username = validate_username(params["username"])
    suffix = params["name"]
    db_name = _scoped_name(username, suffix)

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        grant = session.scalar(
            select(DatabaseGrant).where(DatabaseGrant.account_id == account.id, DatabaseGrant.db_name == db_name)
        )
        if grant is None:
            raise RuntimeError(f"database '{db_name}' not found for account '{username}'")
        db_user = grant.db_user
        session.delete(grant)

    mariadb.drop_database(db_name)
    mariadb.drop_db_user(db_user)
    return {"db_name": db_name, "status": "dropped"}


def change_password(params: dict) -> dict:
    username = validate_username(params["username"])
    suffix = params["name"]
    db_name = _scoped_name(username, suffix)
    new_password = validate_password_strength(params["password"]) if params.get("password") else mariadb.generate_password()

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        grant = session.scalar(
            select(DatabaseGrant).where(DatabaseGrant.account_id == account.id, DatabaseGrant.db_name == db_name)
        )
        if grant is None:
            raise RuntimeError(f"database '{db_name}' not found for account '{username}'")
        db_user = grant.db_user

    mariadb.set_password(db_user, new_password)
    return {"db_name": db_name, "db_user": db_user, "password": new_password}


def terminate_account_databases(account: Account) -> None:
    """TERMINATE_HOOKS entry: drop every DB + DB user this account owns.
    Idempotent -- safe even if the account never had a database."""
    with write_session() as session:
        grants = session.scalars(select(DatabaseGrant).where(DatabaseGrant.account_id == account.id)).all()
        grant_data = [(g.db_name, g.db_user, g.id) for g in grants]

    for db_name, db_user, grant_id in grant_data:
        mariadb.drop_database(db_name)
        mariadb.drop_db_user(db_user)
        with write_session() as session:
            grant = session.get(DatabaseGrant, grant_id)
            if grant is not None:
                session.delete(grant)
