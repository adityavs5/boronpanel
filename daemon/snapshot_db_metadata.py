"""Private recovery metadata for panel-owned, local MariaDB database users.

Password hashes belong only inside encrypted repositories/private staging, never
in API responses or the API-readable panel database. Stored SQL is never executed.
"""
import json
import os
import re

from daemon import mariadb
from daemon.snapshot_databases import _database_name
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


def capture(username, grants):
    validate_username(username)
    entries = []
    connection = mariadb._connect()
    try:
        with connection.cursor() as cursor:
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
    return {'format': 1, 'username': username, 'databases': entries}


def write_metadata(path, metadata):
    """Create once inside the already validated private backup staging directory."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(metadata, handle, sort_keys=True)


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
