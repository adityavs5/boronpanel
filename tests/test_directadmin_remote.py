"""Offline regression cases for the next test pass; no source server required."""
from contextlib import contextmanager
from urllib.parse import urlencode

import pytest

from daemon import directadmin_remote as remote
from daemon.import_compat import prepare_sql
from shared.validation import ValidationError


TARGET = {'version': '10.11-MariaDB', 'collations': {'utf8mb4_unicode_ci', 'utf8mb4_bin'}, 'engines': {'innodb'}}


def test_strict_blocks_unsupported_collation():
    with pytest.raises(ValidationError, match='Unsupported collate'):
        prepare_sql('CREATE TABLE t (v TEXT) COLLATE=utf8mb4_0900_ai_ci;', TARGET)


def test_opt_in_changes_schema_not_data():
    insert = "INSERT INTO t VALUES ('COLLATE=utf8mb4_0900_ai_ci; DEFINER=old@localhost');"
    sql, notes = prepare_sql('CREATE TABLE t (v TEXT) COLLATE=utf8mb4_0900_ai_ci;\n' + insert, TARGET, True)
    assert 'COLLATE=utf8mb4_unicode_ci' in sql
    assert insert in sql
    assert len(notes) == 1


def test_executable_comment_definer_changes_to_destination():
    sql, notes = prepare_sql('/*!50017 DEFINER=`old`@`localhost`*/', TARGET, True)
    assert sql == '/*!50017 DEFINER=CURRENT_USER*/'
    assert notes


@pytest.mark.parametrize('sql', ['SET @@GLOBAL.GTID_PURGED=\'uuid:1\';', 'SET @@SESSION.SQL_LOG_BIN=0;', 'CREATE TABLE t (i INT) ENGINE=unknown;'])
def test_unsafe_or_unsupported_features_block_even_in_adapt_mode(sql):
    with pytest.raises(ValidationError):
        prepare_sql(sql, TARGET, True)


def test_ordinary_comments_and_multiline_strings_unchanged():
    sql = "/* COLLATE=utf8mb4_0900_ai_ci */\nINSERT INTO t VALUES ('hello\nCOLLATE=utf8mb4_0900_ai_ci');"
    assert prepare_sql(sql, TARGET, True) == (sql, [])


def test_alternate_quote_modes_never_automatically_rewritten():
    with pytest.raises(ValidationError, match='quote|NO_BACKSLASH_ESCAPES'):
        prepare_sql("SET SQL_MODE='NO_BACKSLASH_ESCAPES';", TARGET, True)


def source_with_response(text):
    source = object.__new__(remote.Source)
    @contextmanager
    def stream(*args, **kwargs):
        yield iter([text.encode()])
    source.stream = stream
    return source


def test_account_list_urlencoded():
    source = source_with_response('list%5B%5D=alice&list%5B%5D=bob')
    assert source.accounts() == ['alice', 'bob']


def test_login_html_is_rejected():
    source = source_with_response('<html>login</html>')
    with pytest.raises(ValidationError, match='Unrecognized'):
        source.accounts()


def test_remote_error_details_are_not_reflected():
    source = source_with_response('error=1&text=secret-password')
    with pytest.raises(ValidationError) as error:
        source.accounts()
    assert 'secret-password' not in str(error.value)


def test_backup_listing_ignores_symlinks_and_unexpected_paths(monkeypatch):
    source = object.__new__(remote.Source)
    responses = iter([
        {'list[]': ['alice.example.com']},
        {'list[]': ['backup.tar.gz']},
        {'/backups/backup.tar.gz': urlencode({'type': 'file', 'size': 123, 'date': '1', 'linkpath': ''}),
         '/backups/linked.tar.gz': urlencode({'type': 'file', 'size': 123, 'linkpath': '/etc/shadow'}),
         '/elsewhere/backup.tar.gz': {'type': 'file', 'size': 123}},
    ])
    source.request = lambda *args, **kwargs: next(responses)
    assert source.backups('alice') == {'/backups/backup.tar.gz': (123, '1')}


def test_host_key_mismatch_fails_closed():
    class Key:
        def asbytes(self):
            return b'test key'
    with pytest.raises(ValidationError, match='does not match'):
        remote.PinnedKey('SHA256:wrong').missing_host_key(None, 'server', Key())


def test_rpc_is_global_admin_only():
    from daemon.rpc_policy import POLICY_BY_OPERATION
    assert POLICY_BY_OPERATION['directadmin_remote.inspect'] == 'global_admin'


def test_source_admin_is_not_subject_to_local_reserved_name_rules(monkeypatch):
    monkeypatch.setattr(remote.webhooks, '_assert_public_destination', lambda url: '192.0.2.10')
    result = remote.validate({'host': 'source.example.com', 'password': 'test', 'login': 'admin'})
    assert result['login'] == 'admin'
    assert source_with_response('list%5B%5D=admin&list%5B%5D=alice').accounts() == ['admin', 'alice']


def test_remote_transfer_waits_for_new_stable_archive_and_clears_password(tmp_path, monkeypatch):
    from daemon import cpanel_import
    monkeypatch.setattr(cpanel_import, '_update_job', lambda *args, **kwargs: None)
    monkeypatch.setattr(remote.time, 'sleep', lambda seconds: None)
    class FakeSource:
        count = 0
        def __init__(self, params): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def accounts(self): return ['alice']
        def runtime_inventory(self, user): return {}
        def backup_domain(self, user): return 'alice.example.com'
        def backups(self, user):
            self.count += 1
            return {} if self.count == 1 else {'/backups/new.tar.gz': (4, '123')}
        def request(self, endpoint, payload, user, method):
            assert endpoint == 'CMD_API_SITE_BACKUP'
            assert payload['action'] == 'backup'
            assert payload['domain'] == 'alice.example.com'
            assert 'database' in payload.values()
        @contextmanager
        def stream(self, endpoint, payload, user):
            assert endpoint == 'CMD_FILE_MANAGER'
            assert payload['path'] == '/backups/new.tar.gz'
            yield iter([b'data'])
    monkeypatch.setattr(remote, 'Source', FakeSource)
    params = {'remote_user': 'alice', 'remote': {'password': 'secret'}}
    result = remote.fetch_archive(1, params, tmp_path)
    assert result.read_bytes() == b'data'
    assert 'password' not in params['remote']


def test_shared_wordpress_database_reuses_imported_credentials(tmp_path, monkeypatch):
    from daemon import cpanel_import as ci
    (tmp_path / 'wp-config.php').write_text("<?php define('DB_NAME', 'old_shop');")
    monkeypatch.setattr(ci, '_domain_docroot', lambda *args: str(tmp_path))
    calls = []
    monkeypatch.setattr(ci, '_rewrite_wp_config', lambda *args: calls.append(args) or 'rewritten')
    monkeypatch.setattr(ci.mariadb, 'set_password', lambda *args: pytest.fail('must not rotate shared database credentials'))
    for domain in ['one.example.com', 'two.example.com']:
        ci._wordpress_rewrite_step.__wrapped__('alice', domain, {'old_shop': 'alice_shop'}, {'alice_shop': ('alice_shop', 'test-secret')})
    assert len(calls) == 2
    assert all(args[1:] == ('alice_shop', 'alice_shop', 'test-secret') for args in calls)


def test_backup_domain_is_real_owned_domain_and_cached():
    source = object.__new__(remote.Source)
    calls = []
    def request(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return {'list[]': ['alice.example.com']}
    source.request = request
    assert source.backup_domain('alice') == 'alice.example.com'
    assert source.backup_domain('alice') == 'alice.example.com'
    assert calls == [('CMD_API_SHOW_DOMAINS', {'user': 'alice'})]


def test_domain_less_account_fails_without_fake_domain():
    source = source_with_response('')
    with pytest.raises(ValidationError, match='no domain available'):
        source.backup_domain('alice')


@pytest.mark.parametrize('method,stage', [('GET', 'listing source backups'), ('POST', 'creating the source backup')])
def test_remote_failure_identifies_stage_without_exposing_details(method, stage):
    source = source_with_response('error=1&text=Domain+does+not+exist&details=secret-password')
    with pytest.raises(ValidationError) as error:
        source.request('CMD_API_SITE_BACKUP', method=method)
    assert stage in str(error.value)
    assert 'rejected the account domain' in str(error.value)
    assert 'secret-password' not in str(error.value)


def test_disabled_user_backups_have_typed_error():
    source = source_with_response('error=1&text=User+backups+have+been+disabled.&details=Allowance+threshold+is+0%25')
    with pytest.raises(remote.UserBackupsDisabled):
        source.request('CMD_API_SITE_BACKUP', method='POST')


def test_admin_backup_is_one_account_and_preserves_config():
    source = object.__new__(remote.Source)
    source.p = {'login': 'admin'}
    calls = []
    def request(endpoint, data=None, **kwargs):
        calls.append((endpoint, data, kwargs))
        return {'location': '/home/admin'} if data is None else {'error': '0'}
    source.request = request
    directory = source.start_admin_backup('alice')
    assert directory.startswith('/admin_backups/boron-')
    _, data, kwargs = calls[-1]
    assert data['who'] == 'selected' and data['select0'] == 'alice'
    assert data['write_backup_conf'] == 'no'
    assert data['local_path'] == '/home/admin' + directory
    assert kwargs == {'method': 'POST'}


def test_admin_listing_only_accepts_selected_account_regular_archives():
    source = object.__new__(remote.Source)
    directory = '/admin_backups/boron-test'
    source.request = lambda *a: {
        directory + '/user.admin.alice.tar.zst': {'type': 'file', 'size': '123', 'mtime': '2'},
        directory + '/user.admin.bob.tar.gz': {'type': 'file', 'size': '123'},
        directory + '/user.admin.alice.tar.gz': {'type': 'file', 'size': '123', 'linkpath': '/etc/shadow'},
    }
    assert source.admin_backups(directory, 'alice') == {directory + '/user.admin.alice.tar.zst': (123, '2')}


def test_disabled_user_backups_fall_back_to_admin_transfer(tmp_path, monkeypatch):
    from daemon import cpanel_import
    monkeypatch.setattr(cpanel_import, '_update_job', lambda *a, **k: None)
    monkeypatch.setattr(remote.time, 'sleep', lambda *a: None)
    class FakeSource:
        def __init__(self, params): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def accounts(self): return ['alice']
        def runtime_inventory(self, user): return {}
        def backups(self, user): return {}
        def backup_domain(self, user): return 'example.com'
        def request(self, *args): raise remote.UserBackupsDisabled('disabled')
        def start_admin_backup(self, user):
            assert user == 'alice'
            return '/admin_backups/boron-test'
        def admin_backups(self, directory, user):
            return {directory + '/user.admin.alice.tar.zst': (4, '123')}
        @contextmanager
        def stream(self, endpoint, payload, user):
            assert user is None  # Admin archive must not use customer impersonation.
            yield iter([b'data'])
    monkeypatch.setattr(remote, 'Source', FakeSource)
    params = {'remote_user': 'alice', 'remote': {'password': 'test'}}
    assert remote.fetch_archive(1, params, tmp_path).read_bytes() == b'data'
    assert 'password' not in params['remote']


def test_runtime_inventory_uses_selected_option_text_and_readonly_selector():
    source = object.__new__(remote.Source)
    source.ssh = None
    calls = []
    def request(endpoint, data=None, user=None, method='GET'):
        calls.append((endpoint, method))
        if endpoint == 'CMD_API_SHOW_DOMAINS': return {'list[]': ['example.com']}
        if endpoint == 'CMD_ADDITIONAL_DOMAINS':
            return {'php1_ver': '8.3', 'php1_select': {'0': {'value': '1', 'text': 'PHP 8.3'}, '5': {'value': '6', 'text': 'PHP 8.0', 'selected': 'yes'}}}
        if endpoint == 'CMD_API_FILE_MANAGER':
            return {'/.cl.selector': {}} if data['path'] == '/' else {'/.cl.selector/node-selector.json': {}}
        if endpoint == 'CMD_FILE_MANAGER':
            return {'nodeapps/backend': {'domain': 'example.com', 'nodejs_version': '20', 'startup_file': 'server.js', 'app_status': 'started', 'app_mode': 'production', 'env_vars': {}}}
        raise AssertionError(endpoint)
    source.request = request
    result = source.runtime_inventory('alice')
    assert result['example.com']['selected'] == '8.0'
    assert result['example.com']['slots']['6'] == '8.0'
    assert result['__applications'][0]['version'] == '20'
    assert all(method == 'GET' for _, method in calls)
