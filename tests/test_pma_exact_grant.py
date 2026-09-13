import pymysql
import pytest
from daemon import mariadb
from tests.test_snapshot_databases import sql_server,sql


def test_phpmyadmin_cannot_access_wildcard_matching_or_foreign_database(sql,sql_server):
    user='boronphpmyadminlogin_123456789012'
    password='Only-for-isolated-pma-test-123!'
    mariadb.create_db_user(user,password)
    try:
        mariadb.grant_exact_database('alpha_wp',user)
        with pymysql.connect(unix_socket=sql_server,user=user,password=password,autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT count(*) FROM alpha_wp.posts')
                assert cursor.fetchone()[0]==1
                for statement in ('CREATE TABLE alphaXwp.unauthorized (id INT)', 'SELECT * FROM bravo_wp.private_data'):
                    with pytest.raises(pymysql.Error):cursor.execute(statement)
    finally:mariadb.drop_db_user(user)
