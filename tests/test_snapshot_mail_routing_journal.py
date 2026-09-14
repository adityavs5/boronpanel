from copy import deepcopy

import pytest

from daemon import mail, snapshot_mail_routing as routing, snapshot_mail_guard as guard
from daemon import snapshot_mail_routing_journal as journal, snapshot_mail_routing_recovery as recovery
from shared.config import settings
from shared.validation import ValidationError
from tests.test_snapshot_databases import sql_server, sql
from tests.test_snapshot_mail_metadata import mail_database
from tests.test_snapshot_mail_routing_sql import routing_account
from tests.test_snapshot_mail_routing_recovery import prepared, repository


@pytest.fixture
def operation(prepared, tmp_path, monkeypatch):
    account, foreign, plan, script = prepared
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(tmp_path / 'guards'))
    monkeypatch.setattr(recovery, 'load_previous', lambda repo, owner, snapshot, ident: deepcopy(plan['previous']))
    monkeypatch.setattr(journal, 'require_stopped', lambda service: None)
    path = journal.create(None, account, 91, plan, 'a' * 64)
    return account, foreign, plan, script, path


def test_durable_sql_and_script_execution_retains_guards(operation):
    account, _, plan, script, path = operation
    assert journal.read(account, path)['phase'] == 'prepared'
    journal.acquire_guards(account, path)
    result = journal.execute(account, path)
    assert result == {'domains': ['alpha.example.test'], 'mailboxes': 1}
    payload = journal.read(account, path)
    assert payload['phase'] == 'verified'
    assert payload['completed_scripts'] == ['inbox@alpha.example.test']
    assert routing.capture(account) == plan['desired_routing']
    assert b'vacation' in script.read_bytes()
    with guard.owned_guards(payload['guards'], 91): pass
    assert journal.inspect(account, path) == dict(phase='verified', sql_matches_desired=True,
        sql_matches_previous=False, scripts_match_desired=True, scripts_match_previous=False)
    with pytest.raises(ValidationError, match='not replay'): journal.execute(account, path)


def test_lost_sql_checkpoint_is_reported_from_actual_state(operation, monkeypatch):
    account, _, plan, script, path = operation
    original = script.read_bytes()
    journal.acquire_guards(account, path)
    write = journal._write
    def fail_after_commit(path, payload, **kwargs):
        if payload['phase'] == 'sql_applied': raise OSError('simulated checkpoint failure')
        return write(path, payload, **kwargs)
    monkeypatch.setattr(journal, '_write', fail_after_commit)
    with pytest.raises(OSError, match='checkpoint failure'): journal.execute(account, path)
    assert script.read_bytes() == original
    observed = journal.inspect(account, path)
    assert observed == dict(phase='applying_sql', sql_matches_desired=True,
        sql_matches_previous=False, scripts_match_desired=False, scripts_match_previous=True)
    with pytest.raises(ValidationError, match='not replay'): journal.execute(account, path)


def test_guard_publication_interruption_resumes_same_tokens(operation, monkeypatch):
    account, _, _, _, path = operation
    block = guard.block
    def lost_ack(*args, **kwargs):
        block(*args, **kwargs)
        raise OSError('lost guard acknowledgement')
    monkeypatch.setattr(guard, 'block', lost_ack)
    before = journal.read(account, path)['guards']
    with pytest.raises(OSError, match='acknowledgement'): journal.acquire_guards(account, path)
    assert journal.read(account, path)['phase'] == 'guarding'
    monkeypatch.setattr(guard, 'block', lambda *a, **k: pytest.fail('Must recognize the already owned guard'))
    journal.acquire_guards(account, path)
    after = journal.read(account, path)
    assert after['phase'] == 'guarded' and after['guards'] == before
    with guard.owned_guards(after['guards'], 91): pass


def test_live_mail_service_precondition_rejects_before_sql(operation, monkeypatch):
    account, _, plan, script, path = operation
    original = script.read_bytes()
    journal.acquire_guards(account, path)
    def running(service): raise ValidationError('Mail service has not stopped completely')
    monkeypatch.setattr(journal, 'require_stopped', running)
    with pytest.raises(ValidationError, match='not stopped'): journal.execute(account, path)
    assert routing.capture(account) == plan['previous']['routing'] and script.read_bytes() == original
    assert journal.read(account, path)['phase'] == 'guarded'


def test_changed_routing_after_safety_copy_prevents_application(operation):
    account, _, _, script, path = operation
    original = script.read_bytes()
    journal.acquire_guards(account, path)
    mail.create_forward('alpha.example.test', 'changed', 'test@example.test')
    before = routing.capture(account)
    with pytest.raises(ValidationError, match='changed after safety'): journal.execute(account, path)
    assert routing.capture(account) == before and script.read_bytes() == original
    assert journal.read(account, path)['phase'] == 'guarded'


def test_journal_rejects_foreign_account_and_moved_job_path(operation):
    account, foreign, _, _, path = operation
    with pytest.raises(ValidationError): journal.read(foreign, path)
    other = path.with_name('unbound-operation.json'); other.write_bytes(path.read_bytes()); other.chmod(0o600)
    with pytest.raises(ValidationError, match='path does not match'): journal.read(account, other)


def test_journal_creation_verifies_real_encrypted_safety(prepared, tmp_path, monkeypatch):
    account, _, plan, _ = prepared
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(tmp_path / 'guards'))
    repo = repository(tmp_path)
    result = recovery.save_previous(repo, account, 92, plan['previous'])
    mismatched = deepcopy(plan); mismatched['previous']['routing']['domains'][0]['forwards'] = []
    with pytest.raises(ValidationError, match='safety does not match'):
        journal.create(repo, account, 92, mismatched, result['snapshot_id'])
    path = journal.create(repo, account, 92, plan, result['snapshot_id'])
    assert journal.read(account, path)['safety_snapshot_id'] == result['snapshot_id']
    assert not (tmp_path / 'guards').exists()


def test_recovery_waits_for_confirmed_worker_before_inspection(operation, monkeypatch):
    from daemon import snapshot_mail_service
    account, _, _, _, path = operation
    monkeypatch.setattr(snapshot_mail_service, 'inspect_switch', lambda *a, **k: {'state': 'running'})
    monkeypatch.setattr(journal, 'inspect', lambda *a, **k: pytest.fail('Must not inspect a running worker'))
    assert journal.recovery_state(account, path)['state'] == 'waiting'


def test_offline_worker_rejects_sql_contention_promptly(operation):
    from daemon import snapshot_jobs
    account, _, _, _, path = operation
    journal.acquire_guards(account, path)
    with snapshot_jobs.lock('database-mutations', blocking=False):
        with pytest.raises(ValidationError, match='in progress'):
            journal.execute(account, path)
    assert journal.read(account, path)['phase'] == 'guarded'


def test_launch_passes_only_bound_internal_worker_arguments(operation, monkeypatch):
    import sys
    from daemon import snapshot_mail_service, snapshot_mail_guard_config
    account, _, _, _, path = operation
    journal.acquire_guards(account, path)
    monkeypatch.setattr(snapshot_mail_guard_config, 'verify', lambda: None)
    called = []
    def launch(command, operation_id):
        from daemon import snapshot_jobs
        # The launcher must not retain a SQL lock needed by its child process.
        with snapshot_jobs.lock('database-mutations', blocking=False): pass
        called.append((command, operation_id))
        return {'launched': True}
    monkeypatch.setattr(snapshot_mail_service, 'supervised_command', launch)
    assert journal.launch(account, path) == {'launched': True}
    payload = journal.read(account, path)
    assert called == [([sys.executable, '-m', 'daemon.snapshot_mail_routing_journal', str(account.id), str(path)], payload['operation_id'])]
