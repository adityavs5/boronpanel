"""Rollback ownership and retry regression tests; service teardown is stubbed."""
import pytest
from sqlalchemy import select
from daemon import cpanel_import as ci
from shared.db import write_session
from shared.models import Account, CpanelImportJob, PanelUser, Session, Domain, utcnow


def setup_rows():
    with write_session() as db:
        account = Account(username='importqa', uid=6123, gid=6123)
        other = Account(username='retained', uid=6124, gid=6124)
        job = CpanelImportJob(username='importqa', source='upload', status='failed', results=[])
        db.add_all([account, other, job])
        db.flush()
        user = PanelUser(username='importqa', password_hash='unused', role='customer', account_id=account.id)
        db.add(user)
        db.flush()
        db.add(Session(panel_user_id=user.id, session_id='test-token', expires_at=utcnow()))
        db.add(Domain(account_id=account.id, domain='import.example.com', docroot='/unused'))
        return job.id, (account.id, account.created_at.replace(tzinfo=None))


def test_cleanup_removes_owned_rows_but_preserves_other_account_and_job(isolated_db, monkeypatch):
    job_id, ownership = setup_rows()
    monkeypatch.setattr(ci.handlers_account, '_terminate_account', lambda p: {'status': 'terminated'})
    ci._cleanup_failed_import(job_id, 'importqa', ownership)
    with write_session() as db:
        assert db.get(Account, ownership[0]) is None
        assert db.scalar(select(Account).where(Account.username == 'retained')) is not None
        assert db.scalar(select(PanelUser)) is None
        assert db.scalar(select(Session)) is None
        assert db.scalar(select(Domain)) is None
        assert db.get(CpanelImportJob, job_id).results[-1]['status'] == 'ok'


@pytest.mark.parametrize('ownership_kind', ['none', 'wrong_identity', 'teardown_failure'])
def test_cleanup_never_deletes_unowned_or_incompletely_terminated_account(isolated_db, monkeypatch, ownership_kind):
    job_id, ownership = setup_rows()
    if ownership_kind == 'none':
        ownership = None
    elif ownership_kind == 'wrong_identity':
        ownership = (ownership[0], None)
    monkeypatch.setattr(ci.handlers_account, '_terminate_account', lambda p: {'status': 'error'})
    ci._cleanup_failed_import(job_id, 'importqa', ownership)
    with write_session() as db:
        assert db.scalar(select(Account).where(Account.username == 'importqa')) is not None
        assert db.scalar(select(PanelUser)) is not None


@pytest.mark.parametrize('failure', ['panel_login', 'files', 'provisioning'])
def test_worker_rolls_back_new_account_on_failure(isolated_db, tmp_path, monkeypatch, failure):
    monkeypatch.setattr(ci.settings, 'cpanel_import_staging_dir', str(tmp_path / 'staging'))
    monkeypatch.setattr(ci, '_obtain_archive', lambda *args: tmp_path / 'source.tar.gz')
    monkeypatch.setattr(ci, '_extract_archive', lambda *args, **kwargs: None)
    monkeypatch.setattr(ci, '_find_content_root', lambda path: path)
    monkeypatch.setattr(ci, '_parse_account_info', lambda path: {})
    monkeypatch.setattr(ci, '_parse_domains', lambda *args: [])
    monkeypatch.setattr(ci, '_parse_mysql_dumps', lambda *args: [])
    monkeypatch.setattr(ci, '_parse_ssl_certs', lambda *args: [])
    monkeypatch.setattr(ci, '_parse_mailboxes', lambda *args: [])
    monkeypatch.setattr(ci, '_parse_ftp_accounts', lambda *args: [])
    monkeypatch.setattr(ci, '_parse_cron', lambda *args: [])
    def fail(*args):
        raise RuntimeError('test failure')
    def create(params):
        with write_session() as db:
            account = Account(username=params['username'])
            db.add(account)
            db.flush()
            result = {'id': account.id}
        if failure == 'provisioning':
            fail()
        return result
    monkeypatch.setattr(ci.handlers_account, 'create_account', create)
    monkeypatch.setattr(ci.handlers_account, '_terminate_account', lambda p: {'status': 'terminated'})
    monkeypatch.setattr(ci.handlers_auth, 'create_panel_user', fail if failure == 'panel_login' else lambda p: None)
    monkeypatch.setattr(ci.audit, 'record_account_event', lambda *a, **k: None)
    monkeypatch.setattr(ci, '_copy_homedir', fail)
    with write_session() as db:
        job = CpanelImportJob(username='importqa', source='url', source_ref='https://example.com/a', status='pending', results=[])
        db.add(job)
        db.flush()
        job_id = job.id
    ci._run_import_job(job_id, {'source': 'url', 'source_ref': 'https://example.com/a'})
    with write_session() as db:
        assert db.scalar(select(Account)) is None
        job = db.get(CpanelImportJob, job_id)
        assert job.status == 'failed'
        assert job.initial_password is None
        assert job.results[-1]['item'] == 'cleanup'
        assert job.results[-1]['status'] == 'ok'
