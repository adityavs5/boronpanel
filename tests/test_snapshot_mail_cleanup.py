import json
import os

import pytest

from daemon import snapshot_mail_cleanup as cleanup, snapshot_mail_journal as journal
from shared.config import settings
from shared.db import write_session
from shared.models import (Account, SnapshotDestination, SnapshotPolicy, SnapshotRun,
                           SnapshotRestore, SnapshotMailRecovery)
from shared.validation import ValidationError


@pytest.fixture
def completed(isolated_db, tmp_path, monkeypatch):
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    work = private / 'work'
    work.mkdir(mode=0o700)
    monkeypatch.setattr(settings, 'snapshot_private_dir', str(private))
    with write_session() as session:
        account = Account(username='alpha', uid=65534, gid=65534, status='active')
        dest = SnapshotDestination(name='test', namespace='test', kind='local', path='/unused')
        session.add_all([account, dest]); session.flush()
        policy = SnapshotPolicy(name='mail', destination_id=dest.id)
        session.add(policy); session.flush()
        run = SnapshotRun(account_id=account.id, policy_id=policy.id, destination_id=dest.id)
        session.add(run); session.flush()
        row = SnapshotRestore(account_id=account.id, run_id=run.id, status='completed',
                              selection={'kind': 'mail'}, safety_snapshot_id='a'*64,
                              summary={'guards_released': True, 'mailboxes': 1})
        session.add(row); session.flush()
        ident, account_id = row.id, account.id
        session.add(SnapshotMailRecovery(restore_id=ident, phase='completed',
                                        work=str(work), journal=str(work / 'switch.json')))
    payload = dict(format=1, restore_id=ident, operation_id='b'*32, undo=False, entries=[
        dict(domain='alpha.test', local_part='inbox', token='c'*64, plan=dict(
            format=1, prepared='.boron-mail-ready-'+'d'*32,
            home=[1, 2], current=[1, 3], replacement=[1, 4]))])
    journal.create(work / 'switch.json', payload)
    receipt = dict(format=1, account_id=account_id, restore_id=ident, snapshot_id='a'*64)
    for name, data in [('mail-safety-result.json', receipt),
                       ('release-intent.json', dict(receipt, operation_id='b'*32))]:
        path = work / name
        path.write_text(json.dumps(data)); path.chmod(0o600)
    for name in ('data', 'ready-0', 'unrelated'):
        (work / name).mkdir()
        (work / name / 'message').write_text(name)
    return ident, work


def test_completed_cleanup_keeps_journals_and_unrelated_data(completed):
    ident, work = completed
    cleanup.cleanup_preparation(ident)
    assert not (work / 'data').exists()
    assert not (work / 'ready-0').exists()
    assert (work / 'unrelated/message').read_text() == 'unrelated'
    assert journal.read(work / 'switch.json')['restore_id'] == ident
    assert (work / 'release-intent.json').exists()
    with write_session() as session:
        row = session.get(SnapshotRestore, ident)
        assert row.summary == {'guards_released': True, 'mailboxes': 1, 'preparation_cleaned': True}
        assert row.safety_snapshot_id == 'a'*64
    cleanup.cleanup_preparation(ident)


@pytest.mark.parametrize('status', ['pending', 'running', 'failed'])
def test_incomplete_restore_is_never_cleaned(completed, status):
    ident, work = completed
    with write_session() as session:
        session.get(SnapshotRestore, ident).status = status
    with pytest.raises(ValidationError, match='Only completed'):
        cleanup.cleanup_preparation(ident)
    assert (work / 'data/message').exists()


def test_interrupted_cleanup_resumes_without_touching_evidence(completed, monkeypatch):
    ident, work = completed
    original = cleanup.shutil.rmtree
    def fail_second(path, **kwargs):
        if path == 'ready-0':
            raise OSError('disk error')
        return original(path, **kwargs)
    fail_second.avoids_symlink_attacks = True
    monkeypatch.setattr(cleanup.shutil, 'rmtree', fail_second)
    with pytest.raises(OSError, match='disk error'):
        cleanup.cleanup_preparation(ident)
    assert not (work / 'data').exists()
    assert (work / 'ready-0/message').exists()
    with write_session() as session:
        assert not session.get(SnapshotRestore, ident).summary.get('preparation_cleaned')
    monkeypatch.setattr(cleanup.shutil, 'rmtree', original)
    cleanup.cleanup_preparation(ident)
    assert not (work / 'ready-0').exists()
    assert (work / 'switch.json').exists()


def test_preparation_root_symlink_rejected_before_any_removal(completed, tmp_path):
    ident, work = completed
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'keep').write_text('keep')
    (work / 'ready-0').rename(work / 'retained')
    (work / 'ready-0').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValidationError, match='not a directory'):
        cleanup.cleanup_preparation(ident)
    assert (work / 'data/message').exists()
    assert (outside / 'keep').read_text() == 'keep'


def test_nested_symlink_is_unlinked_without_following(completed, tmp_path):
    ident, work = completed
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'keep').write_text('keep')
    (work / 'data/redirect').symlink_to(outside, target_is_directory=True)
    cleanup.cleanup_preparation(ident)
    assert (outside / 'keep').read_text() == 'keep'


def test_wrong_release_receipt_rejected(completed):
    ident, work = completed
    path = work / 'release-intent.json'
    receipt = json.loads(path.read_text()); receipt['restore_id'] += 1
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValidationError, match='release intent'):
        cleanup.cleanup_preparation(ident)
    assert (work / 'ready-0/message').exists()


def test_cleanup_failure_does_not_reclassify_completed_restore(completed, monkeypatch):
    from daemon import snapshot_restores as restores
    ident, work = completed
    monkeypatch.setattr(cleanup, 'cleanup_preparation', lambda ident: (_ for _ in ()).throw(OSError()))
    restores._cleanup_mail_preparation(ident)
    with write_session() as session:
        assert session.get(SnapshotRestore, ident).status == 'completed'
    assert (work / 'data/message').exists()


def test_startup_retries_completed_preparation_only(completed, monkeypatch):
    from daemon import snapshot_restores as restores
    ident, work = completed
    def complete_displaced(ident):
        with write_session() as session:
            row = session.get(SnapshotRestore, ident)
            row.summary = {**row.summary, 'displaced_cleaned': True}
    monkeypatch.setattr(restores, '_cleanup_mail_displaced', complete_displaced)
    queued = []
    monkeypatch.setattr(restores.jobs._executor, 'submit', lambda fn, *args: queued.append((fn, args)))
    restores.recover_restores()
    assert queued == [(restores.retry_mail_preparation_cleanup, (ident,))]
    queued[0][0](*queued[0][1])
    assert not (work / 'data').exists()
    queued.clear()
    restores.recover_restores()
    assert queued == []


def test_retention_protects_mail_until_displaced_cleanup_finishes(completed, monkeypatch):
    from daemon import snapshot_restores as restores
    ident, work = completed
    with write_session() as session:
        first = session.get(SnapshotRestore, ident)
        run = session.get(SnapshotRun, first.run_id)
        account_id, destination_id, policy_id = first.account_id, run.destination_id, run.policy_id
        second = SnapshotRestore(account_id=account_id, run_id=run.id, status='completed',
                                 selection={'kind': 'mail'}, safety_snapshot_id='b'*64,
                                 summary={'displaced_cleaned': True})
        latest = SnapshotRestore(account_id=account_id, run_id=run.id, status='completed',
                                 selection={'kind': 'files'}, safety_snapshot_id='c'*64)
        session.add_all([second, latest])
    forgotten = []
    monkeypatch.setattr(restores.storage, 'snapshots', lambda *args: [{'id': v*64} for v in 'abc'])
    monkeypatch.setattr(restores.storage, 'forget', lambda repo, account, ids, **kw: forgotten.extend(ids))
    assert restores.apply_safety_retention({}, account_id, destination_id, policy_id, 1) == 1
    assert forgotten == ['b'*64]
    with write_session() as session:
        first = session.get(SnapshotRestore, ident)
        assert first.safety_snapshot_id == 'a'*64
        first.summary = {**first.summary, 'displaced_cleaned': True}
    forgotten.clear()
    assert restores.apply_safety_retention({}, account_id, destination_id, policy_id, 1) == 1
    assert forgotten == ['a'*64]
