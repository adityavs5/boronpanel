"""Regression cases for the September repository security scan boundaries."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest


def test_systemd_account_commands_bound_output_and_stop_service(monkeypatch):
    from daemon import procutil
    stopped = []
    def bounded(args, **kwargs):
        assert kwargs['limit'] == procutil.MAX_TENANT_OUTPUT
        assert '--property=RuntimeMaxSec=3.0s' in args
        raise RuntimeError('capture limit')
    monkeypatch.setattr(procutil, '_bounded_run', bounded)
    monkeypatch.setattr(procutil, '_stop_account_unit', stopped.append)
    args = ['/usr/bin/systemd-run', '--unit=boron-account-2000-test-ab12', '--', '/bin/true']
    with pytest.raises(RuntimeError, match='capture limit'):
        procutil.run(args, timeout=3)
    assert stopped == ['boron-account-2000-test-ab12.service']
    assert not any('RuntimeMaxSec' in arg for arg in args)
    assert procutil._account_unit(['/usr/bin/systemd-run', '--', '--unit=boron-account-2000-test']) is None


def test_tenant_fifo_reads_reject_without_waiting(tmp_path, monkeypatch):
    from daemon import terminal, fileauth, logs
    from shared.config import settings
    home = tmp_path / 'home' / 'alice'; home.mkdir(parents=True)
    protected = home / 'private'; protected.mkdir()
    os.mkfifo(protected / '.htpasswd')
    with pytest.raises(Exception):
        fileauth._read_htpasswd_beneath(str(home), 'private')
    ssh = home / '.ssh'; ssh.mkdir(); os.mkfifo(ssh / 'authorized_keys')
    fd = os.open(ssh, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(Exception):
            terminal._read_lines(fd)
    finally:
        os.close(fd)
    monkeypatch.setattr(settings, 'home_base', str(tmp_path / 'home'))
    (home / 'logs').mkdir(); os.mkfifo(home / 'logs' / 'access.log')
    with pytest.raises(Exception):
        logs._safe_read_segment('alice', 'access.log', max_bytes=1024)


def test_terminal_lock_is_private_and_nonblocking(tmp_path, monkeypatch):
    import fcntl
    from daemon import terminal
    from shared.config import settings
    monkeypatch.setattr(settings, 'snapshot_private_dir', str(tmp_path / 'private'))
    fd = terminal._locked('alice')
    try:
        with pytest.raises(BlockingIOError):
            terminal._locked('alice')
        assert (tmp_path / 'private').stat().st_mode & 0o077 == 0
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)
    fd = terminal._locked('alice'); os.close(fd)


def test_portable_archive_worker_cannot_follow_foreign_source(tmp_path):
    home = tmp_path / 'home'; home.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'secret').write_text('foreign-canary')
    (home / 'swapped').symlink_to(outside, target_is_directory=True)
    work = tmp_path / 'work'; work.mkdir()
    artifact = work / 'account.tar'
    payload = dict(work=str(work), artifact=str(artifact), paths=[str(home / 'swapped' / 'secret')],
                   roots=[str(home)], home=str(home), mail=str(tmp_path / 'mail'),
                   options=dict(mode='archive', components=['files']), manifest={'format_version': 1})
    worker = Path(__file__).resolve().parents[1] / 'daemon/snapshot_archive_worker.py'
    result = subprocess.run([sys.executable, '-I', str(worker)], input=json.dumps(payload),
                            text=True, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert not artifact.exists() or b'foreign-canary' not in artifact.read_bytes()
    (home / 'ok').write_text('owned-content')
    payload['paths'] = [str(home / 'ok')]
    result = subprocess.run([sys.executable, '-I', str(worker)], input=json.dumps(payload),
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    with tarfile.open(artifact) as archive:
        assert archive.extractfile('account/home/ok').read() == b'owned-content'
        assert json.load(archive.extractfile('account/manifest.json'))['inventory'][0]['size_bytes'] == 13


def test_landlock_rejects_symlink_policy_root(tmp_path):
    from daemon import landlock_exec as ll
    real = tmp_path / 'real'; real.mkdir()
    link = tmp_path / 'link'; link.symlink_to(real, target_is_directory=True)
    with pytest.raises((ValueError, NotADirectoryError)):
        ll._add_rule(None, -1, [], str(link), ll.READ_TREE)
    with pytest.raises((ValueError, NotADirectoryError)):
        ll._add_rule(None, -1, [], str(link / 'child'), ll.READ_TREE)


def test_mail_import_foreign_domain_rejected_before_any_effect(isolated_db, monkeypatch, tmp_path):
    from daemon import cpanel_import as ci, directadmin_restore as da
    from shared.db import write_session
    from shared.models import Account, Domain, MailDomain
    from shared.validation import ValidationError
    with write_session() as session:
        alice = Account(username='alice', uid=2000, gid=2000, status='active')
        bob = Account(username='bob', uid=2001, gid=2001, status='active')
        session.add_all([alice, bob]); session.flush()
        session.add(Domain(account_id=bob.id, domain='foreign.example', kind='primary', docroot='/home/bob/public_html'))
        session.add(MailDomain(account_id=bob.id, domain='foreign.example'))
    monkeypatch.setattr(ci.handlers_mail, 'ensure_mail_domain', lambda *_: pytest.fail('foreign mail provisioning'))
    monkeypatch.setattr(da.handlers_mail, 'create_mailbox', lambda *_: pytest.fail('foreign mailbox mutation'))
    with pytest.raises(ValidationError, match='another account'):
        ci._prepare_import_mail_domain('alice', 'foreign.example')
    with pytest.raises(ValidationError, match='another account'):
        da.restore_mail(tmp_path, {'domain': 'foreign.example', 'local_part': 'sales', 'password_hash': '{CRYPT}$6$salt$' + 'a'*86, 'quota_mb': 1}, username='alice')
    with write_session() as session, pytest.raises(Exception):
        ci._preflight_domain_ownership(session, ['foreign.example'])


def test_upload_admission_precedes_body_read_and_bounds_chunked(monkeypatch):
    from fastapi import FastAPI, File, UploadFile
    from api.upload_guard import BoundedUploadRoute
    from api.security import get_identity, Identity
    from shared.config import settings
    app = FastAPI(); app.router.route_class = BoundedUploadRoute
    @app.post('/branding/upload')
    async def upload(file: UploadFile = File(...)):
        return {'size': len(await file.read())}
    monkeypatch.setattr(settings, 'branding_max_upload_bytes', 32)
    async def send_request(chunks, headers=None):
        consumed = []; sent = []
        async def receive():
            consumed.append(True)
            return {'type': 'http.request', 'body': chunks.pop(0), 'more_body': bool(chunks)}
        async def send(message): sent.append(message)
        await app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                   'method': 'POST', 'scheme': 'https', 'path': '/branding/upload', 'raw_path': b'/branding/upload',
                   'query_string': b'', 'headers': headers or [], 'server': ('panel.example', 443),
                   'client': ('127.0.0.1', 1234)}, receive, send)
        return sent[0]['status'], consumed
    status, consumed = asyncio.run(send_request([b'not parsed']))
    assert status == 401 and not consumed
    app.dependency_overrides[get_identity] = lambda: Identity(panel_user_id=1, username='admin', role='admin', account_id=None, auth_method='session')
    headers = [(b'content-type', b'multipart/form-data; boundary=test')]
    prefix = b'--test\r\nContent-Disposition: form-data; name="file"; filename="test.bin"\r\n\r\n'
    status, consumed = asyncio.run(send_request([prefix, b'x' * (64 * 1024 + 33), b'\r\n--test--\r\n'], headers))
    assert status == 413
    status, _ = asyncio.run(send_request([prefix + b'ok\r\n--test--\r\n'], headers))
    assert status == 200
