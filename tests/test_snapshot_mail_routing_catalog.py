import json
from types import SimpleNamespace

import pytest

from daemon import snapshot_jobs as jobs, snapshot_storage as storage, snapshot_restores as restores
from daemon import snapshot_mail_metadata, snapshot_mail_routing_recovery as recovery
from shared.db import write_session
from shared.models import SnapshotDestination, SnapshotPolicy, SnapshotRun
from shared.validation import ValidationError
from tests.test_snapshot_databases import sql_server, sql
from tests.test_snapshot_mail_metadata import mail_database
from tests.test_snapshot_mail_routing_sql import routing_account
from tests.test_snapshot_jobs import make_destination


@pytest.fixture
def saved_routing(routing_account, mail_database, tmp_path):
    account, foreign = routing_account
    destination = make_destination(tmp_path)
    repo = jobs.repository(jobs._row(SnapshotDestination, destination['id']))
    payload = snapshot_mail_metadata.capture(account.username, [SimpleNamespace(domain='alpha.example.test'),
                                                               SimpleNamespace(domain='bravo.example.test')])
    source = jobs.private_directory('sources', f'account-{account.id}') / 'mail-recovery.json'
    source.write_text(json.dumps(payload)); source.chmod(0o600)
    backup = storage.backup(repo, account.id, [str(source)])
    with write_session() as session:
        policy = SnapshotPolicy(name='Catalog', destination_id=destination['id']); session.add(policy); session.flush()
        run = SnapshotRun(account_id=account.id, destination_id=destination['id'], policy_id=policy.id,
                          options={'components': ['mail']}, status='completed', snapshot_id=backup['snapshot_id'])
        session.add(run); session.flush()
    return account, foreign, repo, run


def test_real_catalog_decrypts_once_and_exposes_counts_only(saved_routing, mail_database, monkeypatch):
    account, _, repo, run = saved_routing
    _, _, password, hashed = mail_database
    decrypt = storage.restore_to
    calls = []
    def counted(*args, **kwargs):
        calls.append(True)
        return decrypt(*args, **kwargs)
    monkeypatch.setattr(storage, 'restore_to', counted)
    result = restores.routing_options({'username': account.username, 'run_id': run.id})
    assert len(calls) == 1
    assert result['domains'][0] == dict(domain='alpha.example.test', available=True, reason=None,
                                      forwarders=1, catchall=True, autoresponders=1)
    assert result['domains'][1]['domain'] == 'bravo.example.test'
    assert result['domains'][1]['available'] is False
    assert set(result['domains'][1]) == {'domain', 'available', 'reason'}
    for value in (password, hashed, 'target@example.test', 'Test-only reply', 'source_local_part', 'script_base64'):
        assert value not in repr(result)
    assert list(jobs.private_directory('mail-routing-metadata').iterdir()) == []


def test_foreign_run_and_busy_repository_rejected_before_decryption(saved_routing, monkeypatch):
    account, foreign, _, run = saved_routing
    monkeypatch.setattr(storage, 'restore_to', lambda *a, **k: pytest.fail('Must reject before decrypting'))
    with pytest.raises(ValidationError, match='not found for this account'):
        restores.routing_options({'username': foreign.username, 'run_id': run.id})
    with jobs.lock(f'repository-{run.destination_id}', blocking=False):
        with pytest.raises(ValidationError, match='destination is busy'):
            restores.routing_options({'username': account.username, 'run_id': run.id})


def test_missing_mail_component_has_no_decryption(saved_routing, monkeypatch):
    account, _, _, run = saved_routing
    with write_session() as session: session.get(SnapshotRun, run.id).options = {'components': ['files']}
    monkeypatch.setattr(storage, 'restore_to', lambda *a, **k: pytest.fail('No mail metadata expected'))
    assert restores.routing_options({'username': account.username, 'run_id': run.id})['domains'] == []
