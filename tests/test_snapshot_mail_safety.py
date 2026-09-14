import json
from pathlib import Path

import pytest

from daemon import snapshot_mail_journal as journal, snapshot_mail_restore as restore
from daemon import snapshot_mail_guard as guard, snapshot_mail_service as supervisor, snapshot_storage as storage
from shared.config import settings
from shared.db import write_session
from shared.models import Account, MailDomain
from shared.validation import ValidationError
from tests.test_snapshot_mail_journal import saved
from tests.test_snapshot_storage import repo


def test_displaced_mail_is_encrypted_and_guards_remain(isolated_db, saved, repo, tmp_path, monkeypatch):
    path, payload = saved
    with write_session() as session:
        account = Account(username='alpha', status='active', uid=65534, gid=65534)
        session.add(account); session.flush()
        session.add(MailDomain(account_id=account.id, domain='example.test'))
    for entry in payload['entries']:
        home = Path(settings.mail_base) / entry['domain'] / entry['local_part']
        (home / 'Maildir/cur/original:2,S').write_bytes(b'original mail before restore')
        (home / entry['plan']['prepared'] / 'cur/restored:2,S').write_bytes(b'restored mail')
    storage.initialize(repo)
    monkeypatch.setattr(supervisor, 'inspect_switch', lambda *a, **kw: {'state': 'missing'})
    monkeypatch.setattr(supervisor, 'service_status', lambda *a: {
        'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'running', 'ControlPID': '0'})
    with pytest.raises(ValidationError, match='must finish'):
        restore.backup_displaced(account, repo, path, 1)
    assert not path.with_name('mail-safety.json').exists()
    monkeypatch.setattr(journal, 'require_stopped', lambda *a: None)
    journal.execute(path)
    result = restore.backup_displaced(account, repo, path, 1)
    data = storage.restore_to(repo, account.id, result['snapshot_id'], str(tmp_path / 'recovered'))
    for entry in result['mailboxes']:
        assert (data / entry['path'].lstrip('/') / 'cur/original:2,S').read_bytes() == b'original mail before restore'
        assert not (data / entry['path'].lstrip('/') / 'cur/restored:2,S').exists()
        live = Path(settings.mail_base) / entry['domain'] / entry['local_part'] / 'Maildir'
        assert (live / 'cur/restored:2,S').read_bytes() == b'restored mail'
    manifest = data / str(path.with_name('mail-safety.json')).lstrip('/')
    assert json.loads(manifest.read_text())['account_id'] == account.id
    assert all(entry['token'] not in manifest.read_text() for entry in payload['entries'])
    assert json.loads(path.with_name('mail-safety-result.json').read_text())['snapshot_id'] == result['snapshot_id']
    with guard.owned_guards(payload['entries'], 1):
        pass
    with pytest.raises(FileExistsError):
        restore.backup_displaced(account, repo, path, 1)
