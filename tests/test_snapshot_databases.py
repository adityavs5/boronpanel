import os
import logging
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import pymysql
import pytest

from daemon import mariadb, snapshot_databases as database
from shared.config import settings


@pytest.fixture(scope='module')
def sql_server():
    if not shutil.which('mariadbd') or not shutil.which('mariadb-install-db'):pytest.skip('MariaDB required')
    with tempfile.TemporaryDirectory(prefix='boron-db-restore-',dir='/tmp') as temporary:
        root=Path(temporary)
        datadir=root/'data';socket=root/'mysql.sock'
        initialized=subprocess.run(['/usr/bin/mariadb-install-db','--no-defaults',f'--datadir={datadir}',
            '--auth-root-authentication-method=normal','--skip-test-db'],capture_output=True,text=True,timeout=120)
        assert initialized.returncode==0,initialized.stderr
        process=subprocess.Popen(['/usr/sbin/mariadbd','--no-defaults',f'--datadir={datadir}',f'--socket={socket}',
            f'--pid-file={root}/pid','--skip-networking',f'--log-error={root}/server.log',
            '--innodb-buffer-pool-size=32M','--innodb-log-file-size=16M','--aria-pagecache-buffer-size=8M',
            *(['--user=root'] if os.geteuid()==0 else [])],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(200):
                if process.poll() is not None:pytest.fail((root/'server.log').read_text())
                try:
                    connection=pymysql.connect(unix_socket=str(socket),user='root',autocommit=True)
                    connection.close();break
                except pymysql.Error:time.sleep(.1)
            else:pytest.fail('Isolated MariaDB did not start')
            yield str(socket)
        finally:
            process.terminate()
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.kill();process.wait()


@pytest.fixture
def sql(sql_server,monkeypatch,tmp_path):
    connection=pymysql.connect(unix_socket=sql_server,user='root',autocommit=True)
    with connection.cursor() as cursor:
        for name in ['alpha_wp','alphaXwp','bravo_wp']:
            cursor.execute(f'DROP DATABASE IF EXISTS `{name}`');cursor.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4')
        cursor.execute("DROP USER IF EXISTS 'snapshot_operator'@'localhost'")
        cursor.execute("CREATE USER 'snapshot_operator'@'localhost' IDENTIFIED BY 'test-only-service-password'")
        cursor.execute(f"GRANT {mariadb.HOSTED_DB_PRIVILEGES}, CREATE USER, RELOAD, PROCESS ON *.* TO 'snapshot_operator'@'localhost' WITH GRANT OPTION")
        cursor.execute('CREATE TABLE alpha_wp.posts (id INT PRIMARY KEY, content LONGTEXT, image LONGBLOB)')
        cursor.execute('INSERT INTO alpha_wp.posts VALUES (%s,%s,%s)',(1,'Original WordPress content ☕',b'\0\1\xff\n'))
        cursor.execute('CREATE TABLE bravo_wp.private_data (value VARCHAR(30))')
        cursor.execute("INSERT INTO bravo_wp.private_data VALUES ('must remain private')")
    monkeypatch.setattr(settings,'mariadb_socket',sql_server)
    monkeypatch.setattr(settings,'mariadb_admin_user','snapshot_operator')
    monkeypatch.setitem(settings.secrets,'MARIADB_DAEMON_PASSWORD','test-only-service-password')
    try:yield connection,tmp_path
    finally:connection.close()


def test_real_streamed_database_export_and_restore(sql,caplog,monkeypatch):
    caplog.set_level(logging.INFO,logger='borond.proc')
    monkeypatch.setattr(database.secrets,'token_urlsafe',lambda length:'test-only-temporary-password-marker')
    connection,work=sql
    path=database.dump_database('alpha_wp',work/'backup.sql',work)
    with connection.cursor() as cursor:cursor.execute("UPDATE alpha_wp.posts SET content='changed',image=NULL")
    result=database.restore_database('alpha_wp',path,work)
    assert result['bytes_read']==path.stat().st_size
    with connection.cursor() as cursor:
        cursor.execute('SELECT content,image FROM alpha_wp.posts WHERE id=1')
        assert cursor.fetchone()==('Original WordPress content ☕',b'\0\1\xff\n')
        cursor.execute("SELECT COUNT(*) FROM mysql.user WHERE User LIKE 'boronrestoreworker%'")
        assert cursor.fetchone()[0]==0
    assert not list(work.glob('client-*.cnf'))
    assert '--defaults-file=' in caplog.text
    assert 'test-only-service-password' not in caplog.text
    assert 'test-only-temporary-password-marker' not in caplog.text


def test_import_login_is_exact_database_only(sql):
    connection,work=sql
    with database.import_login('alpha_wp') as (username,password):
        scoped=pymysql.connect(unix_socket=settings.mariadb_socket,user=username,password=password,autocommit=True)
        try:
            with scoped.cursor() as cursor:
                cursor.execute('SELECT COUNT(*) FROM alpha_wp.posts');assert cursor.fetchone()[0]==1
                for statement in ['SELECT * FROM bravo_wp.private_data','CREATE TABLE alphaXwp.leak (id INT)','SELECT * FROM mysql.user']:
                    with pytest.raises(pymysql.Error):cursor.execute(statement)
        finally:scoped.close()
    with connection.cursor() as cursor:
        cursor.execute('SELECT COUNT(*) FROM mysql.user WHERE User=%s',(username,));assert cursor.fetchone()[0]==0


@pytest.mark.parametrize('statement',[
    'DROP TABLE bravo_wp.private_data;',
    'CREATE TABLE alphaXwp.escape (id INT);',
    "SELECT 'file write' INTO OUTFILE '{sentinel}';",
    '\\! touch {sentinel}\n',
    'source {sentinel}\n',
    "LOAD DATA LOCAL INFILE '/etc/passwd' INTO TABLE alpha_wp.posts;",
])
def test_import_rejects_cross_database_and_client_file_commands(sql,statement):
    connection,work=sql
    sentinel=work/'must-not-be-created'
    path=work/'attack.sql';path.write_text(statement.format(sentinel=sentinel))
    with pytest.raises(RuntimeError):database.restore_database('alpha_wp',path,work)
    assert not sentinel.exists()
    with connection.cursor() as cursor:
        cursor.execute('SELECT value FROM bravo_wp.private_data');assert cursor.fetchone()[0]=='must remain private'
        cursor.execute("SELECT COUNT(*) FROM mysql.user WHERE User LIKE 'boronrestoreworker%'");assert cursor.fetchone()[0]==0
    assert not list(work.glob('client-*.cnf'))


@pytest.mark.parametrize('statement',[
    'CREATE VIEW alpha_wp.visible_posts AS SELECT id FROM alpha_wp.posts',
    'CREATE TRIGGER alpha_wp.keep_post BEFORE DELETE ON alpha_wp.posts FOR EACH ROW SET @snapshot_test = 1',
    'CREATE PROCEDURE alpha_wp.example() SELECT 1',
    'CREATE EVENT alpha_wp.example ON SCHEDULE EVERY 1 DAY DO SELECT 1',
    'CREATE SEQUENCE alpha_wp.sequence_example',
    'CREATE TABLE alpha_wp.history (id INT) WITH SYSTEM VERSIONING',
])
def test_unsupported_objects_are_reported_instead_of_silently_omitted(sql,statement):
    connection,work=sql
    with connection.cursor() as cursor:cursor.execute(statement)
    with pytest.raises(Exception,match='outside the configured hosting privilege set'):
        database.dump_database('alpha_wp',work/'incomplete.sql',work)
    assert not (work/'incomplete.sql').exists()


def test_system_database_cannot_get_a_restore_login(sql):
    for name in ['mysql','sys','information_schema','performance_schema']:
        with pytest.raises(Exception,match='System databases'):
            with database.import_login(name):pytest.fail('System database login created')


def test_real_backup_job_database_round_trip(sql,isolated_db,monkeypatch):
    from daemon import snapshot_jobs as jobs,snapshot_storage as storage
    from shared.db import write_session
    from shared.models import Account,DatabaseGrant,SnapshotDestination,SnapshotRun
    connection,work=sql
    home=work/'homes'/'alpha';home.mkdir(parents=True)
    monkeypatch.setattr(settings,'home_base',str(home.parent))
    monkeypatch.setattr(jobs._executor,'submit',lambda *args:None)
    with write_session() as session:
        account=Account(username='alpha',status='active',uid=65534,gid=65534)
        session.add(account);session.flush()
        session.add(DatabaseGrant(account_id=account.id,db_name='alpha_wp',db_user='alpha_wp'))
    dest=jobs.create_destination({'name':'Database proof','path':str(work/'repository')})
    jobs.initialize_destination({'id':dest['id']})
    policy=jobs.save_policy({'name':'Databases','destination_id':dest['id'],'components':['databases'],'accounts':['alpha']})
    ident=jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    jobs.execute_run(ident)
    result=jobs._row(SnapshotRun,ident)
    assert result.status=='completed',result.error
    repo=jobs.repository(jobs._row(SnapshotDestination,dest['id']))
    restored=storage.restore_to(repo,account.id,result.snapshot_id,str(work/'restored'))
    dump=restored/str(Path(settings.snapshot_private_dir)/'sources'/f'account-{account.id}'/'databases'/'alpha_wp.sql').lstrip('/')
    assert dump.is_file()
    assert not list(restored.rglob('client-*.cnf'))
    with connection.cursor() as cursor:cursor.execute("UPDATE alpha_wp.posts SET content='changed after snapshot'")
    database.restore_database('alpha_wp',dump,work)
    with connection.cursor() as cursor:
        cursor.execute('SELECT content FROM alpha_wp.posts WHERE id=1')
        assert cursor.fetchone()[0]=='Original WordPress content ☕'


    from daemon import snapshot_restores as restores
    catalog=restores.database_options({'username':'alpha','run_id':ident})
    assert len(catalog['databases'])==1
    assert catalog['databases'][0]['name']=='alpha_wp'
    assert catalog['databases'][0]['available'] is True
    assert catalog['databases'][0]['size']>0
    with connection.cursor() as cursor:
        cursor.execute("UPDATE alpha_wp.posts SET content='version before queued restore'")
        cursor.execute('CREATE TABLE alpha_wp.added_later (id INT)')
        cursor.execute('INSERT INTO alpha_wp.added_later VALUES (42)')
    with pytest.raises(Exception,match='not found for this account'):
        restores.trigger({'username':'alpha','run_id':ident,'confirmation':'alpha','kind':'databases','databases':['bravo_wp']})
    request=restores.trigger({'username':'alpha','run_id':ident,'confirmation':'alpha','kind':'databases','databases':['alpha_wp']})
    restores.execute(request['id'])
    result=restores.list_restores({'username':'alpha'})['restores'][0]
    assert result['status']=='completed',result['error']
    assert result['safety_snapshot_id']
    assert result['summary']['databases']==['alpha_wp']
    with connection.cursor() as cursor:
        cursor.execute('SELECT content FROM alpha_wp.posts WHERE id=1')
        assert cursor.fetchone()[0]=='Original WordPress content ☕'
        cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='alpha_wp' AND TABLE_NAME='added_later'")
        assert cursor.fetchone()[0]==0
        cursor.execute('SELECT value FROM bravo_wp.private_data')
        assert cursor.fetchone()[0]=='must remain private'
    recovery=restores.undo({'username':'alpha','restore_id':request['id'],'confirmation':'alpha'})
    restores.execute(recovery['id'])
    result=restores.list_restores({'username':'alpha'})['restores'][0]
    assert result['status']=='completed',result['error']
    with connection.cursor() as cursor:
        cursor.execute('SELECT content FROM alpha_wp.posts WHERE id=1')
        assert cursor.fetchone()[0]=='version before queued restore'
        cursor.execute('SELECT id FROM alpha_wp.added_later')
        assert cursor.fetchone()[0]==42
    assert not list(Path(settings.snapshot_private_dir).rglob('client-*.cnf'))
    assert not (Path(settings.snapshot_private_dir)/'database-safety'/f'account-{account.id}'/'databases').exists()
    from sqlalchemy import delete
    with write_session() as session:
        session.execute(delete(DatabaseGrant).where(DatabaseGrant.account_id==account.id))
    catalog=restores.database_options({'username':'alpha','run_id':ident})
    assert catalog['databases'][0]['available'] is False
    assert 'no longer registered' in catalog['databases'][0]['reason']
    with pytest.raises(Exception,match='not found for this account'):
        restores.trigger({'username':'alpha','run_id':ident,'confirmation':'alpha','kind':'databases','databases':['alpha_wp']})


def test_startup_cleanup_only_removes_reserved_worker_names(sql):
    connection,_=sql
    with connection.cursor() as cursor:
        cursor.execute("CREATE USER 'boronrestoreworker_abcdef012345'@'localhost' IDENTIFIED BY 'test'")
        cursor.execute("CREATE USER 'alpha_boronrestoreworker'@'localhost' IDENTIFIED BY 'test'")
    assert database.cleanup_abandoned_logins()==1
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM mysql.user WHERE User='alpha_boronrestoreworker'")
        assert cursor.fetchone()[0]==1
        cursor.execute("DROP USER 'alpha_boronrestoreworker'@'localhost'")


def test_client_cannot_source_an_existing_sql_file(sql):
    connection,work=sql
    nested=work/'nested.sql';nested.write_text('DROP TABLE alpha_wp.posts;\n')
    request=work/'request.sql';request.write_text(f'source {nested}\n')
    with pytest.raises(RuntimeError):database.restore_database('alpha_wp',request,work)
    with connection.cursor() as cursor:
        cursor.execute('SELECT COUNT(*) FROM alpha_wp.posts');assert cursor.fetchone()[0]==1


def test_local_infile_is_disabled_even_when_server_allows_it(sql):
    connection,work=sql
    secret=work/'private.txt';secret.write_text('must not be imported\n')
    with connection.cursor() as cursor:
        cursor.execute('SET GLOBAL local_infile=ON')
        cursor.execute('CREATE TABLE alpha_wp.imported (content LONGTEXT)')
    request=work/'local-file.sql';request.write_text(f"LOAD DATA LOCAL INFILE '{secret}' INTO TABLE alpha_wp.imported;\n")
    with pytest.raises(RuntimeError):database.restore_database('alpha_wp',request,work)
    with connection.cursor() as cursor:
        cursor.execute('SELECT COUNT(*) FROM alpha_wp.imported');assert cursor.fetchone()[0]==0


def test_export_cannot_write_outside_private_work_directory(sql):
    _,work=sql
    with pytest.raises(Exception,match='private work directory'):
        database.dump_database('alpha_wp',work.parent/'outside.sql',work)
    assert not (work.parent/'outside.sql').exists()


def test_replace_tables_handles_foreign_keys_and_quoted_names(sql):
    connection,work=sql
    with connection.cursor() as cursor:
        cursor.execute('CREATE TABLE alpha_wp.child (id INT PRIMARY KEY, post_id INT, FOREIGN KEY(post_id) REFERENCES alpha_wp.posts(id))')
        cursor.execute('INSERT INTO alpha_wp.child VALUES (1,1)')
        cursor.execute('CREATE TABLE alpha_wp.`strange``table` (id INT)')
        cursor.execute('INSERT INTO alpha_wp.`strange``table` VALUES (17)')
    dump=database.dump_database('alpha_wp',work/'tables.sql',work)
    with connection.cursor() as cursor:
        cursor.execute('CREATE TABLE alpha_wp.later (id INT)')
        cursor.execute('DELETE FROM alpha_wp.child')
    database.restore_database('alpha_wp',dump,work,replace_tables=True)
    with connection.cursor() as cursor:
        cursor.execute('SELECT post_id FROM alpha_wp.child');assert cursor.fetchone()[0]==1
        cursor.execute('SELECT id FROM alpha_wp.`strange``table`');assert cursor.fetchone()[0]==17
        cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='alpha_wp' AND TABLE_NAME='later'");assert cursor.fetchone()[0]==0
        cursor.execute('SELECT @@FOREIGN_KEY_CHECKS');assert cursor.fetchone()[0]==1
        cursor.execute('SELECT value FROM bravo_wp.private_data');assert cursor.fetchone()[0]=='must remain private'


def test_replace_rejects_external_foreign_keys_before_changes(sql):
    connection,work=sql
    dump=database.dump_database('alpha_wp',work/'tables.sql',work)
    with connection.cursor() as cursor:
        cursor.execute('CREATE TABLE bravo_wp.linked (id INT, FOREIGN KEY(id) REFERENCES alpha_wp.posts(id))')
        cursor.execute('INSERT INTO bravo_wp.linked VALUES (1)')
    try:
        with pytest.raises(Exception,match='linked to another database'):
            database.restore_database('alpha_wp',dump,work,replace_tables=True)
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM alpha_wp.posts');assert cursor.fetchone()[0]==1
            cursor.execute('SELECT COUNT(*) FROM bravo_wp.linked');assert cursor.fetchone()[0]==1
    finally:
        with connection.cursor() as cursor:cursor.execute('DROP TABLE bravo_wp.linked')


def test_replace_rejects_empty_dump_before_changes(sql):
    connection,work=sql
    dump=work/'empty.sql';dump.touch()
    with pytest.raises(Exception,match='empty'):
        database.restore_database('alpha_wp',dump,work,replace_tables=True)
    with connection.cursor() as cursor:
        cursor.execute('SELECT COUNT(*) FROM alpha_wp.posts');assert cursor.fetchone()[0]==1
