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
    restores.recover_restores()
    with write_session() as session:
        assert session.get(SnapshotRestore, ident).status == ('running' if prepared else 'failed')
    if prepared:
        assert (work / 'retained').is_file()
