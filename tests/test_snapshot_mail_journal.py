import copy
import os
from pathlib import Path

import pytest

from daemon import snapshot_mail_journal as journal, snapshot_mail_exchange as exchange, snapshot_mail_guard as guard
from shared.config import settings
from shared.validation import ValidationError
from tests.test_snapshot_mail_service import isolated_service


@pytest.fixture
def saved(tmp_path, monkeypatch):
    if os.geteuid() != 0:
        pytest.skip('Private root-owned journals required')
    from daemon import snapshot_mail_guard_config
    monkeypatch.setattr(snapshot_mail_guard_config, 'verify', lambda: {'guard': 'ready'})
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    monkeypatch.setattr(settings, 'snapshot_private_dir', str(private))
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(tmp_path / 'guards'))
    monkeypatch.setattr(settings, 'mail_base', str(tmp_path / 'mail'))
    entries = []
    for local in ('one', 'two'):
        home = tmp_path / 'mail/example.test' / local
        name = '.boron-mail-ready-' + 'a' * 32
        for folder in ('Maildir', name):
            for child in ('cur', 'new', 'tmp'):
                (home / folder / child).mkdir(parents=True)
        token = guard.block('example.test', local, 1)
        entries.append(dict(domain='example.test', local_part=local, token=token,
                            plan=exchange.plan('example.test', local, name)))
    payload = dict(format=1, operation_id='b'*32, restore_id=1, undo=False, entries=entries)
    path = private / 'switch.json'
    journal.create(path, payload)
    return path, payload


def test_guarded_batch_switch_retains_guards_and_refuses_replay(saved, monkeypatch):
    path, payload = saved
    monkeypatch.setattr(journal, 'require_stopped', lambda service: None)
    assert journal.execute(path) == {'exchanged': 2}
    assert [row['state'] for row in journal.inspect(path)] == ['applied', 'applied']
    assert 'token' not in repr(journal.inspect(path))
    with pytest.raises(ValidationError):
        journal.execute(path)
    for entry in payload['entries']:
        assert (guard.Path(settings.mail_restore_guard_dir) / guard.marker_name(entry['domain'], entry['local_part'])).exists()


def test_service_barrier_failure_changes_nothing(saved, monkeypatch):
    path, payload = saved
    def refuse(service):
        raise ValidationError('still running')
    monkeypatch.setattr(journal, 'require_stopped', refuse)
    with pytest.raises(ValidationError):
        journal.execute(path)
    assert all(row['state'] == 'ready' for row in journal.inspect(path))


def test_wrong_guard_on_later_entry_prevents_entire_batch(saved, monkeypatch):
    path, payload = saved
    monkeypatch.setattr(journal, 'require_stopped', lambda service: None)
    payload['entries'][1]['token'] = 'c'*64
    path.unlink()
    journal.create(path, payload)
    with pytest.raises(ValidationError):
        journal.execute(path)
    assert all(row['state'] == 'ready' for row in journal.inspect(path))


def test_interrupted_batch_inspects_each_mailbox_without_blind_replay(saved, monkeypatch):
    path, payload = saved
    monkeypatch.setattr(journal, 'require_stopped', lambda service: None)
    original = exchange.apply
    def interrupt(domain, local, plan, **kwargs):
        if local == 'two':
            raise RuntimeError('worker interruption')
        return original(domain, local, plan, **kwargs)
    monkeypatch.setattr(exchange, 'apply', interrupt)
    with pytest.raises(RuntimeError):
        journal.execute(path)
    assert [row['state'] for row in journal.inspect(path)] == ['applied', 'ready']
    with pytest.raises(ValidationError):
        journal.execute(path)


@pytest.mark.parametrize('change', ['duplicate', 'token', 'plan', 'operation', 'undo'])
def test_invalid_journal_rejected(saved, change):
    path, original = saved
    payload = copy.deepcopy(original)
    if change == 'duplicate':
        payload['entries'].append(payload['entries'][0])
    elif change == 'token':
        payload['entries'][0]['token'] = 'invalid'
    elif change == 'plan':
        payload['entries'][0]['plan']['home'] = [True, 3]
    elif change == 'operation':
        payload['operation_id'] = '../elsewhere'
    else:
        payload['undo'] = 'false'
    with pytest.raises(ValidationError):
        journal.validate(payload)


def test_public_or_symlink_journal_rejected(saved):
    path, payload = saved
    path.chmod(0o644)
    with pytest.raises(ValidationError):
        journal.read(path)
    path.chmod(0o600)
    link = path.with_name('link.json')
    link.symlink_to(path)
    with pytest.raises(ValidationError):
        journal.read(link)


def test_existing_journal_is_not_overwritten(saved):
    path, payload = saved
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        journal.create(path, payload)
    assert path.read_bytes() == original


@pytest.mark.parametrize('interrupted', [False, True])
def test_real_supervised_journal_worker(saved, isolated_service, tmp_path, interrupted):
    from daemon import snapshot_mail_service as supervisor
    path, payload = saved
    service_name, operations = isolated_service
    operation = payload['operation_id']
    operations.append(operation)
    script = tmp_path / 'supervised-worker.py'
    repo = Path(__file__).parents[1]
    script.write_text(
        f'import sys\nsys.path.insert(0, {str(repo)!r})\n'
        'from shared.config import settings\n'
        'from daemon import snapshot_mail_journal as journal, snapshot_mail_exchange as exchange\n'
        f'settings.snapshot_private_dir = {settings.snapshot_private_dir!r}\n'
        f'settings.mail_restore_guard_dir = {settings.mail_restore_guard_dir!r}\n'
        f'settings.mail_base = {settings.mail_base!r}\n'
        + ('original = exchange.apply\n'
           'def interrupt(domain, local, plan, **kwargs):\n'
           ' if local == "two": raise SystemExit(91)\n'
           ' return original(domain, local, plan, **kwargs)\n'
           'exchange.apply = interrupt\n' if interrupted else '')
        + f'journal.execute({str(path)!r}, service={service_name!r})\n'
    )
    command = [str(repo / '.venv/bin/python'), str(script)]
    if interrupted:
        with pytest.raises(ValidationError, match='Mail switch failed'):
            supervisor.supervised_command(command, operation, service=service_name)
    else:
        supervisor.supervised_command(command, operation, service=service_name)
    expected = ['applied', 'ready'] if interrupted else ['applied', 'applied']
    assert [row['state'] for row in journal.inspect(path)] == expected
    assert supervisor.service_status(service_name)['ActiveState'] == 'active'
    with guard.owned_guards(payload['entries'], payload['restore_id']):
        pass
    observed = journal.recovery_state(path, service=service_name)
    assert observed['state'] == ('partial' if interrupted else 'applied')
    assert all(entry['token'] not in repr(observed) for entry in payload['entries'])
    before = path.read_bytes()
    rollback = journal.prepare_rollback(path, path.with_name('undo.json'), service=service_name)
    undo_payload = journal.read(rollback)
    assert len(undo_payload['entries']) == (1 if interrupted else 2)
    assert undo_payload['operation_id'] != operation
    assert undo_payload['undo'] is True
    assert path.read_bytes() == before
    # A failed transient unit deliberately remains until terminal-state
    # inspection. Retire only this disposable test unit before the undo worker.
    if interrupted:
        import subprocess
        subprocess.run(['/usr/bin/systemctl', 'reset-failed', supervisor.switch_unit(service_name)],
                       capture_output=True, check=True, timeout=20)
    undo_script = tmp_path / 'undo-worker.py'
    undo_script.write_text(
        f'import sys\nsys.path.insert(0, {str(repo)!r})\n'
        'from shared.config import settings\n'
        'from daemon import snapshot_mail_journal as journal\n'
        f'settings.snapshot_private_dir = {settings.snapshot_private_dir!r}\n'
        f'settings.mail_restore_guard_dir = {settings.mail_restore_guard_dir!r}\n'
        f'settings.mail_base = {settings.mail_base!r}\n'
        f'journal.execute({str(rollback)!r}, service={service_name!r})\n')
    operations.append(undo_payload['operation_id'])
    supervisor.supervised_command([str(repo / '.venv/bin/python'), str(undo_script)],
                                 undo_payload['operation_id'], service=service_name)
    assert [row['state'] for row in journal.inspect(path)] == ['ready', 'ready']
    assert journal.recovery_state(rollback, service=service_name)['state'] == 'applied'
    with guard.owned_guards(payload['entries'], payload['restore_id']):
        pass


def test_launch_uses_persisted_operation_and_private_worker(saved, monkeypatch):
    path, payload = saved
    captured = []
    monkeypatch.setattr(journal, 'supervised_command', lambda command, operation: captured.append((command, operation)))
    journal.launch(path)
    command, operation = captured[0]
    assert operation == payload['operation_id']
    assert command[1:] == ['-m', 'daemon.snapshot_mail_journal', str(path)]
    assert all(entry['token'] not in repr(command) for entry in payload['entries'])


def test_running_worker_prevents_directory_inspection_and_rollback(saved, monkeypatch):
    from daemon import snapshot_mail_service as supervisor
    path, payload = saved
    monkeypatch.setattr(supervisor, 'inspect_switch', lambda *a, **kw: {'state': 'running'})
    def forbidden(*args, **kwargs):
        raise AssertionError('Cannot inspect changing mail trees')
    monkeypatch.setattr(journal, 'inspect', forbidden)
    assert journal.recovery_state(path)['state'] == 'waiting'
    target = path.with_name('undo.json')
    with pytest.raises(ValidationError, match='not ready'):
        journal.prepare_rollback(path, target)
    assert not target.exists()


def test_completed_switch_requires_mail_service_recovery_before_rollback(saved, monkeypatch):
    from daemon import snapshot_mail_service as supervisor
    path, payload = saved
    monkeypatch.setattr(journal, 'require_stopped', lambda service: None)
    journal.execute(path)
    monkeypatch.setattr(supervisor, 'inspect_switch', lambda *a, **kw: {'state': 'missing'})
    monkeypatch.setattr(supervisor, 'service_status', lambda *a: {'ActiveState': 'failed'})
    state = journal.recovery_state(path)
    assert state['state'] == 'service_recovery_required' and state['outcome'] == 'applied'
    with pytest.raises(ValidationError, match='not ready'):
        journal.prepare_rollback(path, path.with_name('undo.json'))
