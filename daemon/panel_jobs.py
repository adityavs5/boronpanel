"""Persistent administrator listener-change jobs; API restart never owns the job."""
import logging
import os
from pathlib import Path
import threading
import tomllib

from sqlalchemy import select
from daemon import panel_config
from daemon.configtx import ConfigWriter, StepResult
from shared.config import CONFIG_PATH, Settings, settings
from shared.db import write_session
from shared.models import PanelConfigJob, utcnow
from shared.panel_ports import listener_ports
from shared.validation import ValidationError

logger = logging.getLogger('borond.panel_jobs')
_queue_lock = threading.Lock()
ACTIVE = ('pending', 'running')


def serialize(job):
    return {key: getattr(job, key) for key in ('id', 'status', 'admin_port', 'customer_port', 'initiated_by', 'error', 'result')} | {
        'created_at': job.created_at.isoformat(),
        'completed_at': job.completed_at.isoformat() if job.completed_at else None}


def status(params):
    with write_session() as session:
        jobs = session.scalars(select(PanelConfigJob).order_by(PanelConfigJob.id.desc()).limit(10)).all()
        admin, customer = listener_ports()
        return {'admin_port': admin, 'customer_port': customer, 'hostname': settings.panel_hostname,
                'telemetry': {'provider': 'sentry', 'enabled': bool(settings.sentry_dsn),
                              'environment': settings.telemetry_environment},
                'jobs': [serialize(job) for job in jobs]}


def _launch(job_id):
    # Give the initiating request time to finish before graceful API shutdown.
    timer = threading.Timer(2, execute, args=(job_id,))
    timer.daemon = True
    timer.start()


def start(params):
    ports = panel_config.validate_ports(params.get('admin_port'), params.get('customer_port'))
    if params.get('confirm') is not True:
        raise ValidationError('Confirm the new panel ports before applying them')
    with _queue_lock, write_session() as session:
        if session.scalar(select(PanelConfigJob.id).where(PanelConfigJob.status.in_(ACTIVE))):
            raise ValidationError('A panel configuration change is already running')
        panel_config.check_available(settings.api_bind_host, listener_ports(), ports)
        job = PanelConfigJob(admin_port=ports[0], customer_port=ports[1], initiated_by=params.get('initiated_by', 'admin'))
        session.add(job)
        session.flush()
        result = serialize(job)
    _launch(result['id'])
    return result


def _original_path(job_id):
    directory = Path(settings.backup_dir) / 'panel-config/jobs'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory / f'{job_id}.toml'


def _finish(job_id, result=None, error=None):
    with write_session() as session:
        job = session.get(PanelConfigJob, job_id)
        job.status = 'failed' if error else 'completed'
        job.error = error[:3000] if error else None
        job.result = result or {}
        job.completed_at = utcnow()


def execute(job_id):
    with _queue_lock:
        with write_session() as session:
            job = session.get(PanelConfigJob, job_id)
            if not job or job.status != 'pending':
                return
            ports = job.admin_port, job.customer_port
            job.status = 'running'
    try:
        original = _original_path(job_id)
        # Root-private recovery journal, not the API-readable SQLite database.
        fd = os.open(original, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            output.write(Path(CONFIG_PATH).read_text())
            output.flush()
            os.fsync(output.fileno())
        result = panel_config.apply_ports(*ports, config_path=CONFIG_PATH)
        _finish(job_id, result=result)
    except Exception as exc:
        logger.exception('Panel configuration job %s failed', job_id)
        _finish(job_id, error=str(exc))


def _recover_running(job):
    """Inspect actual listeners after daemon interruption, then restore if needed."""
    target = Path(CONFIG_PATH)
    config = Settings(**tomllib.loads(target.read_text()))
    actual = config.api_bind_port, config.api_customer_port if config.api_customer_port is not None else config.api_bind_port
    requested = job.admin_port, job.customer_port
    certificate = '/etc/boron/ssl/api/panel.crt'
    if actual == requested and panel_config.verify_listeners(config.api_bind_host, requested, certificate).ok:
        settings.api_bind_port, settings.api_customer_port = actual
        _finish(job.id, result={'status': 'completed', 'recovered': True})
        return
    original = _original_path(job.id)
    if not original.exists():
        _finish(job.id, error='Interrupted before saving configuration; no listener change was started.')
        return
    previous = original.read_text()
    old = Settings(**tomllib.loads(previous))
    old_ports = old.api_bind_port, old.api_customer_port if old.api_customer_port is not None else old.api_bind_port
    metadata = target.stat()
    def validate(path):
        Settings(**tomllib.loads(path.read_text()))
        os.chmod(path, metadata.st_mode & 0o777)
        os.chown(path, metadata.st_uid, metadata.st_gid)
        return StepResult(True)
    result = ConfigWriter(str(target), validate, panel_config.restart_api,
        lambda: panel_config.verify_listeners(old.api_bind_host, old_ports, certificate),
        str(original.parent), f'recovery-{job.id}').apply(previous)
    if result.ok:
        settings.api_bind_port, settings.api_customer_port = old.api_bind_port, old.api_customer_port
    _finish(job.id, error='Interrupted change: ' + ('previous listeners restored.' if result.ok else 'automatic recovery failed; administrator attention required.'))


def recover_jobs():
    with write_session() as session:
        jobs = session.scalars(select(PanelConfigJob).where(PanelConfigJob.status.in_(ACTIVE))).all()
        for job in jobs:
            session.expunge(job)
    for job in jobs:
        if job.status == 'pending':
            _launch(job.id)
        else:
            try:
                _recover_running(job)
            except Exception:
                logger.exception('Panel configuration recovery failed for %s', job.id)
                _finish(job.id, error='Interrupted change could not be recovered; administrator attention required.')
