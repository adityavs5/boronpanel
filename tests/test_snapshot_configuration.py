import json
from pathlib import Path

import pytest
from sqlalchemy import select

from tests.test_snapshot_jobs import environment, make_destination
from tests.test_cron import fake_crontab
from daemon import cron, snapshot_jobs as jobs, snapshot_storage as storage, snapshot_restores as restores
from daemon import handlers_cron
from shared.db import write_session
from shared.models import Account, SnapshotDestination, SnapshotRun, SnapshotRestore
from shared.validation import ValidationError


@pytest.fixture
def configuration(environment, fake_crontab):
    root, _ = environment
    fake_crontab['alpha'] = 'MAILTO=""\n# manual\n0 3 * * * /bin/true\n'
    dest = make_destination(root)
    policy = jobs.save_policy(dict(name='Configuration', destination_id=dest['id'],
        accounts=['alpha'], components=['config'], frequency='manual'))
    ident = jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    jobs.execute_run(ident)
    assert jobs._row(SnapshotRun,ident).status=='completed'
    return root, ident, dest, fake_crontab


def test_real_encrypted_configuration_restore_and_undo(configuration):
    root, ident, dest, state = configuration
    original = state['alpha']
    state['alpha'] = 'MAILTO=current@example.test\n@hourly /bin/false\n'
    before = state['alpha']
    state['bravo'] = '0 4 * * * /bin/true\n'
    assert restores.configuration_options(dict(username='alpha',run_id=ident))['cron_available']
    request = restores.trigger(dict(username='alpha',run_id=ident,confirmation='alpha',
                                    kind='config',config_sections=['cron']))
    restores.execute(request['id'])
    row = jobs._row(SnapshotRestore,request['id'])
    assert row.status=='completed',row.error
    assert state['alpha']==original and state['bravo']=='0 4 * * * /bin/true\n'
    assert row.safety_snapshot_id
    undo = restores.undo(dict(username='alpha',restore_id=row.id,confirmation='alpha'))
    restores.execute(undo['id'])
    assert jobs._row(SnapshotRestore,undo['id']).status=='completed'
    assert state['alpha']==before
    redo = restores.undo(dict(username='alpha',restore_id=undo['id'],confirmation='alpha'))
    restores.execute(redo['id'])
    assert jobs._row(SnapshotRestore,redo['id']).status=='completed'
    assert state['alpha']==original


def test_configuration_scope_and_selection_rejected(configuration):
    _, ident, _, state = configuration
    original = dict(state)
    with pytest.raises(ValidationError,match='not found'):
        restores.configuration_options(dict(username='bravo',run_id=ident))
    with pytest.raises(ValidationError,match='scheduled tasks'):
        restores.trigger(dict(username='alpha',run_id=ident,confirmation='alpha',kind='config',config_sections=['unsupported']))
    with pytest.raises(ValidationError,match='username'):
        restores.trigger(dict(username='alpha',run_id=ident,confirmation='wrong',kind='config',config_sections=['cron']))
    assert state==original


def test_failed_install_keeps_encrypted_previous_configuration(configuration, monkeypatch):
    _, ident, dest, state = configuration
    state['alpha']='MAILTO=keep@example.test\n0 5 * * * /bin/true\n'
    before=state['alpha']
    request=restores.trigger(dict(username='alpha',run_id=ident,confirmation='alpha',kind='config',config_sections=['cron']))
    def fail(*args):raise cron.CronError('private configuration must not leak')
    monkeypatch.setattr(cron,'restore_configuration',fail)
    restores.execute(request['id'])
    row=jobs._row(SnapshotRestore,request['id'])
    assert row.status=='failed' and row.safety_snapshot_id
    assert 'private configuration must not leak' not in row.error
    assert state['alpha']==before


def test_cron_edit_is_blocked_while_account_restore_holds_lock(configuration):
    _, _, _, state=configuration
    with write_session() as session:
        account=session.scalar(select(Account).where(Account.username=='alpha'))
    with jobs.lock(f'account-{account.id}'):
        with pytest.raises(ValidationError,match='in progress'):
            handlers_cron.set_cron_mailto(dict(username='alpha',mailto='changed@example.test'))
    assert 'changed' not in state['alpha']


def test_php_metadata_decrypts_owned_snapshot_and_bound_safety_copy(configuration):
    from daemon import snapshot_configuration, snapshot_php
    from daemon.snapshot_db_metadata import write_metadata
    root, ident, dest, _ = configuration
    run = jobs._row(SnapshotRun, ident)
    account = jobs._row(Account, run.account_id)
    repo = jobs.repository(jobs._row(SnapshotDestination, dest['id']))
    expected = snapshot_php.validate_for_restore(account, snapshot_php.capture(account))
    assert snapshot_configuration.load_php(repo, account, run.snapshot_id) == expected
    with write_session() as session:
        foreign = session.scalar(select(Account).where(Account.username == 'bravo'))
    with pytest.raises(ValidationError):
        snapshot_configuration.load_php(repo, foreign, run.snapshot_id)
    work = jobs.private_directory('restores', 'restore-987')
    path = work/'config-recovery.json'
    payload = dict(format=1, account_id=account.id, username=account.username,
                   restore_id=987, php_configuration=snapshot_php.capture(account))
    write_metadata(path, payload)
    safety = storage.backup(repo, account.id, [str(path)])['snapshot_id']
    assert snapshot_configuration.load_php(repo, account, safety, source_restore_id=987) == expected
    with pytest.raises(ValidationError, match='paths'):
        snapshot_configuration.load_php(repo, account, safety, source_restore_id=988)
    del payload['php_configuration']
    path.unlink()  # Replace only this test's create-once staging fixture.
    write_metadata(path, payload)
    older = storage.backup(repo, account.id, [str(path)])['snapshot_id']
    with pytest.raises(ValidationError, match='no complete PHP'):
        snapshot_configuration.load_php(repo, account, older, source_restore_id=987)


def test_php_worker_encrypts_previous_settings_before_apply(configuration, monkeypatch):
    from types import SimpleNamespace
    from daemon import snapshot_configuration, snapshot_php, ols, sysops
    root, ident, dest, _ = configuration
    run = jobs._row(SnapshotRun, ident)
    account = jobs._row(Account, run.account_id)
    repo = jobs.repository(jobs._row(SnapshotDestination, dest['id']))
    archived = snapshot_php.capture(account)
    with write_session() as session:
        session.get(Account, account.id).php_version = '8.2'
    before = snapshot_php.capture(account)
    events = []
    updates = {}
    def update(restore_id, **values):
        updates.update(values)
        events.append(('update', dict(values)))
    def refresh(current):
        assert updates.get('safety_snapshot_id')
        events.append(('runtime', current.php_version))
    monkeypatch.setattr(ols, 'refresh_vhost', refresh)
    monkeypatch.setattr(sysops, 'recycle_php_workers', lambda username: None)
    work = jobs.private_directory('restores', 'restore-991')
    snapshot_configuration.restore_php(991, account, SimpleNamespace(selection={}), repo,
                                       run.snapshot_id, work, update)
    assert snapshot_php.capture(account) == archived
    assert updates['status'] == 'completed' and updates['summary']['config_sections'] == ['php']
    previous = snapshot_configuration.load_php(repo, account, updates['safety_snapshot_id'], source_restore_id=991)
    assert previous == snapshot_php.validate_for_restore(account, before)
    assert events[0][0] == 'update' and events[1] == ('runtime', archived['default_version'])


def test_queued_php_restore_undo_and_redo(configuration, monkeypatch):
    from daemon import snapshot_php, ols, sysops
    _, ident, _, _ = configuration
    run = jobs._row(SnapshotRun, ident)
    account = jobs._row(Account, run.account_id)
    archived = snapshot_php.capture(account)
    with write_session() as session:
        session.get(Account, account.id).php_version = '8.2'
    before = snapshot_php.capture(account)
    monkeypatch.setattr(ols, 'refresh_vhost', lambda account: None)
    monkeypatch.setattr(sysops, 'recycle_php_workers', lambda username: None)
    options = restores.configuration_options(dict(username='alpha', run_id=ident))
    assert options['php_available'] and options['cron_available']
    assert options['php_default_version'] == archived['default_version']
    request = restores.trigger(dict(username='alpha', run_id=ident, confirmation='alpha',
                                    kind='config', config_sections=['php']))
    restores.execute(request['id'])
    row = jobs._row(SnapshotRestore, request['id'])
    assert row.status == 'completed', row.error
    assert row.selection['config_sections'] == ['php']
    assert snapshot_php.capture(account) == archived
    undo = restores.undo(dict(username='alpha', restore_id=row.id, confirmation='alpha'))
    restores.execute(undo['id'])
    undone = jobs._row(SnapshotRestore, undo['id'])
    assert undone.status == 'completed', undone.error
    assert snapshot_php.capture(account) == before
    redo = restores.undo(dict(username='alpha', restore_id=undone.id, confirmation='alpha'))
    restores.execute(redo['id'])
    redone = jobs._row(SnapshotRestore, redo['id'])
    assert redone.status == 'completed', redone.error
    assert snapshot_php.capture(account) == archived


@pytest.mark.parametrize('reconciliation_fails', [False, True])
def test_interrupted_php_restore_reconciles_committed_settings_and_retains_undo(configuration, monkeypatch, reconciliation_fails):
    from daemon import snapshot_php, ols, sysops
    _, ident, _, _ = configuration
    run = jobs._row(SnapshotRun, ident)
    account = jobs._row(Account, run.account_id)
    archived = snapshot_php.capture(account)
    with write_session() as session:
        session.get(Account, account.id).php_version = '8.2'
    before = snapshot_php.capture(account)
    def crash(account):
        raise SystemExit('simulated process loss after database commit')
    monkeypatch.setattr(ols, 'refresh_vhost', crash)
    request = restores.trigger(dict(username='alpha', run_id=ident, confirmation='alpha', kind='config', config_sections=['php']))
    with pytest.raises(SystemExit):
        restores.execute(request['id'])
    interrupted = jobs._row(SnapshotRestore, request['id'])
    assert interrupted.status == 'running' and interrupted.safety_snapshot_id
    assert snapshot_php.capture(account) == archived
    calls = []
    def reconcile(current):
        calls.append(current.php_version)
        if reconciliation_fails:
            raise RuntimeError('private failure details')
    monkeypatch.setattr(ols, 'refresh_vhost', reconcile)
    monkeypatch.setattr(sysops, 'recycle_php_workers', lambda username: None)
    restores.recover_restores()
    failed = jobs._row(SnapshotRestore, interrupted.id)
    assert failed.status == 'failed' and failed.safety_snapshot_id == interrupted.safety_snapshot_id
    assert calls == [archived['default_version']]
    assert ('could not be confirmed' in failed.error) == reconciliation_fails
    assert 'private failure details' not in failed.error
    monkeypatch.setattr(ols, 'refresh_vhost', lambda current: None)
    undo = restores.undo(dict(username='alpha', restore_id=failed.id, confirmation='alpha'))
    restores.execute(undo['id'])
    assert jobs._row(SnapshotRestore, undo['id']).status == 'completed'
    assert snapshot_php.capture(account) == before
