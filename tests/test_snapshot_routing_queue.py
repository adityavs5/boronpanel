from copy import deepcopy
import os

import pytest

from daemon import snapshot_restores as restores, snapshot_jobs as jobs, snapshot_routing_worker as worker
from daemon import snapshot_mail_routing_journal as journal, snapshot_mail_routing_recovery as recovery
from daemon import snapshot_mail_service as service, snapshot_mail_guard_config as guard_config, mail
from shared.config import settings
from shared.db import write_session
from shared.models import Account, SnapshotRestore
from shared.validation import ValidationError
from tests.test_snapshot_databases import sql_server, sql
from tests.test_snapshot_mail_metadata import mail_database
from tests.test_snapshot_mail_routing_sql import routing_account
from tests.test_snapshot_mail_routing_catalog import saved_routing


@pytest.fixture
def queue(saved_routing, tmp_path, monkeypatch):
    account, foreign, repo, run = saved_routing
    with write_session() as session:
        session.get(Account, account.id).uid = 2000; session.get(Account, account.id).gid = 2000
    home = tmp_path / 'vmail' / 'alpha.example.test' / 'inbox'; home.mkdir(parents=True)
    os.chown(home.parent, 150, 150); os.chown(home, 150, 150)
    script = home / '.dovecot.sieve'; script.write_bytes(b'# custom before restore\nkeep;\n')
    os.chown(script, 150, 150); script.chmod(0o600)
    monkeypatch.setattr(settings, 'mail_base', str(home.parent.parent))
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(tmp_path / 'guards'))
    monkeypatch.setattr(guard_config, 'verify', lambda: None)
    monkeypatch.setattr(journal, 'require_stopped', lambda service: None)
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: {'state': 'terminal', 'result': 'success'})
    monkeypatch.setattr(service, 'service_status', lambda *a, **k: {'ActiveState': 'active', 'SubState': 'running'})
    monkeypatch.setattr(journal, 'launch', lambda account, path: journal.execute(account, path))
    queued = []; monkeypatch.setattr(jobs._executor, 'submit', lambda fn, *args: queued.append((fn, args)))
    return account, foreign, repo, run, script, queued


def request(account, run):
    return dict(username=account.username, run_id=run.id, kind='mail_routing', confirmation=account.username,
                mail_domains=['alpha.example.test'], mail_pause_acknowledged=True)


def test_real_queued_routing_restore_and_exact_script_undo(queue):
    account, _, repo, run, script, queued = queue
    mail.create_forward('alpha.example.test', 'new', 'new@example.test')
    original_script = script.read_bytes()
    body = request(account, run)
    row = restores.trigger(body)
    assert queued[-1] == (restores.execute, (row['id'],))
    with pytest.raises(ValidationError, match='needs recovery'):
        mail.delete_catchall('alpha.example.test')
    restores.execute(row['id'])
    completed = jobs._row(SnapshotRestore, row['id'])
    assert completed.status == 'completed', completed.error
    assert completed.summary['routing_finalized'] and completed.summary['guards_released']
    assert completed.summary['routing_safety_snapshots']['restore'] == completed.safety_snapshot_id
    assert not script.exists()  # Saved responder was disabled.
    assert not any(item['local_part'] == 'new' for item in mail.list_forwards('alpha.example.test'))
    undo = restores.undo(dict(username=account.username, restore_id=row['id'], confirmation=account.username,
                              mail_pause_acknowledged=True))
    restores.execute(undo['id'])
    undone = jobs._row(SnapshotRestore, undo['id'])
    assert undone.status == 'completed', undone.error
    assert script.read_bytes() == original_script
    assert any(item['local_part'] == 'new' for item in mail.list_forwards('alpha.example.test'))
    assert (jobs.private_directory('restores', f"restore-{row['id']}") / 'mail-routing-operation.json').exists()


def test_queue_rejects_pause_foreign_domain_and_foreign_account(queue):
    account, foreign, _, run, _, _ = queue
    body = request(account, run); body['mail_pause_acknowledged'] = False
    with pytest.raises(ValidationError, match='interruption'): restores.trigger(body)
    body = request(account, run); body['mail_domains'] = ['bravo.example.test']
    with pytest.raises(ValidationError, match='unavailable'): restores.trigger(body)
    body = request(foreign, run)
    with pytest.raises(ValidationError, match='not found for this account'): restores.trigger(body)


def test_failure_before_journal_does_not_leave_account_blocked(queue, monkeypatch):
    account, _, _, run, script, _ = queue
    row = restores.trigger(request(account, run))
    original = script.read_bytes()
    def fail(*a, **k): raise RuntimeError('private failure marker')
    monkeypatch.setattr(recovery, 'prepare', fail)
    restores.execute(row['id'])
    failed = jobs._row(SnapshotRestore, row['id'])
    assert failed.status == 'failed' and failed.summary['routing_finalized']
    assert 'private failure marker' not in failed.error and script.read_bytes() == original
    mail.delete_catchall('alpha.example.test')


def test_startup_dispatches_running_routing_job_to_recovery(queue):
    account, _, _, run, _, queued = queue
    row = restores.trigger(request(account, run))
    restores._update(row['id'], status='running')
    queued.clear(); restores.recover_restores()
    assert (worker.recover, (row['id'],)) in queued


def test_queued_partial_failure_rolls_back_and_records_both_safety_copies(queue, monkeypatch):
    account, _, _, run, script, _ = queue
    mail.create_forward('alpha.example.test', 'before', 'before@example.test')
    original_script = script.read_bytes()
    request_row = restores.trigger(request(account, run))
    write = journal._write
    def fail_original_commit(path, payload, **kwargs):
        if payload.get('direction', 'restore') == 'restore' and payload['phase'] == 'sql_applied':
            raise OSError('simulated lost SQL checkpoint')
        return write(path, payload, **kwargs)
    monkeypatch.setattr(journal, '_write', fail_original_commit)
    restores.execute(request_row['id'])
    row = jobs._row(SnapshotRestore, request_row['id'])
    assert row.status == 'failed', row.error
    assert row.summary['rolled_back'] and row.summary['routing_finalized'] and row.summary['guards_released']
    assert set(row.summary['routing_safety_snapshots']) == {'restore', 'rollback'}
    assert row.safety_snapshot_id == row.summary['routing_safety_snapshots']['restore']
    assert script.read_bytes() == original_script
    assert any(item['local_part'] == 'before' for item in mail.list_forwards('alpha.example.test'))
    mail.delete_catchall('alpha.example.test')  # Finalized rollback releases ordinary mail edits.


def test_supervisor_observation_timeout_waits_then_finalizes_same_job(queue, monkeypatch):
    account, _, _, run, _, _ = queue
    scheduled = []; monkeypatch.setattr(worker, 'schedule', scheduled.append)
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: {'state': 'running'})
    launches = []
    def timed_out(owner, path):
        launches.append(path)
        raise TimeoutError('observation timed out')
    monkeypatch.setattr(journal, 'launch', timed_out)
    row = restores.trigger(request(account, run))
    restores.execute(row['id'])
    assert jobs._row(SnapshotRestore, row['id']).status == 'running'
    assert scheduled == [row['id']] and len(launches) == 1
    # Model that same supervised process finishing after the observer timed out.
    journal.execute(jobs._row(Account, account.id), launches[0])
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: {'state': 'terminal', 'result': 'success'})
    worker.recover(row['id'])
    assert jobs._row(SnapshotRestore, row['id']).status == 'completed'
    assert len(launches) == 1
