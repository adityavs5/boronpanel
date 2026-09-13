"""Streaming SQL backup/import with a short-lived database-specific login.

The temporary import user gets no global privileges, GRANT OPTION or FILE
privilege. SQL dump bytes never pass through a panel Python string.
"""
from contextlib import contextmanager
import os
import pymysql
from pathlib import Path
import re
import secrets
import stat
import tempfile

from daemon import mariadb
from daemon.procutil import run
from shared.config import settings
from shared.validation import ValidationError, validate_db_identifier


def _database_name(name):
    validate_db_identifier(name)
    if name.lower() in {'mysql','sys','information_schema','performance_schema'}:
        raise ValidationError('System databases cannot be restored through hosting backups')


def _option(value):
    value=str(value)
    if any(c in value for c in '\0\r\n'):raise ValidationError('Invalid database connection setting')
    return '"'+value.replace('\\','\\\\').replace('"','\\"')+'"'


@contextmanager
def client_config(directory,username,password):
    directory=Path(directory)
    if directory.is_symlink() or directory.resolve()!=directory or not directory.is_dir() or directory.stat().st_uid!=os.geteuid() or directory.stat().st_mode & 0o077:
        raise ValidationError('Database work directory must be private and owned by the backup service')
    fd,name=tempfile.mkstemp(prefix='client-',suffix='.cnf',dir=directory)
    try:
        with os.fdopen(fd,'w') as handle:
            handle.write(f'[client]\nuser={_option(username)}\npassword={_option(password)}\nsocket={_option(settings.mariadb_socket)}\n')
        yield name
    finally:
        Path(name).unlink(missing_ok=True)


def _sql_file(path):
    path=Path(path)
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.resolve()!=path or info.st_uid!=os.geteuid():
        raise ValidationError('SQL restore source must be a verified regular file owned by the backup service')
    return path


def _grant_identifier(name):
    _database_name(name)
    # GRANT database names are LIKE patterns even inside backticks.
    return '`'+name.replace('_','\\_')+'`'


@contextmanager
def import_login(db_name):
    _database_name(db_name)
    # Prefix before the first underscore is longer than a hosting username,
    # so these names cannot collide with normal account-prefixed SQL users.
    username='boronrestoreworker_'+secrets.token_hex(6)
    password=secrets.token_urlsafe(40)
    connection=mariadb._connect()
    created=False
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE USER '{username}'@'localhost' IDENTIFIED BY %s",(password,))
            created=True
            cursor.execute(f"GRANT {mariadb.HOSTED_DB_PRIVILEGES} ON {_grant_identifier(db_name)}.* TO '{username}'@'localhost'")
        yield username,password
    finally:
        try:
            if created:
                with connection.cursor() as cursor:cursor.execute(f"DROP USER IF EXISTS '{username}'@'localhost'")
        finally:connection.close()


def cleanup_abandoned_logins():
    """Call at daemon startup, before starting any database restore workers."""
    connection=mariadb._connect()
    removed=0
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT User FROM mysql.user WHERE Host='localhost' AND User LIKE %s",('boronrestoreworker\\_%',))
            names=[row[0] for row in cursor.fetchall()]
            for username in names:
                if re.fullmatch(r'boronrestoreworker_[0-9a-f]{12}',username):
                    cursor.execute(f"DROP USER '{username}'@'localhost'")
                    removed+=1
        return removed
    finally:connection.close()


def validate_supported_objects(db_name):
    """Never silently omit objects unsupported by the panel's hosting grants."""
    _database_name(db_name)
    connection=mariadb._connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE=%s',(db_name,'VIEW'))
            views=cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE NOT IN ('BASE TABLE','VIEW')",(db_name,))
            special_tables=cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM mysql.proc WHERE db=%s',(db_name,))
            routines=cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM mysql.event WHERE db=%s',(db_name,))
            events=cursor.fetchone()[0]
            cursor.execute('SELECT @@datadir')
            datadir=Path(cursor.fetchone()[0])
        # MariaDB 10.11 stores trigger definitions in .TRG files. SHOW TRIGGERS
        # hides them from users without TRIGGER privilege, so that query alone
        # cannot prove a table-only backup is complete. The root daemon checks
        # for their presence without reading their contents.
        folder=datadir/db_name
        triggers=any(path.suffix=='.TRG' for path in folder.iterdir())
        unsupported=[]
        if views:unsupported.append('views')
        if special_tables:unsupported.append('special table types')
        if routines:unsupported.append('stored routines')
        if events:unsupported.append('events')
        if triggers:unsupported.append('triggers')
        if unsupported:
            raise ValidationError('Database contains '+', '.join(unsupported)+' outside the configured hosting privilege set; the backup was stopped to avoid omitting them')
    finally:connection.close()


def dump_database(db_name,output_path,work_directory):
    _database_name(db_name)
    output_path=Path(output_path)
    work=Path(work_directory)
    if not output_path.is_absolute() or output_path.parent.resolve()!=output_path.parent or not output_path.parent.is_relative_to(work) or not output_path.parent.is_dir():
        raise ValidationError('Database export must stay inside its private work directory')
    validate_supported_objects(db_name)
    if output_path.exists() or output_path.is_symlink():raise ValidationError('Database export target must not already exist')
    with client_config(work_directory,settings.mariadb_admin_user,settings.mariadb_admin_password) as config:
        result=run(['/usr/bin/nice','-n','10','/usr/bin/ionice','-c','2','-n','7','/usr/bin/mariadb-dump',
            f'--defaults-file={config}','--single-transaction','--quick','--skip-dump-date','--hex-blob',
            '--skip-routines','--skip-events','--skip-triggers',f'--result-file={output_path}','--',db_name],timeout=1800,discard_stdout=True)
        if not result.ok:
            output_path.unlink(missing_ok=True)
            raise RuntimeError(f'Database export failed for {db_name}: {result.stderr.strip()}')
    output_path.chmod(0o600)
    return output_path


def _validate_database_boundary(db_name):
    # A table reset must not leave foreign keys in another schema dangling.
    connection=mariadb._connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE
                WHERE (TABLE_SCHEMA=%s AND REFERENCED_TABLE_SCHEMA<>%s)
                   OR (REFERENCED_TABLE_SCHEMA=%s AND TABLE_SCHEMA<>%s)""",(db_name,db_name,db_name,db_name))
            if cursor.fetchone()[0]:
                raise ValidationError('Database has foreign keys linked to another database; restore both through a coordinated migration')
    finally:connection.close()


def _clear_tables(db_name,username,password):
    validate_supported_objects(db_name)
    _validate_database_boundary(db_name)
    connection=pymysql.connect(unix_socket=settings.mariadb_socket,user=username,password=password,
        database=db_name,autocommit=True,connect_timeout=15,read_timeout=120,write_timeout=120)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SET SESSION lock_wait_timeout=30')
            cursor.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE=%s',(db_name,'BASE TABLE'))
            tables=[row[0] for row in cursor.fetchall()]
            cursor.execute('SET SESSION FOREIGN_KEY_CHECKS=0')
            for table in tables:
                # Table names are database metadata, not restricted panel identifiers.
                quoted='`'+table.replace('`','``')+'`'
                cursor.execute('DROP TABLE '+quoted)
            return len(tables)
    finally:connection.close()


def restore_database(db_name,dump_path,work_directory,*,replace_tables=False):
    """The caller must check account ownership and save current DB contents first."""
    _database_name(db_name)
    dump_path=_sql_file(dump_path)
    if dump_path.stat().st_size==0:raise ValidationError('Database export is empty')
    with import_login(db_name) as (username,password):
        if replace_tables:_clear_tables(db_name,username,password)
        with client_config(work_directory,username,password) as config:
            result=run(['/usr/bin/nice','-n','10','/usr/bin/ionice','-c','2','-n','7','/usr/bin/mariadb',
                f'--defaults-file={config}','--batch','--binary-mode','--sandbox','--local-infile=0',
                '--skip-reconnect','--default-character-set=utf8mb4','--',db_name],
                input_path=str(dump_path),discard_stdout=True,timeout=1800)
            if not result.ok:raise RuntimeError(f'Database restore failed for {db_name}: {result.stderr.strip()}')
    return {'database':db_name,'bytes_read':dump_path.stat().st_size}
