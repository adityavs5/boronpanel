from copy import deepcopy

import pytest

from daemon import snapshot_mail_routing_journal as journal, snapshot_mail_routing_recovery as recovery
from daemon import snapshot_mail_guard as guard, snapshot_mail_service as service, mail
from shared.validation import ValidationError
from tests.test_snapshot_databases import sql_server, sql
from tests.test_snapshot_mail_metadata import mail_database
from tests.test_snapshot_mail_routing_sql import routing_account
from tests.test_snapshot_mail_routing_recovery import prepared
from tests.test_snapshot_mail_routing_journal import operation


@pytest.fixture
def verified(operation, monkeypatch):
    account, foreign, plan, script, path = operation
    journal.acquire_guards(account, path)
    journal.execute(account, path)
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: {'state': 'terminal', 'result': 'success'})
    monkeypatch.setattr(service, 'service_status', lambda *a, **k: {'ActiveState': 'active', 'SubState': 'running'})
    return operation


def test_finalize_records_release_intent_and_completes_idempotently(verified, monkeypatch):
    account, _, _, _, path = verified
    release = guard.release_batch
    def check_intent(entries, ident):
        assert journal.read(account, path)['phase'] == 'release_intent'
        return release(entries, ident)
    monkeypatch.setattr(guard, 'release_batch', check_intent)
    result = journal.finalize(None, account, path)
    assert result['state'] == 'completed' and result['guards_released']
    assert journal.read(account, path)['phase'] == 'completed'
    monkeypatch.setattr(guard, 'release_batch', lambda *a: pytest.fail('No second release'))
    assert journal.finalize(None, account, path) == result


def test_running_worker_prevents_finalization_and_decryption(verified, monkeypatch):
    account, _, _, _, path = verified
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: {'state': 'running'})
    monkeypatch.setattr(recovery, 'load_previous', lambda *a: pytest.fail('No decryption while worker runs'))
    assert journal.finalize(None, account, path)['state'] == 'waiting'
    payload = journal.read(account, path)
    with guard.owned_guards(payload['guards'], 91): pass
    assert payload['phase'] == 'verified'


def test_stopped_mail_service_retains_guards(verified, monkeypatch):
    account, _, _, _, path = verified
    monkeypatch.setattr(service, 'service_status', lambda *a, **k: {'ActiveState': 'inactive', 'SubState': 'dead'})
    with pytest.raises(ValidationError, match='not resumed'): journal.finalize(None, account, path)
    with guard.owned_guards(journal.read(account, path)['guards'], 91): pass


def test_mismatched_encrypted_safety_retains_guards(verified, monkeypatch):
    account, _, plan, _, path = verified
    different = deepcopy(plan['previous']); different['routing']['domains'][0]['forwards'] = []
    monkeypatch.setattr(recovery, 'load_previous', lambda *a: different)
    with pytest.raises(ValidationError, match='safety changed'): journal.finalize(None, account, path)
    with guard.owned_guards(journal.read(account, path)['guards'], 91): pass


def test_interrupted_guard_release_resumes_without_reapplying(verified, monkeypatch):
    account, _, _, script, path = verified
    release = guard.release_batch
    def lost_ack(entries, ident):
        release(entries, ident)
        raise OSError('lost release acknowledgement')
    monkeypatch.setattr(guard, 'release_batch', lost_ack)
    original = script.read_bytes()
    with pytest.raises(OSError, match='acknowledgement'): journal.finalize(None, account, path)
    assert journal.read(account, path)['phase'] == 'release_intent'
    monkeypatch.setattr(guard, 'release_batch', release)
    assert journal.finalize(None, account, path)['state'] == 'completed'
    assert script.read_bytes() == original


def test_lost_completion_checkpoint_resumes_from_release_intent(verified, monkeypatch):
    account, _, _, _, path = verified
    write = journal._write
    def fail_complete(path, payload, **kwargs):
        if payload['phase'] == 'completed': raise OSError('completion checkpoint unavailable')
        return write(path, payload, **kwargs)
    monkeypatch.setattr(journal, '_write', fail_complete)
    with pytest.raises(OSError, match='checkpoint unavailable'): journal.finalize(None, account, path)
    assert journal.read(account, path)['phase'] == 'release_intent'
    monkeypatch.setattr(journal, '_write', write)
    assert journal.finalize(None, account, path)['state'] == 'completed'


def test_changed_state_blocks_guard_release(verified):
    account, _, _, _, path = verified
    mail.create_forward('alpha.example.test', 'newer', 'newer@example.test')
    with pytest.raises(ValidationError, match='state changed'): journal.finalize(None, account, path)
    with guard.owned_guards(journal.read(account, path)['guards'], 91): pass


def test_release_recovery_never_removes_a_later_jobs_guard(verified, monkeypatch):
    account, _, _, _, path = verified
    release = guard.release_batch
    replacement = []
    def release_then_replace(entries, ident):
        release(entries, ident)
        entry = entries[0]
        replacement.append(dict(domain=entry['domain'], local_part=entry['local_part'],
                                token=guard.block(entry['domain'], entry['local_part'], 92)))
        raise OSError('interruption after release')
    monkeypatch.setattr(guard, 'release_batch', release_then_replace)
    with pytest.raises(OSError): journal.finalize(None, account, path)
    assert journal.recovery_state(account, path)['state'] == 'finalizing'
    monkeypatch.setattr(guard, 'release_batch', release)
    with pytest.raises(ValidationError, match='another job'): journal.finalize(None, account, path)
    with guard.owned_guards(replacement, 92): pass


def test_completed_recovery_does_not_inspect_reused_supervisor_unit(verified, monkeypatch):
    account, _, _, _, path = verified
    journal.finalize(None, account, path)
    monkeypatch.setattr(service, 'inspect_switch', lambda *a, **k: pytest.fail('Completed operation needs no supervisor lookup'))
    assert journal.recovery_state(account, path)['state'] == 'completed'
