import json
import logging
from types import SimpleNamespace

import pymysql
import pytest

from daemon import mariadb, snapshot_db_metadata as metadata, snapshot_databases as database
from shared.config import settings
from tests.test_snapshot_databases import sql_server, sql


def entry():
    return metadata.capture('alpha', [SimpleNamespace(db_name='alpha_wp', db_user='alpha_wp')])['databases'][0]


def test_deleted_database_and_login_recover_original_credentials_and_data(sql, caplog):
    connection, work = sql
    caplog.set_level(logging.INFO)
    saved = entry()
    metadata.write_metadata(work / 'recovery.json', {'databases': [saved]})
    assert (work / 'recovery.json').stat().st_mode & 0o777 == 0o600
    assert 'test-only-hosting-password' not in (work / 'recovery.json').read_text()
    dump = database.dump_database('alpha_wp', work / 'backup.sql', work)
    mariadb.drop_database('alpha_wp')
    mariadb.drop_db_user('alpha_wp')
    metadata.recreate_missing('alpha', saved)
    database.restore_database('alpha_wp', dump, work)
    scoped = pymysql.connect(unix_socket=settings.mariadb_socket, user='alpha_wp', password='test-only-hosting-password')
    try:
        with scoped.cursor() as cursor:
            cursor.execute('SELECT content FROM alpha_wp.posts WHERE id=1')
            assert cursor.fetchone()[0] == 'Original WordPress content ☕'
            for statement in ('SELECT * FROM bravo_wp.private_data', 'CREATE TABLE alphaXwp.escape (id INT)', 'SELECT * FROM mysql.user'):
                with pytest.raises(pymysql.Error):
                    cursor.execute(statement)
    finally:
        scoped.close()
    with connection.cursor() as cursor:
        cursor.execute("SELECT DEFAULT_CHARACTER_SET_NAME,DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='alpha_wp'")
        assert cursor.fetchone() == (saved['charset'], saved['collation'])
    assert saved['password_hash'] not in caplog.text
    assert 'test-only-hosting-password' not in caplog.text


def test_existing_database_or_login_is_never_adopted(sql):
    saved = entry()
    with pytest.raises(Exception, match='Database already exists'):
        metadata.recreate_missing('alpha', saved)
    mariadb.drop_database('alpha_wp')
    with pytest.raises(Exception, match='login already exists'):
        metadata.recreate_missing('alpha', saved)
    assert not mariadb.database_exists('alpha_wp')
    assert mariadb.user_exists('alpha_wp')


def test_failed_reconstruction_removes_only_new_resources(sql, monkeypatch):
    saved = entry()
    mariadb.drop_database('alpha_wp')
    mariadb.drop_db_user('alpha_wp')
    monkeypatch.setattr(mariadb, 'grant_exact_database', lambda *args: (_ for _ in ()).throw(RuntimeError('Grant failed')))
    with pytest.raises(RuntimeError, match='Grant failed'):
        metadata.recreate_missing('alpha', saved)
    assert not mariadb.database_exists('alpha_wp')
    assert not mariadb.user_exists('alpha_wp')
    assert mariadb.database_exists('bravo_wp')


@pytest.mark.parametrize('change', [
    {'name': 'bravo_wp'}, {'user': 'bravo_wp'}, {'host': '%'},
    {'plugin': 'unix_socket'}, {'password_hash': 'invalid'},
    {'charset': 'utf8mb4; DROP DATABASE bravo_wp'}, {'collation': '`injected`'},
])
def test_recovery_metadata_rejects_foreign_ownership_and_sql_injection(sql, change):
    saved = {**entry(), **change}
    with pytest.raises(Exception):
        metadata.recreate_missing('alpha', saved)
    assert mariadb.database_exists('alpha_wp')
    assert mariadb.database_exists('bravo_wp')


def test_private_metadata_refuses_overwrite_or_symlink(tmp_path):
    existing = tmp_path / 'existing'
    existing.write_text('preserve')
    link = tmp_path / 'link'
    link.symlink_to(existing)
    for path in (existing, link):
        with pytest.raises(FileExistsError):
            metadata.write_metadata(path, {})
    assert existing.read_text() == 'preserve'


@pytest.mark.parametrize('payload', [
    [], {'format': 99, 'username': 'alpha', 'databases': []},
    {'format': 1, 'username': 'bravo', 'databases': []},
    {'format': 1, 'username': 'alpha', 'databases': {}},
    {'format': 1, 'username': 'alpha', 'databases': [{'name': 'alpha_wp'}]},
])
def test_read_recovery_metadata_rejects_invalid_or_foreign_documents(tmp_path, payload):
    path = tmp_path / 'metadata.json'
    path.write_text(json.dumps(payload))
    with pytest.raises(Exception, match='Invalid database recovery metadata'):
        metadata.read_metadata(path, 'alpha')


def test_read_recovery_metadata_rejects_symlinks_and_oversized_files(tmp_path):
    path = tmp_path / 'metadata.json'
    path.write_text('{}')
    link = tmp_path / 'link'
    link.symlink_to(path)
    with pytest.raises(Exception, match='metadata file'):
        metadata.read_metadata(link, 'alpha')
    with path.open('wb') as handle:
        handle.truncate(4 * 1024 * 1024 + 1)
    with pytest.raises(Exception, match='metadata file'):
        metadata.read_metadata(path, 'alpha')


@pytest.mark.parametrize('missing', ['database', 'login'])
def test_partial_resource_repair_preserves_surviving_resource(sql, missing):
    connection, work = sql
    saved = entry()
    if missing == 'database':
        mariadb.drop_database('alpha_wp')
    else:
        mariadb.drop_db_user('alpha_wp')
    result = metadata.repair_missing('alpha', saved)
    assert result == {'created_database': missing == 'database', 'created_login': missing == 'login'}
    assert metadata.repair_missing('alpha', saved) == {'created_database': False, 'created_login': False}
    scoped = pymysql.connect(unix_socket=settings.mariadb_socket, user='alpha_wp', password='test-only-hosting-password')
    try:
        with scoped.cursor() as cursor:
            if missing == 'login':
                cursor.execute('SELECT content FROM alpha_wp.posts WHERE id=1')
                assert cursor.fetchone()[0] == 'Original WordPress content ☕'
            else:
                cursor.execute('CREATE TABLE alpha_wp.recovered_access (id INT)')
            with pytest.raises(pymysql.Error):
                cursor.execute('SELECT * FROM bravo_wp.private_data')
    finally:
        scoped.close()


def test_partial_repair_refuses_changed_surviving_password(sql):
    saved = entry()
    mariadb.drop_database('alpha_wp')
    mariadb.set_password('alpha_wp', 'different-test-only-password')
    with pytest.raises(Exception, match='differs'):
        metadata.repair_missing('alpha', saved)
    assert not mariadb.database_exists('alpha_wp')
    scoped = pymysql.connect(unix_socket=settings.mariadb_socket, user='alpha_wp', password='different-test-only-password')
    scoped.close()


def test_partial_repair_failure_does_not_remove_existing_database(sql, monkeypatch):
    connection, work = sql
    saved = entry()
    mariadb.drop_db_user('alpha_wp')
    monkeypatch.setattr(mariadb, 'grant_exact_database', lambda *args: (_ for _ in ()).throw(RuntimeError('grant failed')))
    with pytest.raises(RuntimeError):
        metadata.repair_missing('alpha', saved)
    assert not mariadb.user_exists('alpha_wp')
    with connection.cursor() as cursor:
        cursor.execute('SELECT content FROM alpha_wp.posts WHERE id=1')
        assert cursor.fetchone()[0] == 'Original WordPress content ☕'
