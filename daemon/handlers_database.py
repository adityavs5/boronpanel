"""Per-account MariaDB database/user CRUD (Phase d)."""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, DatabaseGrant, DatabaseUser, DatabaseUserGrant
from shared.validation import ValidationError, validate_db_identifier, validate_password_strength, validate_username

from daemon.database_operations import serialized
from daemon import mariadb, resource_limits


def _grant_dict(grant: DatabaseGrant) -> dict:
    return {
        "id": grant.id,
        "account_id": grant.account_id,
        "db_name": grant.db_name,
        "db_user": grant.db_user,
        "created_at": grant.created_at.isoformat() if grant.created_at else None,
    }


def _ensure_user_rows(session, account_id: int) -> None:
    """Idempotently expose legacy one-user databases through the new model."""
    grants = session.scalars(select(DatabaseGrant).where(DatabaseGrant.account_id == account_id)).all()
    for grant in grants:
        user = session.scalar(select(DatabaseUser).where(
            DatabaseUser.account_id == account_id,
            DatabaseUser.db_user == grant.db_user,
            DatabaseUser.host == "localhost",
        ))
        if user is None:
            user = DatabaseUser(account_id=account_id, db_user=grant.db_user, host="localhost")
            session.add(user)
            session.flush()
        link = session.scalar(select(DatabaseUserGrant).where(
            DatabaseUserGrant.database_grant_id == grant.id,
            DatabaseUserGrant.database_user_id == user.id,
        ))
        if link is None:
            session.add(DatabaseUserGrant(database_grant_id=grant.id, database_user_id=user.id))


def _user_dict(session, user: DatabaseUser) -> dict:
    rows = session.execute(
        select(DatabaseGrant.db_name, DatabaseUserGrant.privileges)
        .join(DatabaseUserGrant, DatabaseUserGrant.database_grant_id == DatabaseGrant.id)
        .where(DatabaseUserGrant.database_user_id == user.id)
        .order_by(DatabaseGrant.db_name)
    ).all()
    return {
        "id": user.id,
        "db_user": user.db_user,
        "host": user.host,
        "databases": [{"db_name": name, "privileges": privileges} for name, privileges in rows],
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _scoped_name(username: str, suffix: str) -> str:
    validate_db_identifier(suffix, max_len=40)
    name = f"{username}_{suffix}"
    return validate_db_identifier(name)


def _resolve_existing_db_name(username: str, name: str) -> str:
    """Look up an *existing* database's canonical db_name from a caller-
    supplied identifier that may be either the bare suffix (legacy UI
    forms, ``db.create``'s own contract, existing tests) or the
    already-fully-qualified db_name (the React dashboard, which only ever
    has the full name -- it comes straight back from ``db.list``, never a
    bare suffix). Root cause of a real "database not found" bug: passing
    an already-scoped name through ``_scoped_name`` double-prefixed it
    (``demo1_shop`` -> ``demo1_demo1_shop``), and phpMyAdmin's token
    endpoint independently reimplemented the same scoping with the same
    flaw. Tolerant only for *lookups* of something that must already
    exist -- ``db.create`` keeps calling ``_scoped_name`` directly and
    unconditionally prefixes, so a legitimately suffix-starting-with-
    username create request is never reinterpreted."""
    candidate = validate_db_identifier(name, max_len=64)
    prefix = f"{username}_"
    if candidate.startswith(prefix):
        return candidate
    return _scoped_name(username, candidate)


@serialized
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
        resource_limits.require_capacity(session, account.id, "database")
        account_id = account.id

    if mariadb.database_exists(db_name):
        raise RuntimeError(f"database '{db_name}' already exists in MariaDB")
    if mariadb.user_exists(db_user):
        raise RuntimeError(f"database user '{db_user}' already exists in MariaDB; create the database with another name")

    password = validate_password_strength(params["password"]) if params.get("password") else mariadb.generate_password()

    mariadb.create_database(db_name)
    try:
        mariadb.create_db_user(db_user, password)
        mariadb.grant_all(db_name, db_user)
    except Exception:
        mariadb.drop_db_user(db_user)
        mariadb.drop_database(db_name)
        raise

    try:
        with write_session() as session:
            grant = DatabaseGrant(account_id=account_id, db_name=db_name, db_user=db_user)
            session.add(grant)
            session.flush()
            user = DatabaseUser(account_id=account_id, db_user=db_user, host="localhost")
            session.add(user)
            session.flush()
            session.add(DatabaseUserGrant(database_grant_id=grant.id, database_user_id=user.id))
            result = _grant_dict(grant)
    except Exception:
        mariadb.drop_db_user(db_user)
        mariadb.drop_database(db_name)
        raise

    result["password"] = password
    return result


def list_databases(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        _ensure_user_rows(session, account.id)
        session.flush()
        grants = session.scalars(select(DatabaseGrant).where(DatabaseGrant.account_id == account.id)).all()
        result = []
        for grant in grants:
            users = session.scalars(
                select(DatabaseUser)
                .join(DatabaseUserGrant, DatabaseUserGrant.database_user_id == DatabaseUser.id)
                .where(DatabaseUserGrant.database_grant_id == grant.id)
                .order_by(DatabaseUser.db_user)
            ).all()
            result.append({**_grant_dict(grant), "users": [u.db_user for u in users]})
        return {"databases": result}


def list_users(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        _ensure_user_rows(session, account.id)
        session.flush()
        users = session.scalars(select(DatabaseUser).where(
            DatabaseUser.account_id == account.id,
        ).order_by(DatabaseUser.db_user)).all()
        return {"users": [_user_dict(session, user) for user in users]}


@serialized
def create_user(params: dict) -> dict:
    username = validate_username(params["username"])
    db_user = _scoped_name(username, params["name"])
    password = validate_password_strength(params["password"]) if params.get("password") else mariadb.generate_password()
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        _ensure_user_rows(session, account.id)
        if session.scalar(select(DatabaseUser).where(DatabaseUser.db_user == db_user, DatabaseUser.host == "localhost")):
            raise RuntimeError(f"database user '{db_user}' already exists")
        account_id = account.id
    if mariadb.user_exists(db_user):
        raise RuntimeError(f"database user '{db_user}' already exists in MariaDB")
    mariadb.create_db_user(db_user, password)
    try:
        with write_session() as session:
            row = DatabaseUser(account_id=account_id, db_user=db_user, host="localhost")
            session.add(row)
            session.flush()
            result = _user_dict(session, row)
    except Exception:
        mariadb.drop_db_user(db_user)
        raise
    return {**result, "password": password}


def _owned_user_and_database(session, username: str, db_user_name: str, db_name: str):
    account = session.scalar(select(Account).where(Account.username == username))
    if account is None:
        raise RuntimeError(f"account '{username}' not found")
    _ensure_user_rows(session, account.id)
    user_name = _resolve_existing_db_name(username, db_user_name)
    database_name = _resolve_existing_db_name(username, db_name)
    user = session.scalar(select(DatabaseUser).where(
        DatabaseUser.account_id == account.id, DatabaseUser.db_user == user_name,
        DatabaseUser.host == "localhost",
    ))
    database = session.scalar(select(DatabaseGrant).where(
        DatabaseGrant.account_id == account.id, DatabaseGrant.db_name == database_name,
    ))
    if user is None:
        raise RuntimeError(f"database user '{user_name}' not found")
    if database is None:
        raise RuntimeError(f"database '{database_name}' not found")
    return user, database


@serialized
def grant_user(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        user, database = _owned_user_and_database(session, username, params["user"], params["database"])
        existing = session.scalar(select(DatabaseUserGrant).where(
            DatabaseUserGrant.database_grant_id == database.id,
            DatabaseUserGrant.database_user_id == user.id,
        ))
        user_id, database_id, db_user, db_name = user.id, database.id, user.db_user, database.db_name
    mariadb.grant_exact_database(db_name, db_user)
    with write_session() as session:
        link = session.scalar(select(DatabaseUserGrant).where(
            DatabaseUserGrant.database_grant_id == database_id,
            DatabaseUserGrant.database_user_id == user_id,
        ))
        if link is None:
            session.add(DatabaseUserGrant(database_grant_id=database_id, database_user_id=user_id))
    return {"db_name": db_name, "db_user": db_user, "status": "granted"}


@serialized
def revoke_user(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        user, database = _owned_user_and_database(session, username, params["user"], params["database"])
        if database.db_user == user.db_user:
            raise ValidationError("The database's original application user cannot be revoked while the database exists")
        link = session.scalar(select(DatabaseUserGrant).where(
            DatabaseUserGrant.database_grant_id == database.id,
            DatabaseUserGrant.database_user_id == user.id,
        ))
        if link is None:
            raise RuntimeError("database user is not assigned to this database")
        link_id, db_user, db_name = link.id, user.db_user, database.db_name
    mariadb.revoke_all(db_name, db_user)
    with write_session() as session:
        link = session.get(DatabaseUserGrant, link_id)
        if link is not None:
            session.delete(link)
    return {"db_name": db_name, "db_user": db_user, "status": "revoked"}


@serialized
def change_user_password(params: dict) -> dict:
    username = validate_username(params["username"])
    password = validate_password_strength(params["password"]) if params.get("password") else mariadb.generate_password()
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        _ensure_user_rows(session, account.id)
        name = _resolve_existing_db_name(username, params["user"])
        user = session.scalar(select(DatabaseUser).where(DatabaseUser.account_id == account.id, DatabaseUser.db_user == name))
        if user is None:
            raise RuntimeError(f"database user '{name}' not found")
        db_user = user.db_user
    mariadb.set_password(db_user, password)
    return {"db_user": db_user, "password": password}


@serialized
def drop_user(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        _ensure_user_rows(session, account.id)
        name = _resolve_existing_db_name(username, params["user"])
        user = session.scalar(select(DatabaseUser).where(DatabaseUser.account_id == account.id, DatabaseUser.db_user == name))
        if user is None:
            raise RuntimeError(f"database user '{name}' not found")
        if session.scalar(select(DatabaseGrant.id).where(DatabaseGrant.account_id == account.id, DatabaseGrant.db_user == name)):
            raise ValidationError("The database's original application user is removed when its database is deleted")
        user_id, db_user = user.id, user.db_user
    mariadb.drop_db_user(db_user)
    with write_session() as session:
        links = session.scalars(select(DatabaseUserGrant).where(DatabaseUserGrant.database_user_id == user_id)).all()
        for link in links:
            session.delete(link)
        session.flush()
        user = session.get(DatabaseUser, user_id)
        if user is not None:
            session.delete(user)
    return {"db_user": db_user, "status": "dropped"}


@serialized
def drop_database(params: dict) -> dict:
    username = validate_username(params["username"])
    db_name = _resolve_existing_db_name(username, params["name"])

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
        grant_id = grant.id

    mariadb.drop_database(db_name)
    mariadb.drop_db_user(db_user)
    with write_session() as session:
        grant = session.get(DatabaseGrant, grant_id)
        if grant is not None:
            links = session.scalars(select(DatabaseUserGrant).where(DatabaseUserGrant.database_grant_id == grant_id)).all()
            linked_user_ids = [link.database_user_id for link in links]
            for link in links:
                session.delete(link)
            session.flush()
            session.delete(grant)
            session.flush()
            for user_id in linked_user_ids:
                user = session.get(DatabaseUser, user_id)
                if user is not None and user.db_user == db_user:
                    session.delete(user)
    return {"db_name": db_name, "status": "dropped"}


@serialized
def change_password(params: dict) -> dict:
    username = validate_username(params["username"])
    db_name = _resolve_existing_db_name(username, params["name"])
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


@serialized
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
                links = session.scalars(select(DatabaseUserGrant).where(DatabaseUserGrant.database_grant_id == grant_id)).all()
                linked_user_ids = [link.database_user_id for link in links]
                for link in links:
                    session.delete(link)
                session.flush()
                session.delete(grant)
                session.flush()
                for user_id in linked_user_ids:
                    user = session.get(DatabaseUser, user_id)
                    if user is not None and user.db_user == db_user:
                        session.delete(user)
