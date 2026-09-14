import pytest

from daemon import snapshot_mail_routing_journal as journal, snapshot_mail_routing_recovery as recovery
from daemon import snapshot_mail_service as service, snapshot_mail_guard as guard, snapshot_mail_routing as routing
from shared.config import settings
from shared.validation import ValidationError
from tests.test_snapshot_databases import sql_server, sql
from tests.test_snapshot_mail_metadata import mail_database
from tests.test_snapshot_mail_routing_sql import routing_account
from tests.test_snapshot_mail_routing_recovery import prepared, repository
from tests.test_snapshot_mail_routing_journal import operation


def test_real_encrypted_rollback_of_sql_only_partial_application(prepared, tmp_path, monkeypatch):
    account, _, plan, script = prepared
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(tmp_path / 'guards'))
    monkeypatch.setattr(journal, 'require_stopped', lambda service: None)
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: {'state': 'terminal', 'result': 'exit-code'})
    monkeypatch.setattr(service, 'service_status', lambda *a, **k: {'ActiveState': 'active', 'SubState': 'running'})
    repo = repository(tmp_path)
    safety = recovery.save_previous(repo, account, 101, plan['previous'])
    original = journal.create(repo, account, 101, plan, safety['snapshot_id'])
    journal.acquire_guards(account, original)
    write = journal._write
    def lose_sql_checkpoint(path, payload, **kwargs):
        if payload['phase'] == 'sql_applied': raise OSError('SQL checkpoint interrupted')
        return write(path, payload, **kwargs)
    monkeypatch.setattr(journal, '_write', lose_sql_checkpoint)
    with pytest.raises(OSError): journal.execute(account, original)
    monkeypatch.setattr(journal, '_write', write)
    before_original_journal = original.read_bytes()
    rollback = journal.prepare_rollback(repo, account, original)
    reverse = journal.read(account, rollback)
    assert reverse['direction'] == 'rollback'
    assert reverse['guards'] == journal.read(account, original)['guards']
    assert reverse['operation_id'] != reverse['source_operation_id']
    assert reverse['safety_snapshot_id'] != safety['snapshot_id']
    partial = recovery.load_previous(repo, account, reverse['safety_snapshot_id'], 101, kind='rollback')
    assert partial['routing'] == plan['desired_routing'] and partial['scripts'] == plan['previous']['scripts']
    with pytest.raises(ValidationError, match='superseded'): journal.execute(account, original)
    with pytest.raises(ValidationError, match='superseded'): journal.finalize(repo, account, original)
    journal.execute(account, rollback)
    assert routing.capture(account) == plan['previous']['routing']
    assert script.read_bytes() == b'# custom previous script\r\nkeep;\r\n'
    result = journal.finalize(repo, account, rollback)
    assert result['rolled_back'] and result['guards_released']
    assert journal.recovery_state(account, original)['rolled_back']
    assert original.read_bytes() == before_original_journal
    assert recovery.load_previous(repo, account, safety['snapshot_id'], 101) == plan['previous']
    with pytest.raises(ValidationError, match='source job'):
        recovery.load_previous(repo, account, reverse['safety_snapshot_id'], 101)


def test_running_worker_blocks_rollback_before_safety_read(operation, monkeypatch):
    account, _, _, _, path = operation
    journal.acquire_guards(account, path)
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: {'state': 'running'})
    monkeypatch.setattr(recovery, 'load_previous', lambda *a: pytest.fail('No decryption expected'))
    with pytest.raises(ValidationError, match='still running'): journal.prepare_rollback(None, account, path)
    assert not path.with_name('mail-routing-rollback-operation.json').exists()


def test_unrelated_script_edits_are_not_overwritten_by_rollback(operation, monkeypatch):
    account, _, _, script, path = operation
    journal.acquire_guards(account, path)
    journal.execute(account, path)
    script.write_bytes(b'# later custom change\nkeep;\n')
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: {'state': 'terminal'})
    monkeypatch.setattr(recovery, 'save_previous', lambda *a, **k: pytest.fail('Do not authorize reverse writes'))
    with pytest.raises(ValidationError, match='unrelated changes'): journal.prepare_rollback(None, account, path)
    assert script.read_bytes() == b'# later custom change\nkeep;\n'
    with guard.owned_guards(journal.read(account, path)['guards'], 91): pass


def test_failed_rollback_safety_encryption_keeps_current_state_and_guards(operation, monkeypatch):
    account, _, _, script, path = operation
    journal.acquire_guards(account, path)
    journal.execute(account, path)
    current_rules, current_script = routing.capture(account), script.read_bytes()
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: {'state': 'terminal'})
    def fail(*a, **k): raise RuntimeError('rollback storage unavailable')
    monkeypatch.setattr(recovery, 'save_previous', fail)
    with pytest.raises(RuntimeError, match='storage unavailable'): journal.prepare_rollback(None, account, path)
    assert routing.capture(account) == current_rules and script.read_bytes() == current_script
    assert not path.with_name('mail-routing-rollback-operation.json').exists()
    with guard.owned_guards(journal.read(account, path)['guards'], 91): pass


@pytest.mark.parametrize('lost_phase', ['applying_sql', 'sql_applied', 'verified'])
def test_interrupted_reverse_operation_resumes_with_same_safety(prepared, tmp_path, monkeypatch, lost_phase):
    account, _, plan, script = prepared
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(tmp_path / 'guards'))
    monkeypatch.setattr(journal, 'require_stopped', lambda service: None)
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: {'state': 'terminal'})
    monkeypatch.setattr(service, 'service_status', lambda *a, **k: {'ActiveState': 'active', 'SubState': 'running'})
    repo = repository(tmp_path)
    saved = recovery.save_previous(repo, account, 102, plan['previous'])
    original = journal.create(repo, account, 102, plan, saved['snapshot_id'])
    journal.acquire_guards(account, original)
    journal.execute(account, original)
    reverse = journal.prepare_rollback(repo, account, original)
    before = journal.read(account, reverse)
    write = journal._write
    def interrupted(path, payload, **kwargs):
        if payload['phase'] == lost_phase: raise OSError('interrupted reverse checkpoint')
        return write(path, payload, **kwargs)
    monkeypatch.setattr(journal, '_write', interrupted)
    with pytest.raises(OSError): journal.execute(account, reverse)
    monkeypatch.setattr(journal, '_write', write)
    # Running worker means observe, with no decrypt, retirement or second launch.
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: {'state': 'running'})
    assert journal.resume_rollback(repo, account, reverse) == {'state': 'waiting'}
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: {'state': 'terminal'})
    from daemon import snapshot_mail_guard_config
    monkeypatch.setattr(snapshot_mail_guard_config, 'verify', lambda: None)
    retired, launched = [], []
    monkeypatch.setattr(service, 'retire_switch', lambda ident: retired.append(ident))
    def supervised(command, ident):
        launched.append(ident)
        journal.execute(account, command[-1])
    monkeypatch.setattr(service, 'supervised_command', supervised)
    result = journal.resume_rollback(repo, account, reverse)
    assert result['rolled_back'] and result['guards_released']
    assert retired == launched == [before['operation_id']]
    assert routing.capture(account) == plan['previous']['routing']
    assert script.read_bytes() == b'# custom previous script\r\nkeep;\r\n'
    after = journal.read(account, reverse)
    assert after['safety_snapshot_id'] == before['safety_snapshot_id']
    assert after['previous'] == before['previous']
    assert after['phase'] == 'completed'


def test_reverse_continuation_rejects_unrelated_script_changes(operation, monkeypatch):
    from copy import deepcopy
    account, _, plan, script, original = operation
    journal.acquire_guards(account, original)
    journal.execute(account, original)
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: {'state': 'terminal'})
    monkeypatch.setattr(recovery, 'save_previous', lambda *a, **k: {'snapshot_id': 'b' * 64})
    current = {'routing': routing.capture(account), 'scripts': journal.sieve.capture(account, ['inbox@alpha.example.test'])}
    monkeypatch.setattr(recovery, 'load_previous', lambda *a, **k: deepcopy(current if k.get('kind') == 'rollback' else plan['previous']))
    reverse = journal.prepare_rollback(None, account, original)
    payload = journal.read(account, reverse); payload['phase'] = 'applying_scripts'; journal._write(reverse, payload)
    script.write_bytes(b'# later edit\nkeep;\n')
    rules = routing.capture(account)
    with pytest.raises(ValidationError, match='unrelated changes'):
        journal.execute(account, reverse)
    assert routing.capture(account) == rules
    assert script.read_bytes() == b'# later edit\nkeep;\n'
    with guard.owned_guards(payload['guards'], 91): pass


def test_prelaunch_reverse_recognizes_only_its_bound_original_unit(monkeypatch):
    payload = {'phase': 'guarded', 'operation_id': 'a' * 32, 'source_operation_id': 'b' * 32}
    calls = []
    def inspect(ident, **kwargs):
        calls.append(ident)
        if ident != payload['source_operation_id']: raise ValidationError('unit belongs to another operation')
        return {'state': 'terminal'}
    monkeypatch.setattr(service, 'inspect_switch', inspect)
    assert journal._rollback_worker(payload) == ({'state': 'terminal'}, 'b' * 32)
    assert calls == ['a' * 32, 'b' * 32]
    payload['phase'] = 'applying_sql'; calls.clear()
    with pytest.raises(ValidationError): journal._rollback_worker(payload)
    assert calls == ['a' * 32]
