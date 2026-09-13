from pathlib import Path
import os
import pymysql
import pytest
from sqlalchemy import select

from daemon import mariadb, snapshot_jobs as jobs, snapshot_restores as restores
from shared.config import settings
from shared.db import write_session
from shared.models import Account, DatabaseGrant, SnapshotRun, SnapshotRestore
from tests.test_snapshot_databases import sql_server, sql


@pytest.fixture
def saved_database(sql, isolated_db, monkeypatch):
    connection, work = sql
    home = work/'homes'/'alpha'
    home.mkdir(parents=True)
    monkeypatch.setattr(settings, 'home_base', str(home.parent))
    monkeypatch.setattr(jobs._executor, 'submit', lambda *args: None)
    with write_session() as session:
        account = Account(username='alpha', status='active', uid=65534, gid=65534)
        session.add(account)
        session.flush()
        session.add(DatabaseGrant(account_id=account.id, db_name='alpha_wp', db_user='alpha_wp'))
    dest = jobs.create_destination({'name':'Partial recovery', 'path':str(work/'repo')})
    jobs.initialize_destination({'id':dest['id']})
    policy = jobs.save_policy({'name':'Databases', 'destination_id':dest['id'], 'components':['databases'], 'accounts':['alpha']})
    run_id = jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    jobs.execute_run(run_id)
    assert jobs._row(SnapshotRun, run_id).status == 'completed'
    return connection, run_id


def request(run_id):
    return restores.trigger({'username':'alpha', 'run_id':run_id, 'confirmation':'alpha', 'kind':'databases', 'databases':['alpha_wp']})


def assert_original_data():
    connection = pymysql.connect(unix_socket=settings.mariadb_socket, user='alpha_wp', password='test-only-hosting-password')
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT content FROM alpha_wp.posts WHERE id=1')
            assert cursor.fetchone()[0] == 'Original WordPress content ☕'
    finally:
        connection.close()


@pytest.mark.parametrize('missing', ['database', 'login'])
def test_queued_partial_recovery_and_previous_data(saved_database, missing):
    connection, run_id = saved_database
    if missing == 'database':
        mariadb.drop_database('alpha_wp')
    else:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE alpha_wp.posts SET content='before login repair'")
        mariadb.drop_db_user('alpha_wp')
    catalog = restores.database_options({'username':'alpha', 'run_id':run_id})
    assert catalog['databases'][0]['action'] == 'repair'
    restored = request(run_id)
    restores.execute(restored['id'])
    row = jobs._row(SnapshotRestore, restored['id'])
    assert row.status == 'completed', row.error
    assert_original_data()
    if missing == 'login':
        assert row.safety_snapshot_id
        undo = restores.undo({'username':'alpha', 'restore_id':row.id, 'confirmation':'alpha'})
        restores.execute(undo['id'])
        assert jobs._row(SnapshotRestore, undo['id']).status == 'completed'
        with connection.cursor() as cursor:
            cursor.execute('SELECT content FROM alpha_wp.posts WHERE id=1')
            assert cursor.fetchone()[0] == 'before login repair'
    else:
        assert row.safety_snapshot_id is None


def test_interrupted_create_grant_sequence_is_reconciled_on_retry(saved_database, monkeypatch):
    connection, run_id = saved_database
    mariadb.drop_database('alpha_wp')
    mariadb.drop_db_user('alpha_wp')
    interrupted = request(run_id)
    grant = mariadb.grant_exact_database
    def interrupt(*args):
        raise SystemExit('simulated worker termination before grant')
    monkeypatch.setattr(mariadb, 'grant_exact_database', interrupt)
    with pytest.raises(SystemExit):
        restores.execute(interrupted['id'])
    first = jobs._row(SnapshotRestore, interrupted['id'])
    assert first.status == 'running'
    assert first.summary['reconstruction_pending'] == ['alpha_wp']
    assert mariadb.database_exists('alpha_wp') and mariadb.user_exists('alpha_wp')
    monkeypatch.setattr(mariadb, 'grant_exact_database', grant)
    restores.recover_restores()
    assert jobs._row(SnapshotRestore, first.id).status == 'failed'
    assert restores.database_options({'username':'alpha', 'run_id':run_id})['databases'][0]['action'] == 'repair'
    retry = request(run_id)
    restores.execute(retry['id'])
    row = jobs._row(SnapshotRestore, retry['id'])
    assert row.status == 'completed', row.error
    assert jobs._row(SnapshotRestore, first.id).summary['reconstruction_pending'] == []
    assert_original_data()


def test_mixed_restore_undo_keeps_recreated_database(saved_database):
    connection, old_run = saved_database
    with connection.cursor() as cursor:
        cursor.execute('CREATE DATABASE alpha_mix CHARACTER SET utf8mb4')
        cursor.execute("CREATE USER 'alpha_mix'@'localhost' IDENTIFIED BY 'test-only-mixed-password'")
        cursor.execute('CREATE TABLE alpha_mix.posts (content TEXT)')
        cursor.execute("INSERT INTO alpha_mix.posts VALUES ('backed-up mixed data')")
    mariadb.grant_exact_database('alpha_mix', 'alpha_mix')
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == 'alpha'))
        session.add(DatabaseGrant(account_id=account.id, db_name='alpha_mix', db_user='alpha_mix'))
    policy_id = jobs._row(SnapshotRun, old_run).policy_id
    run_id = jobs.queue_policy({'id':policy_id})['run_ids'][0]
    jobs.execute_run(run_id)
    assert jobs._row(SnapshotRun, run_id).status == 'completed'
    mariadb.drop_database('alpha_wp')
    mariadb.drop_db_user('alpha_wp')
    with connection.cursor() as cursor:
        cursor.execute("UPDATE alpha_mix.posts SET content='before mixed restore'")
    selected = restores.trigger({'username':'alpha', 'run_id':run_id, 'confirmation':'alpha', 'kind':'databases', 'databases':['alpha_wp','alpha_mix']})
    restores.execute(selected['id'])
    row = jobs._row(SnapshotRestore, selected['id'])
    assert row.status == 'completed', row.error
    assert row.summary['safety_databases'] == ['alpha_mix']
    assert_original_data()
    undo = restores.undo({'username':'alpha', 'restore_id':row.id, 'confirmation':'alpha'})
    restores.execute(undo['id'])
    assert jobs._row(SnapshotRestore, undo['id']).status == 'completed'
    assert_original_data()
    with connection.cursor() as cursor:
        cursor.execute('SELECT content FROM alpha_mix.posts')
        assert cursor.fetchone()[0] == 'before mixed restore'
