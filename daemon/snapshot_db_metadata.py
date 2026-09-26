"""Private recovery metadata for panel-owned, local MariaDB database users.

Password hashes belong only inside encrypted repositories/private staging, never
in API responses or the API-readable panel database. Stored SQL is never executed.
"""
from daemon.database_operations import serialized
import json
import os
import re

from daemon import mariadb
from daemon.snapshot_databases import _database_name
from shared.db import write_session
from shared.models import Account, DatabaseGrant, DatabaseUser, DatabaseUserGrant
from sqlalchemy import select
from shared.validation import ValidationError, validate_db_identifier, validate_username


def validate_entry(username, entry):
    validate_username(username)
    if not isinstance(entry, dict):
        raise ValidationError('Invalid database recovery metadata')
    name, user = entry.get('name'), entry.get('user')
    _database_name(name)
    validate_db_identifier(user)
    if not name.startswith(username + '_') or not user.startswith(username + '_'):
        raise ValidationError('Database recovery metadata belongs to another account')
    if entry.get('host') != 'localhost' or entry.get('plugin') != 'mysql_native_password':
        raise ValidationError('Unsupported database recovery authentication')
    if not isinstance(entry.get('password_hash'), str) or not re.fullmatch(r'\*[0-9A-F]{40}', entry['password_hash']):
        raise ValidationError('Invalid database recovery authentication hash')
    for key in ('charset', 'collation'):
        if not isinstance(entry.get(key), str) or not re.fullmatch(r'[a-zA-Z0-9_]{1,64}', entry[key]):
            raise ValidationError('Invalid database recovery character set')
    return {key: entry[key] for key in ('name', 'user', 'host', 'plugin', 'password_hash', 'charset', 'collation')}


def _validate_user(username, entry):
    if not isinstance(entry, dict):
        raise ValidationError('Invalid database user recovery metadata')
    user = validate_db_identifier(entry.get('user'))
    if not user.startswith(username + '_'):
        raise ValidationError('Database user recovery metadata belongs to another account')
    host = mariadb.validate_database_host(entry.get('host'))
    if entry.get('plugin') != 'mysql_native_password' or not re.fullmatch(r'\*[0-9A-F]{40}', str(entry.get('password_hash', ''))):
        raise ValidationError('Unsupported database recovery authentication')
    return {'user': user, 'host': host, 'plugin': 'mysql_native_password', 'password_hash': entry['password_hash']}


def _validate_grant(username, entry, database_names, user_keys):
    if not isinstance(entry, dict):
        raise ValidationError('Invalid database grant recovery metadata')
    database = _database_name(entry.get('database')) or entry['database']
    user = validate_db_identifier(entry.get('user'))
    host = mariadb.validate_database_host(entry.get('host'))
    if database not in database_names or (user, host) not in user_keys:
        raise ValidationError('Database grant recovery metadata has an unknown resource')
    preset, privileges = mariadb.normalize_database_privileges(entry.get('preset', 'all'), entry.get('privileges'))
    return {'database': database, 'user': user, 'host': host, 'preset': preset, 'privileges': list(privileges)}


class RecoveryMetadata(dict):
    def __init__(self, databases, users=(), grants=()):
        super().__init__(databases)
        self.users = list(users)
        self.grants = list(grants)


@serialized
def capture(username, grants):
    validate_username(username)
    entries = []
    if not grants:
        return {'format': 2, 'username': username, 'databases': entries, 'users': [], 'grants': []}
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise ValidationError('Database recovery account no longer exists')
        users = session.scalars(select(DatabaseUser).where(DatabaseUser.account_id == account.id)).all()
        links = session.execute(
            select(DatabaseGrant.db_name, DatabaseUser.db_user, DatabaseUser.host,
                   DatabaseUserGrant.privileges, DatabaseUserGrant.privilege_list)
            .join(DatabaseUserGrant, DatabaseUserGrant.database_grant_id == DatabaseGrant.id)
            .join(DatabaseUser, DatabaseUser.id == DatabaseUserGrant.database_user_id)
            .where(DatabaseGrant.account_id == account.id)
        ).all()
    registered = {(user.db_user, user.host): user for user in users}
    # Old installations may not have materialized the compatibility rows yet.
    for grant in grants:
        registered.setdefault((grant.db_user, 'localhost'), None)
    connection = mariadb._connect()
    try:
        with connection.cursor() as cursor:
            user_entries = []
            for user, host in sorted(registered):
                validate_db_identifier(user)
                host = mariadb.validate_database_host(host)
                cursor.execute("SELECT plugin, authentication_string, Password, ssl_type FROM mysql.user WHERE User=%s AND Host=%s", (user, host))
                auth = cursor.fetchone()
                if not auth or auth[3]:
                    raise ValidationError('Database login is missing or uses unsupported TLS requirements: ' + user + '@' + host)
                user_entries.append(_validate_user(username, {'user': user, 'host': host, 'plugin': auth[0] or 'mysql_native_password', 'password_hash': auth[1] or auth[2]}))
            for grant in grants:
                name, user = grant.db_name, grant.db_user
                _database_name(name)
                validate_db_identifier(user)
                if not name.startswith(username + '_') or not user.startswith(username + '_'):
                    raise ValidationError('Database registration belongs to another account')
                cursor.execute('SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (name,))
                schema = cursor.fetchone()
                cursor.execute("SELECT plugin, authentication_string, Password, ssl_type FROM mysql.user WHERE User=%s AND Host='localhost'", (user,))
                auth = cursor.fetchone()
                if not schema or not auth:
                    raise ValidationError('Database or registered login is missing: ' + name)
                if auth[3]:
                    raise ValidationError('Database login has custom TLS requirements; recovery metadata is unsupported')
                entry = dict(name=name, user=user, host='localhost', plugin=auth[0] or 'mysql_native_password',
                             password_hash=auth[1] or auth[2], charset=schema[0], collation=schema[1])
                entries.append(validate_entry(username, entry))
    finally:
        connection.close()
    grant_entries = []
    linked = {(db_name, user, host) for db_name, user, host, _preset, _values in links}
    for db_name, user, host, preset, values in links:
        custom = [item for item in (values or '').split(',') if item]
        grant_entries.append(_validate_grant(username, {'database': db_name, 'user': user, 'host': host,
            'preset': preset, 'privileges': custom}, {item['name'] for item in entries}, {(item['user'], item['host']) for item in user_entries}))
    for entry in entries:
        key = (entry['name'], entry['user'], 'localhost')
        if key not in linked:
            grant_entries.append(_validate_grant(username, {'database': entry['name'], 'user': entry['user'],
                'host': 'localhost', 'preset': 'all', 'privileges': []}, {item['name'] for item in entries}, {(item['user'], item['host']) for item in user_entries}))
    return {'format': 2, 'username': username, 'databases': entries, 'users': user_entries, 'grants': grant_entries}


def write_metadata(path, metadata):
    """Create once inside the already validated private backup staging directory."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(metadata, handle, sort_keys=True)


@serialized
def recreate_missing(username, entry):
    """Recreate an entirely missing database/login pair; caller checks registration.

    Existing names cause an explicit conflict, never adoption or password changes.
    Do not use this primitive until verified snapshot metadata and current panel
    ownership have been checked by the account restore coordinator.
    """
    entry = validate_entry(username, entry)
    name, user = entry['name'], entry['user']
    connection = mariadb._connect()
    created_database = created_user = False
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (name,))
            if cursor.fetchone():
                raise ValidationError('Database already exists; use the existing-database restore path')
            cursor.execute('SELECT User FROM mysql.user WHERE User=%s', (user,))
            if cursor.fetchone():
                raise ValidationError('Database login already exists; resolve ownership before reconstruction')
            cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET {entry['charset']} COLLATE {entry['collation']}")
            created_database = True
            cursor.execute(f"CREATE USER '{user}'@'localhost' IDENTIFIED BY PASSWORD %s", (entry['password_hash'],))
            created_user = True
        mariadb.grant_exact_database(name, user)
    except Exception:
        # Compensate only resources created by this call, never pre-existing data.
        if created_user:
            mariadb.drop_db_user(user)
        if created_database:
            mariadb.drop_database(name)
        raise
    finally:
        connection.close()


def read_metadata(path, username):
    """Read only verified, privately restored metadata; never return it to an API."""
    import stat
    from pathlib import Path
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
        raise ValidationError('Invalid database recovery metadata file')
    try:
        payload = json.loads(path.read_text())
        if payload.get('format') not in (1, 2) or payload.get('username') != username:
            raise ValueError()
        entries = payload['databases']
        if not isinstance(entries, list):
            raise ValueError()
        result = {}
        for entry in entries:
            value = validate_entry(username, entry)
            if value['name'] in result:
                raise ValueError()
            result[value['name']] = value
        if payload.get('format') == 1:
            users = [_validate_user(username, {'user': value['user'], 'host': value['host'],
                'plugin': value['plugin'], 'password_hash': value['password_hash']}) for value in result.values()]
            grants = [{'database': value['name'], 'user': value['user'], 'host': value['host'],
                'preset': 'all', 'privileges': list(mariadb.HOSTED_DB_PRIVILEGE_NAMES)} for value in result.values()]
            return RecoveryMetadata(result, users, grants)
        raw_users, raw_grants = payload.get('users'), payload.get('grants')
        if not isinstance(raw_users, list) or not isinstance(raw_grants, list):
            raise ValueError()
        users = [_validate_user(username, item) for item in raw_users]
        if len({(item['user'], item['host']) for item in users}) != len(users):
            raise ValueError()
        grants = [_validate_grant(username, item, set(result), {(user['user'], user['host']) for user in users}) for item in raw_grants]
        if len({(item['database'], item['user'], item['host']) for item in grants}) != len(grants):
            raise ValueError()
        return RecoveryMetadata(result, users, grants)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValidationError('Invalid database recovery metadata') from None


@serialized
def restore_access(username, database_names, metadata):
    """Restore account-owned logins/grants after selected SQL data is safe."""
    validate_username(username)
    selected = {_database_name(name) or name for name in database_names}
    users_by_key = {(item['user'], item['host']): item for item in getattr(metadata, 'users', [])}
    grants = [item for item in getattr(metadata, 'grants', []) if item['database'] in selected]
    needed = {(item['user'], item['host']) for item in grants}
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise ValidationError('Database recovery account no longer exists')
        databases = {}
        for database in selected:
            row = session.scalar(select(DatabaseGrant).where(DatabaseGrant.account_id == account.id, DatabaseGrant.db_name == database))
            if row is None:
                raise ValidationError('Database registration is missing after reconstruction: ' + database)
            databases[database] = row
        existing_rows = {(row.db_user, row.host): row for row in session.scalars(select(DatabaseUser).where(DatabaseUser.account_id == account.id)).all()}
        # Upgraded installations can still have the original one-login-per-
        # database registration without a DatabaseUser compatibility row.  A
        # matching DatabaseGrant is authoritative ownership evidence; expose
        # only that exact localhost login through the new model before applying
        # the saved grants.  Unrelated live MariaDB users remain conflicts.
        for database in databases.values():
            key = (database.db_user, 'localhost')
            if key not in needed or key in existing_rows:
                continue
            other = session.scalar(select(DatabaseUser).where(
                DatabaseUser.db_user == key[0], DatabaseUser.host == key[1],
                DatabaseUser.account_id != account.id,
            ))
            if other:
                raise ValidationError('A restored database login belongs to another account')
            row = DatabaseUser(account_id=account.id, db_user=key[0], host=key[1])
            session.add(row)
            session.flush()
            existing_rows[key] = row
        for key in needed:
            saved = users_by_key[key]
            other = session.scalar(select(DatabaseUser).where(DatabaseUser.db_user == key[0], DatabaseUser.host == key[1], DatabaseUser.account_id != account.id))
            if other:
                raise ValidationError('A restored database login belongs to another account')
            if key not in existing_rows:
                if mariadb.user_exists(*key):
                    raise ValidationError('An unregistered database login conflicts with recovery metadata: ' + key[0] + '@' + key[1])
                mariadb.create_db_user_from_hash(saved['user'], saved['password_hash'], saved['host'])
                row = DatabaseUser(account_id=account.id, db_user=saved['user'], host=saved['host'])
                session.add(row); session.flush(); existing_rows[key] = row
            elif not mariadb.user_exists(*key):
                mariadb.create_db_user_from_hash(saved['user'], saved['password_hash'], saved['host'])
        for saved in grants:
            database = databases[saved['database']]
            user = existing_rows[(saved['user'], saved['host'])]
            mariadb.grant_database_privileges(database.db_name, user.db_user, user.host,
                preset=saved['preset'], custom=saved['privileges'])
            link = session.scalar(select(DatabaseUserGrant).where(DatabaseUserGrant.database_grant_id == database.id,
                DatabaseUserGrant.database_user_id == user.id))
            if link is None:
                link = DatabaseUserGrant(database_grant_id=database.id, database_user_id=user.id)
                session.add(link)
            link.privileges = saved['preset']
            link.privilege_list = ','.join(saved['privileges']) if saved['preset'] == 'custom' else None
    return {'users': len(needed), 'grants': len(grants)}


def resource_state(username, entry):
    """Inspect surviving resources without exposing authentication material."""
    import hmac
    entry = validate_entry(username, entry)
    connection = mariadb._connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (entry['name'],))
            database_exists = cursor.fetchone() is not None
            cursor.execute('SELECT Host,plugin,authentication_string,Password,ssl_type FROM mysql.user WHERE User=%s', (entry['user'],))
            users = cursor.fetchall()
            matches = len(users) == 1 and users[0][0] == 'localhost' and (users[0][1] or 'mysql_native_password') == 'mysql_native_password' and not users[0][4] and hmac.compare_digest(users[0][2] or users[0][3], entry['password_hash'])
            return {'database': database_exists, 'login': bool(users), 'login_matches': matches}
    finally:
        connection.close()


@serialized
def repair_missing(username, entry):
    """Repair only a pair whose current panel registration the caller verified.

    Surviving logins must match the saved local authentication identity. Their
    passwords are never reset. Reapplying the exact grant makes an interrupted
    create/create-user/grant sequence retryable without adopting another login.
    """
    entry = validate_entry(username, entry)
    state = resource_state(username, entry)
    if state['login'] and not state['login_matches']:
        raise ValidationError('Surviving database login differs from the recovery metadata')
    name, user = entry['name'], entry['user']
    connection = mariadb._connect()
    created_database = created_user = False
    try:
        with connection.cursor() as cursor:
            if not state['database']:
                cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET {entry['charset']} COLLATE {entry['collation']}")
                created_database = True
            if not state['login']:
                cursor.execute(f"CREATE USER '{user}'@'localhost' IDENTIFIED BY PASSWORD %s", (entry['password_hash'],))
                created_user = True
        mariadb.grant_exact_database(name, user)
    except Exception:
        if created_user:
            mariadb.drop_db_user(user)
        if created_database:
            mariadb.drop_database(name)
        raise
    finally:
        connection.close()
    return {'created_database': created_database, 'created_login': created_user}
