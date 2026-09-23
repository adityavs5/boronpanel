import sys
"""Real systemd worker execution against isolated SQL, files and a test service."""
from pathlib import Path

import pytest

from daemon import snapshot_jobs as jobs, snapshot_mail_guard as guard
from daemon import snapshot_mail_routing as routing, snapshot_mail_routing_journal as journal
from daemon import snapshot_mail_routing_recovery as recovery, snapshot_mail_service as service
from shared.config import settings
from shared.validation import ValidationError
from tests.test_snapshot_databases import sql_server, sql
from tests.test_snapshot_mail_metadata import mail_database
from tests.test_snapshot_mail_routing_sql import routing_account
from tests.test_snapshot_mail_routing_recovery import prepared, repository
from tests.test_snapshot_mail_service import isolated_service


@pytest.mark.parametrize('interrupt', [False, True])
def test_real_systemd_routing_restore_and_interrupted_rollback(prepared, isolated_service, tmp_path, monkeypatch, interrupt):
    account, _, plan, script = prepared
    name, operations = isolated_service
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(tmp_path / 'guards'))
    repo = repository(tmp_path)
    safety = recovery.save_previous(repo, account, 121, plan['previous'])
    path = journal.create(repo, account, 121, plan, safety['snapshot_id'])
    journal.acquire_guards(account, path)
    source = Path(__file__).resolve().parents[1]
    worker = tmp_path / 'routing-worker.py'
    # Only fixture paths and the synthetic isolated SQL credential enter this
    # worker. It cannot reach the live panel database, locks or mail directory.
    worker.write_text(
        f'import sys, os, signal\nsys.path.insert(0, {str(source)!r})\n'
        'from shared.config import settings\n'
        + ''.join(f'settings.{key} = {getattr(settings, key)!r}\n' for key in (
            'db_path', 'snapshot_private_dir', 'mail_restore_guard_dir', 'mail_base',
            'mariadb_socket', 'mariadb_admin_user'))
        + "settings.secrets = {'MARIADB_DAEMON_PASSWORD': 'test-only-service-password'}\n"
        'from daemon import snapshot_mail_routing_journal as journal, snapshot_jobs as jobs\n'
        'from shared.models import Account\n'
        'write = journal._write\n'
        'def checkpoint(path, payload, **kwargs):\n'
        ' if payload["phase"] == sys.argv[2]: os.kill(os.getpid(), signal.SIGKILL)\n'
        ' return write(path, payload, **kwargs)\n'
        'journal._write = checkpoint\n'
        f'journal.execute(jobs._row(Account, {account.id}), sys.argv[1], service={name!r})\n'
    )
    worker.chmod(0o600)

    def run(path, crash_phase='none'):
        payload = journal.read(account, path)
        operations.append(payload['operation_id'])
        before = service.service_status(name)
        command = [sys.executable, str(worker), str(path), crash_phase]
        if crash_phase != 'none':
            with pytest.raises(ValidationError, match='Mail switch failed'):
                service.supervised_command(command, payload['operation_id'], service=name)
        else:
            service.supervised_command(command, payload['operation_id'], service=name)
        after = service.service_status(name)
        assert after['ActiveState'] == 'active' and after['SubState'] == 'running'
        assert after['MainPID'] != before['MainPID']
        assert service.inspect_switch(payload['operation_id'], service=name)['state'] in ('terminal', 'missing')
        with guard.owned_guards(payload['guards'], 121): pass

    run(path, 'verified' if interrupt else 'none')
    assert routing.capture(account) == plan['desired_routing']
    assert b'vacation' in script.read_bytes()
    if not interrupt:
        result = journal.finalize(repo, account, path, service=name)
        assert result['guards_released'] and not result['rolled_back']
        return

    assert journal.recovery_state(account, path, service=name)['phase'] == 'applying_scripts'
    reverse = journal.prepare_rollback(repo, account, path, service=name)
    original, rollback = journal.read(account, path), journal.read(account, reverse)
    service.retire_switch(original['operation_id'], service=name)
    run(reverse, 'sql_applied')
    assert journal.recovery_state(account, reverse, service=name)['phase'] == 'applying_sql'
    assert routing.capture(account) == plan['previous']['routing']
    assert b'vacation' in script.read_bytes()  # SQL recovered, script not yet switched.
    service.retire_switch(rollback['operation_id'], service=name)
    run(reverse)
    result = journal.finalize(repo, account, reverse, service=name)
    assert result['rolled_back'] and result['guards_released']
    assert routing.capture(account) == plan['previous']['routing']
    assert script.read_bytes() == b'# custom previous script\r\nkeep;\r\n'
    assert journal.read(account, reverse)['safety_snapshot_id'] == rollback['safety_snapshot_id']
    assert recovery.load_previous(repo, account, safety['snapshot_id'], 121) == plan['previous']
