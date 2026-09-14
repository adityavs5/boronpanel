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
    assert restore.safety_inventory(account, repo, result['snapshot_id'], 1) == result['mailboxes']
    with pytest.raises(ValidationError, match='does not match this job'):
        restore.safety_inventory(account, repo, result['snapshot_id'], 2)
    # Read the encrypted inventory, not the retained local journal/manifest.
    original_manifest = path.with_name('mail-safety.json').read_bytes()
    path.with_name('mail-safety.json').write_text('{}')
    assert restore.safety_inventory(account, repo, result['snapshot_id'], 1) == result['mailboxes']
    path.with_name('mail-safety.json').write_bytes(original_manifest)
    with write_session() as session:
        foreign = Account(username='foreign', status='active', uid=65533, gid=65533)
        session.add(foreign); session.flush()
        domain = session.query(MailDomain).filter_by(domain='example.test').one()
        domain.account_id = foreign.id
    with pytest.raises(ValidationError, match='no longer owned'):
        restore.safety_inventory(account, repo, result['snapshot_id'], 1)
    with write_session() as session:
        session.query(MailDomain).filter_by(domain='example.test').one().account_id = account.id
    assert not list((Path(settings.snapshot_private_dir) / 'mail-safety-inventory').iterdir())
    from daemon import mail
    monkeypatch.setattr(mail, 'list_mailboxes', lambda domain: [{'local_part': 'one'}, {'local_part': 'two'}])
    previous = restore.prepare_safety(account, repo, result['snapshot_id'], 1)
    for entry in previous['entries']:
        assert entry['action'] == 'existing'
        assert entry['metadata'] is None
        messages = list((Path(entry['prepared']) / 'cur').iterdir()) + list((Path(entry['prepared']) / 'new').iterdir())
        assert len(messages) == 1
        assert b'original mail before restore' in messages[0].read_bytes()
        live = Path(settings.mail_base) / entry['domain'] / entry['local_part'] / 'Maildir'
        assert (live / 'cur/restored:2,S').read_bytes() == b'restored mail'
    monkeypatch.setattr(mail, 'list_mailboxes', lambda domain: [])
    with pytest.raises(ValidationError, match='mailbox to still exist'):
        restore.prepare_safety(account, repo, result['snapshot_id'], 1)
    with guard.owned_guards(payload['entries'], 1):
        pass
    with pytest.raises(FileExistsError):
        restore.backup_displaced(account, repo, path, 1)
    # The repository commit survives a caller dying before its local receipt.
    path.with_name('mail-safety-result.json').unlink()
    recovered = restore.backup_displaced(account, repo, path, 1, recover=True)
    assert recovered['snapshot_id'] == result['snapshot_id']
    assert len(storage.snapshots(repo, account.id)) == 1
    assert restore.backup_displaced(account, repo, path, 1, recover=True) == recovered
    with guard.owned_guards(payload['entries'], 1):
        pass
    # An operation must identify exactly one archive; never choose arbitrarily.
    storage.backup(repo, account.id, [entry['path'] for entry in result['mailboxes']] + [str(path.with_name('mail-safety.json'))],
                   recovery_operation=payload['operation_id'])
    with pytest.raises(ValidationError, match='ambiguous'):
        restore.backup_displaced(account, repo, path, 1, recover=True)
    from daemon import mail
    import os
    monkeypatch.setattr(mail, 'list_mailboxes', lambda domain: [{'local_part': 'one'}, {'local_part': 'two'}])
    for entry in payload['entries']:
        live = Path(settings.mail_base) / entry['domain'] / entry['local_part'] / 'Maildir'
        os.chown(live, 150, 150)
        live.chmod(0o700)
    original_release = guard.release_batch
    def partial_release(entries, restore_id):
        first = entries[0]
        guard.release(first['domain'], first['local_part'], restore_id, first['token'])
        raise RuntimeError('caller interrupted during release')
    monkeypatch.setattr(guard, 'release_batch', partial_release)
    with pytest.raises(RuntimeError, match='interrupted'):
        restore.finalize(account, repo, path, 1)
    assert path.with_name('release-intent.json').exists()
    assert len(list(Path(settings.mail_restore_guard_dir).iterdir())) == 1
    monkeypatch.setattr(guard, 'release_batch', original_release)
    finished = restore.finalize(account, repo, path, 1)
    assert finished == {'safety_snapshot_id': result['snapshot_id'], 'mailboxes': 2, 'guards_released': True}
    assert not list(Path(settings.mail_restore_guard_dir).iterdir())
    assert restore.finalize(account, repo, path, 1) == finished
