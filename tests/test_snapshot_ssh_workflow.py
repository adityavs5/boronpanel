"""Real SSH backup jobs and loopback-only notification delivery."""
import email
import hashlib
import hmac
import json
import os
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import select

from tests.test_snapshot_storage import repo, ssh_environment
from tests.test_snapshot_jobs import environment
from daemon import snapshot_jobs as jobs, snapshot_storage as storage, snapshot_restores as restores
from daemon import notifications, webhooks
from shared.db import write_session
from shared.models import (Account, AccountNotificationPrefs, NotificationSettings,
                           SnapshotDestination, SnapshotRun, SnapshotRestore)


@pytest.fixture
def receivers(monkeypatch):
    mail, hooks = [], []
    class SMTP(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b'220 qa SMTP\r\n')
            while line := self.rfile.readline():
                verb = line.split(None, 1)[0].upper()
                if verb in (b'EHLO', b'HELO'):
                    self.wfile.write(b'250 qa\r\n')
                elif verb == b'DATA':
                    self.wfile.write(b'354 Send message\r\n')
                    lines = []
                    while (line := self.rfile.readline()) != b'.\r\n':
                        if not line:
                            return
                        lines.append(line[1:] if line.startswith(b'..') else line)
                    mail.append(email.message_from_bytes(b''.join(lines)))
                    self.wfile.write(b'250 Stored\r\n')
                elif verb == b'QUIT':
                    self.wfile.write(b'221 Bye\r\n')
                    return
                else:
                    self.wfile.write(b'250 OK\r\n')
    class Hook(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            hooks.append((body, self.headers['X-Boron-Signature']))
            self.send_response(200); self.end_headers()
        def log_message(self, *args): pass
    smtp = socketserver.ThreadingTCPServer(('127.0.0.1', 0), SMTP)
    http = ThreadingHTTPServer(('127.0.0.1', 0), Hook)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (smtp, http)]
    for thread in threads: thread.start()
    monkeypatch.setattr(notifications, 'SMTP_HOST', '127.0.0.1')
    monkeypatch.setattr(notifications, 'SMTP_PORT', smtp.server_address[1])
    url = f'http://backup-receiver.example.test:{http.server_address[1]}/backup'
    # Only this disposable receiver is allowed in this test. Production's
    # public-IP gate remains covered separately; the HTTP transport is real.
    def loopback_only(value):
        assert value == url
        return '127.0.0.1'
    monkeypatch.setattr(webhooks, '_assert_public_destination', loopback_only)
    monkeypatch.setattr(webhooks._executor, 'submit', lambda fn, ident: fn(ident))
    try:
        yield mail, hooks, url
    finally:
        for server in (smtp, http):
            server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=5)


def test_ssh_job_filters_incremental_restore_and_notifications(environment, ssh_environment, receivers):
    root, queued = environment
    spec, authorized = ssh_environment
    mail, hooks, hook_url = receivers
    host_key = Path(spec.ssh_known_hosts_file).read_text().split(' ', 1)[1].strip()
    dest = jobs.create_destination(dict(name='SSH workflow', kind='ssh', path=str(root/'job-repository'),
        ssh_host=spec.ssh_host, ssh_port=spec.ssh_port, ssh_user=spec.ssh_user, ssh_host_key=host_key))
    assert dest['status'] == 'draft' and dest['ssh_public_key'].startswith('ssh-ed25519 ')
    with authorized.open('a') as handle:
        handle.write(dest['ssh_public_key']+'\n')
    jobs.initialize_destination({'id': dest['id']})
    assert jobs._row(SnapshotDestination, dest['id']).status == 'ready'
    home = root/'home/alpha'
    (home/'site').mkdir()
    content = os.urandom(2*1024*1024)
    (home/'site/payload.bin').write_bytes(content)
    (home/'site/cache.tmp').write_text('excluded cache')
    (home/'outside.txt').write_text('outside include filter')
    for path in [home, *home.rglob('*')]:
        os.chown(path, 2000, 2000)
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == 'alpha'))
        session.add(NotificationSettings(id=1, sender_address='panel@example.test',
                                         events={'backup.completed': True, 'backup.failed': True}))
        session.add(AccountNotificationPrefs(account_id=account.id, customer_email='owner@example.test', events={}))
    webhooks.create_webhook(dict(url=hook_url, secret='qa-only-signing-secret',
                                 events=['backup.completed', 'backup.failed']))
    policy = jobs.save_policy(dict(name='SSH selected sites', destination_id=dest['id'],
        components=['files'], accounts=['alpha','bravo'], excluded_accounts=['bravo'],
        include_paths=['site'], exclude_patterns=['*.tmp'], frequency='manual',
        retention_count=3, notification_channels=['email','webhook']))
    def execute():
        ids = jobs.queue_policy({'id': policy['id']})['run_ids']
        assert len(ids) == 1
        jobs.execute_run(ids[0])
        row = jobs._row(SnapshotRun, ids[0])
        assert row.status == 'completed', row.error
        assert row.notification_results == {'email': 'dispatched', 'webhook': 'dispatched'}
        return row
    first = execute()
    second = execute()
    assert second.summary['data_added'] < first.summary['data_added']/10
    assert second.summary['files_unmodified'] >= 1
    remote = jobs.repository(jobs._row(SnapshotDestination, dest['id']))
    restored = storage.restore_to(remote, first.account_id, first.snapshot_id, str(root/'verified-ssh'))
    archived = restored / str(home).lstrip('/')
    assert (archived/'site/payload.bin').read_bytes() == content
    assert not (archived/'site/cache.tmp').exists()
    assert not (archived/'outside.txt').exists()
    (home/'site/payload.bin').write_bytes(b'new content')
    request = restores.trigger(dict(username='alpha', run_id=first.id, confirmation='alpha', paths=['site/payload.bin']))
    restores.execute(request['id'])
    result = jobs._row(SnapshotRestore, request['id'])
    assert result.status == 'completed', result.error
    assert (home/'site/payload.bin').read_bytes() == content
    assert (home/'outside.txt').read_text() == 'outside include filter'
    assert result.safety_snapshot_id
    assert jobs.runs({'username':'bravo'})['runs'] == []
    assert len(mail) == len(hooks) == 2
    assert all(message['To'] == 'owner@example.test' for message in mail)
    for body, signature in hooks:
        assert hmac.compare_digest(signature, 'sha256='+hmac.new(b'qa-only-signing-secret',body,hashlib.sha256).hexdigest())
        assert json.loads(body)['event'] == 'backup.completed'
    jobs.execute_run(second.id)
    assert len(mail) == len(hooks) == 2
    jobs.save_policy(dict(id=policy['id'], name='SSH selected sites', destination_id=dest['id'],
        components=['files'], accounts=['alpha'], include_paths=['missing-site'],
        frequency='manual', notification_channels=['email','webhook']))
    failed_id = jobs.queue_policy({'id': policy['id']})['run_ids'][0]
    jobs.execute_run(failed_id)
    failed = jobs._row(SnapshotRun, failed_id)
    assert failed.status == 'failed'
    assert failed.notification_results == {'email': 'dispatched', 'webhook': 'dispatched'}
    assert len(mail) == len(hooks) == 3
    assert 'failed' in mail[-1].get_payload().lower()
    body, signature = hooks[-1]
    assert json.loads(body)['event'] == 'backup.failed'
    assert hmac.compare_digest(signature, 'sha256='+hmac.new(b'qa-only-signing-secret',body,hashlib.sha256).hexdigest())
    jobs.execute_run(failed_id)
    assert len(mail) == len(hooks) == 3
