import tomllib
import pytest
from sqlalchemy import select
from daemon import panel_jobs as jobs
from daemon.configtx import StepResult
from shared.config import settings
from shared.db import write_session
from shared.models import PanelConfigJob
from shared.validation import ValidationError


@pytest.fixture
def setup(isolated_db, tmp_path, monkeypatch):
    path = tmp_path / 'boron.toml'
    path.write_text('api_bind_port=9443\n')
    monkeypatch.setattr(jobs, 'CONFIG_PATH', path)
    monkeypatch.setattr(settings, 'backup_dir', str(tmp_path / 'backups'))
    monkeypatch.setattr(settings, 'api_bind_port', 9443)
    monkeypatch.setattr(settings, 'api_customer_port', None)
    monkeypatch.setattr(jobs, '_launch', lambda job_id: None)
    monkeypatch.setattr(jobs.panel_config, 'check_available', lambda *args: None)
    return path


def enqueue():
    return jobs.start({'admin_port': 2222, 'customer_port': 3333, 'confirm': True, 'initiated_by': 'admin'})


def test_queue_returns_pending_and_rejects_concurrent_changes(setup):
    job = enqueue()
    assert job['status'] == 'pending'
    assert setup.read_text() == 'api_bind_port=9443\n'
    with pytest.raises(ValidationError, match='already running'):
        enqueue()


def test_queue_requires_confirmation(setup):
    with pytest.raises(ValidationError, match='Confirm'):
        jobs.start({'admin_port': 2222, 'customer_port': 3333})


def test_execute_journals_private_config_and_records_result(setup, monkeypatch):
    job = enqueue()
    calls = []
    def apply(*args, **kwargs):
        calls.append(args)
        original = jobs._original_path(job['id'])
        assert original.read_text() == setup.read_text()
        assert original.stat().st_mode & 0o777 == 0o600
        return {'status': 'completed'}
    monkeypatch.setattr(jobs.panel_config, 'apply_ports', apply)
    jobs.execute(job['id'])
    jobs.execute(job['id'])
    assert calls == [(2222, 3333)]
    saved = jobs.status({})['jobs'][0]
    assert saved['status'] == 'completed' and saved['completed_at']


def test_worker_failure_is_persisted(setup, monkeypatch):
    job = enqueue()
    def fail(*args, **kwargs):
        raise RuntimeError('Previous listeners restored')
    monkeypatch.setattr(jobs.panel_config, 'apply_ports', fail)
    jobs.execute(job['id'])
    assert jobs.status({})['jobs'][0]['error'] == 'Previous listeners restored'


def running():
    job = enqueue()
    with write_session() as session:
        row = session.get(PanelConfigJob, job['id'])
        row.status = 'running'
    return job['id']


def test_recovery_recognizes_verified_new_listeners(setup, monkeypatch):
    job_id = running()
    setup.write_text('api_bind_port=2222\napi_customer_port=3333\n')
    monkeypatch.setattr(jobs.panel_config, 'verify_listeners', lambda *args: StepResult(True))
    jobs.recover_jobs()
    assert jobs.status({})['jobs'][0]['status'] == 'completed'
    assert (settings.api_bind_port, settings.api_customer_port) == (2222, 3333)


def test_interrupted_change_restores_journal_when_new_listeners_fail(setup, monkeypatch):
    job_id = running()
    jobs._original_path(job_id).write_text(setup.read_text())
    setup.write_text('api_bind_port=2222\napi_customer_port=3333\n')
    calls = []
    monkeypatch.setattr(jobs.panel_config, 'restart_api', lambda: calls.append('restart') or StepResult(True))
    monkeypatch.setattr(jobs.panel_config, 'verify_listeners', lambda host, ports, cert: StepResult(ports == (9443, 9443)))
    jobs.recover_jobs()
    assert tomllib.loads(setup.read_text()) == {'api_bind_port': 9443}
    assert calls == ['restart']
    assert 'previous listeners restored' in jobs.status({})['jobs'][0]['error']


def test_interruption_before_journal_does_not_restart(setup, monkeypatch):
    running()
    def forbidden():
        pytest.fail('No mutation started; do not restart')
    monkeypatch.setattr(jobs.panel_config, 'restart_api', forbidden)
    jobs.recover_jobs()
    assert 'no listener change was started' in jobs.status({})['jobs'][0]['error']


def test_pending_job_resumes_on_startup(setup, monkeypatch):
    job = enqueue()
    calls = []
    monkeypatch.setattr(jobs, '_launch', calls.append)
    jobs.recover_jobs()
    assert calls == [job['id']]


@pytest.mark.parametrize('role,code', [('admin', 202), ('customer', 403)])
def test_api_restricts_port_changes_to_admin(setup, monkeypatch, role, code):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import panel_config as router
    from api.security import Identity, get_identity
    app = FastAPI()
    app.include_router(router.api_router)
    app.dependency_overrides[get_identity] = lambda: Identity(panel_user_id=1, username='test', role=role, account_id=None, auth_method='session')
    calls = []
    monkeypatch.setattr(router, 'call_daemon', lambda op, identity, **params: calls.append((op, params)) or {'status': 'pending'})
    with TestClient(app) as client:
        response = client.post('/api/v1/admin/panel-config/ports', json={'admin_port': 2222, 'customer_port': 3333, 'confirm': True})
        assert response.status_code == code
        assert bool(calls) == (role == 'admin')
        assert client.get('/api/v1/admin/panel-config').status_code == (200 if role == 'admin' else 403)
        if role == 'admin':
            assert client.post('/api/v1/admin/panel-config/ports', json={'admin_port': True, 'customer_port': 3333}).status_code == 422
