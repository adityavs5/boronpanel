from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select

from daemon import appinstaller, jobcredentials, wordpress, cmdjobs
from shared.db import write_session
from shared.models import Account, AppInstallJob, WordPressJob, CommandRun, Domain


@pytest.mark.parametrize('model,handler', [(WordPressJob, wordpress), (AppInstallJob, appinstaller)])
def test_completed_password_is_encrypted_and_consumed_once(isolated_db, model, handler, monkeypatch):
    password = 'Private install password 123!'
    with write_session() as session:
        account = Account(username='demo1', status='active', uid=5001, gid=5001)
        session.add(account); session.flush()
        session.add(Domain(account_id=account.id, domain='demo1.example', kind='primary', docroot='/home/demo1/public_html'))
        row = model(account_id=account.id, domain='demo1.example', status='pending',
                    **({'app_id': 'joomla'} if model is AppInstallJob else {}))
        session.add(row); session.flush(); job_id = row.id
    result = {'admin_password': password, 'admin_user': 'admin', 'admin_url': 'https://demo1.example/wp-admin/', 'version': 'test'}
    if handler is wordpress:
        monkeypatch.setattr(wordpress, 'install', lambda params: result)
        handler._run_install_job(job_id, {'username': 'demo1', 'domain': 'demo1.example'})
    else:
        monkeypatch.setitem(appinstaller.APPS['joomla'], 'installer', lambda *args: result)
        handler._run_install_job(job_id, 'demo1', 'demo1.example', 'joomla', {})
    with write_session() as session:
        stored = session.get(model, job_id).admin_password
        assert password not in stored and stored.startswith(jobcredentials.PREFIX)
    barrier = Barrier(2)
    def consume():
        barrier.wait()
        return handler.get_job({'username': 'demo1', 'job_id': job_id})['admin_password']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))
    assert results.count(password) == 1 and results.count(None) == 1
    with write_session() as session:
        assert session.get(model, job_id).admin_password is None


def test_legacy_migration_preserves_reveal_and_is_idempotent(isolated_db):
    with write_session() as session:
        account = Account(username='demo1', status='active', uid=5001, gid=5001)
        session.add(account);session.flush()
        for model in (WordPressJob, AppInstallJob):
            session.add(model(account_id=account.id, domain='demo1.example', status='completed',
                              admin_password='Legacy password 123!',
                              **({'app_id': 'joomla'} if model is AppInstallJob else {})))
    with write_session() as session:
        account_id = session.scalar(select(Account.id))
        session.add(CommandRun(account_id=account_id, kind='wpcli', target='/home/demo1',
                               command_display='test reset', status='completed', revealed_secret='Legacy reset 123!'))
    assert jobcredentials.migrate() == 3
    assert jobcredentials.migrate() == 0
    with write_session() as session:
        for model in (WordPressJob, AppInstallJob):
            stored = session.scalar(select(model)).admin_password
            assert stored.startswith(jobcredentials.PREFIX)
            assert jobcredentials.reveal(stored) == 'Legacy password 123!'


def test_corrupt_encrypted_value_never_falls_back_to_plaintext():
    from daemon.appcrypto import AppCryptoError
    with pytest.raises(AppCryptoError):
        jobcredentials.reveal(jobcredentials.PREFIX + 'invalid')


def test_reset_password_is_encrypted_before_worker_and_revealed_once(isolated_db, monkeypatch):
    password = 'Private reset password 123!'
    with write_session() as session:
        session.add(Account(username='demo1', status='active', uid=5001, gid=5001))
    monkeypatch.setattr(cmdjobs._executor, 'submit', lambda *a, **kw: None)
    result = cmdjobs.submit('demo1', 'wpcli', '/home/demo1', ['/usr/bin/true'],
                            'reset test', revealed_secret=password)
    with write_session() as session:
        row = session.get(CommandRun, result['id'])
        assert row.revealed_secret.startswith(jobcredentials.PREFIX)
        assert password not in row.revealed_secret
        row.status = 'completed'
    barrier = Barrier(2)
    def consume():
        barrier.wait()
        return cmdjobs.get_run({'username': 'demo1', 'job_id': result['id']})['revealed_secret']
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: consume(), range(2)))
    assert values.count(password) == 1 and values.count(None) == 1


@pytest.mark.parametrize('kind', ['cpanel', 'portable'])
def test_import_credentials_migrate_and_reveal_to_only_one_reader(isolated_db, kind):
    from daemon import cpanel_import, portable_archive
    from shared.models import CpanelImportJob, AccountArchiveImportJob
    model, handler = (CpanelImportJob, cpanel_import) if kind == 'cpanel' else (AccountArchiveImportJob, portable_archive)
    secret = 'Imported-account-canary-42!'
    with write_session() as session:
        row = model(username='demo1', status='completed', initial_password=secret,
                    **({'source': 'upload'} if kind == 'cpanel' else {}))
        session.add(row);session.flush();job_id=row.id
    assert jobcredentials.migrate() == 1
    with write_session() as session:
        stored=session.get(model,job_id).initial_password
        assert stored.startswith(jobcredentials.PREFIX) and secret not in stored
    barrier=Barrier(2)
    def consume(_):
        barrier.wait()
        return handler.get_job({'username':'demo1','job_id':job_id}).get('initial_password')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(consume,range(2)))
    assert results.count(secret)==1 and results.count(None)==1
