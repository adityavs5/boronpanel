import pytest

from daemon import snapshot_restores as restores, snapshot_jobs as jobs
from shared.db import write_session
from shared.models import Account, SnapshotDestination, SnapshotPolicy, SnapshotRun, SnapshotRestore
from shared.validation import ValidationError


@pytest.fixture
def retention(isolated_db, monkeypatch):
    with write_session() as session:
        account = Account(username='alpha', status='active'); session.add(account); session.flush()
        destination = SnapshotDestination(name='Local', kind='local', path='/tmp/test-only', namespace='test-only')
        session.add(destination); session.flush()
        policy = SnapshotPolicy(name='Policy', destination_id=destination.id); session.add(policy); session.flush()
        run = SnapshotRun(account_id=account.id, destination_id=destination.id, policy_id=policy.id)
        session.add(run); session.flush()
    existing = {value * 64 for value in 'abcdef'}
    removed = []
    monkeypatch.setattr(restores.storage, 'snapshots', lambda *a: [{'id': value} for value in existing])
    def forget(repo, owner, values, **kwargs):
        removed.extend(values); existing.difference_update(values)
    monkeypatch.setattr(restores.storage, 'forget', forget)
    def create(primary='a', rollback='b', status='completed', finalized=True, selection=None):
        refs = {'restore': primary * 64}
        if rollback: refs['rollback'] = rollback * 64
        with write_session() as session:
            row = SnapshotRestore(account_id=account.id, run_id=run.id, status=status,
                selection=selection or {'kind': 'mail_routing'}, safety_snapshot_id=primary * 64,
                summary={'routing_safety_snapshots': refs, 'routing_finalized': finalized})
            session.add(row); session.flush()
        return row.id
    def apply(): return restores.apply_safety_retention({}, account.id, destination.id, policy.id, 1)
    return create, apply, existing, removed


def test_old_finalized_pair_expires_together(retention):
    create, apply, _, removed = retention
    old = create(); create('c', 'd')
    assert apply() == 2 and removed == ['a' * 64, 'b' * 64]
    row = jobs._row(SnapshotRestore, old)
    assert row.safety_snapshot_id is None and row.summary['routing_safety_snapshots'] == {}
    assert row.summary['routing_safety_expired'] and row.summary['safety_snapshot_expired']


@pytest.mark.parametrize('status,finalized', [('running', False), ('failed', False), ('completed', False)])
def test_unresolved_routing_pair_remains_protected(retention, status, finalized):
    create, apply, _, removed = retention
    old = create(status=status, finalized=finalized); create('c', 'd')
    assert apply() == 0 and removed == []
    assert jobs._row(SnapshotRestore, old).summary['routing_safety_snapshots'] == {'restore': 'a' * 64, 'rollback': 'b' * 64}


def test_queued_recovery_protects_secondary_copy(retention):
    create, apply, _, removed = retention
    old = create(); create('c', 'd')
    create('e', None, status='pending', selection={'kind': 'mail_routing', 'source_snapshot_id': 'b' * 64})
    assert apply() == 1 and removed == ['a' * 64]
    row = jobs._row(SnapshotRestore, old)
    assert row.safety_snapshot_id is None
    assert row.summary['routing_safety_snapshots'] == {'rollback': 'b' * 64}


def test_storage_failure_retains_all_references_and_missing_snapshot_retry(retention, monkeypatch):
    create, apply, existing, _ = retention
    old = create(); create('c', 'd')
    def fail_after_delete(repo, owner, values, **kwargs):
        existing.difference_update(values)
        raise RuntimeError('lost storage acknowledgement')
    monkeypatch.setattr(restores.storage, 'forget', fail_after_delete)
    with pytest.raises(RuntimeError): apply()
    assert len(jobs._row(SnapshotRestore, old).summary['routing_safety_snapshots']) == 2
    monkeypatch.setattr(restores.storage, 'forget', lambda *a, **k: pytest.fail('Already deleted snapshots need no second delete'))
    assert apply() == 2
    assert jobs._row(SnapshotRestore, old).summary['routing_safety_snapshots'] == {}


def test_invalid_reference_blocks_cleanup(retention):
    create, apply, _, removed = retention
    old = create(); create('c', 'd')
    with write_session() as session:
        session.get(SnapshotRestore, old).summary = {'routing_finalized': True, 'routing_safety_snapshots': {'rollback': '../bad'}}
    with pytest.raises(ValidationError, match='retention requires inspection'): apply()
    assert removed == []


def test_recording_safety_is_immutable_and_requires_original_first(retention):
    create, _, _, _ = retention
    ident = create(status='running')
    with write_session() as session:
        row = session.get(SnapshotRestore, ident); row.summary = {}; row.safety_snapshot_id = None
    with pytest.raises(ValidationError, match='original routing safety'):
        restores.record_routing_safety(ident, 'b' * 64, purpose='rollback')
    restores.record_routing_safety(ident, 'a' * 64)
    restores.record_routing_safety(ident, 'a' * 64)
    restores.record_routing_safety(ident, 'b' * 64, purpose='rollback')
    with pytest.raises(ValidationError, match='cannot replace'):
        restores.record_routing_safety(ident, 'c' * 64, purpose='rollback')
    row = jobs._row(SnapshotRestore, ident)
    assert row.safety_snapshot_id == 'a' * 64
    assert row.summary['routing_safety_snapshots'] == {'restore': 'a' * 64, 'rollback': 'b' * 64}
