import pytest

from daemon import snapshot_jobs as jobs, snapshot_restores as restores, snapshot_mail_restore as mail_restore
from shared.db import write_session
from shared.models import Account, SnapshotDestination, SnapshotPolicy, SnapshotRun, SnapshotRestore, SnapshotMailRecovery


@pytest.mark.parametrize('prepared', [False, True])
def test_mail_failure_preserves_recovery_and_never_leaks_exception(isolated_db, tmp_path, monkeypatch, prepared):
    with write_session() as session:
        account = Account(username='alpha', status='active', uid=65534, gid=65534)
        destination = SnapshotDestination(name='test', kind='local', path=str(tmp_path / 'repo'), namespace='fixture')
        session.add_all([account, destination]); session.flush()
        policy = SnapshotPolicy(name='mail', destination_id=destination.id)
        session.add(policy); session.flush()
        source = SnapshotRun(policy_id=policy.id, destination_id=destination.id, account_id=account.id,
                             options={'components': ['mail']}, status='completed', snapshot_id='a'*64)
        session.add(source); session.flush()
        job = SnapshotRestore(run_id=source.id, account_id=account.id, selection={'kind': 'mail', 'mailboxes': ['inbox@example.test']})
        session.add(job); session.flush()
        ident = job.id
    monkeypatch.setattr(jobs, 'repository', lambda row: object())
    monkeypatch.setattr(restores.storage, 'owned_snapshot', lambda *a: {})
    work = None
    def interrupted(account, repo, snapshot_id, addresses, restore_id, checkpoint):
        nonlocal work
        checkpoint('preparing', {})
        if prepared:
            work = jobs.private_directory('mail-preparation', 'fixture')
            (work / 'retained').write_text('private recovery data')
            checkpoint('prepared', {'work': str(work)})
        raise RuntimeError('private diagnostic must not reach customer')
    monkeypatch.setattr(mail_restore, 'run_restore', interrupted)
    restores.execute(ident)
    with write_session() as session:
        row = session.get(SnapshotRestore, ident)
        assert row.status == ('running' if prepared else 'failed')
        assert 'private diagnostic' not in row.error
        if prepared:
            assert session.get(SnapshotMailRecovery, ident).work == str(work)
            assert (work / 'retained').is_file()
            assert row.completed_at is None
    monkeypatch.setattr(jobs._executor, 'submit', lambda function, *args: function(*args))
    restores.recover_restores()
    with write_session() as session:
        assert session.get(SnapshotRestore, ident).status == 'failed'
    if prepared:
        assert (work / 'retained').is_file()


@pytest.mark.parametrize('provisioning', [None, 'planned', 'completed'])
def test_pre_switch_abort_releases_only_proven_preparation(isolated_db, tmp_path, monkeypatch, provisioning):
    import json
    from daemon import snapshot_mail_guard as guard
    from shared.models import MailDomain
    from shared.config import settings
    from shared.validation import ValidationError
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(tmp_path / 'guards'))
    work = jobs.private_directory('mail-preparation', 'abort-test')
    with write_session() as session:
        account = Account(username='alpha', status='active', uid=65534, gid=65534)
        session.add(account); session.flush()
        session.add(MailDomain(account_id=account.id, domain='example.test'))
    token = guard.block('example.test', 'inbox', 7)
    index = work / 'guard-index.json'
    index.write_text(json.dumps(dict(format=1, account_id=account.id, restore_id=7,
                                    entries=[dict(domain='example.test', local_part='inbox', token=token)])))
    index.chmod(0o600)
    retained = work / 'retained-mail'
    retained.write_text('retain prepared copy')
    if provisioning:
        record = work / 'provisioning.json'
        record.write_text(json.dumps(dict(format=1, account_id=account.id, restore_id=7, status=provisioning)))
        record.chmod(0o600)
    if provisioning == 'planned':
        with pytest.raises(ValidationError, match='recovery records'):
            mail_restore.abort_pre_switch(account, work, 7)
        assert len(list((tmp_path / 'guards').iterdir())) == 1
    else:
        assert mail_restore.abort_pre_switch(account, work, 7)['guards_released'] == 1
        assert mail_restore.abort_pre_switch(account, work, 7)['guards_released'] == 0
        assert not list((tmp_path / 'guards').iterdir())
    assert retained.read_text() == 'retain prepared copy'
