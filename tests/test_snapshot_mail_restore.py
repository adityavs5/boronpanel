from pathlib import Path
import json

import pytest

from daemon import mail, snapshot_mail_restore as restore, snapshot_mail_files as files
from shared.config import settings
from shared.db import write_session
from shared.models import Account, MailDomain
from shared.validation import ValidationError


def test_batch_staging_retains_inventory_on_interruption(isolated_db, tmp_path, monkeypatch):
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    work = private / 'work'
    work.mkdir(mode=0o700)
    monkeypatch.setattr(settings, 'snapshot_private_dir', str(private))
    monkeypatch.setattr(settings, 'mail_base', str(tmp_path / 'mail'))
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(tmp_path / 'guards'))
    with write_session() as session:
        account = Account(username='alpha', status='active', uid=65534, gid=65534)
        session.add(account); session.flush()
        session.add(MailDomain(account_id=account.id, domain='alpha.example.test'))
    entries = []
    for local in ('one', 'two'):
        source = work / local
        source.mkdir(mode=0o700)
        home = Path(settings.mail_base) / 'alpha.example.test' / local
        for folder in ('cur', 'new', 'tmp'):
            (source / folder).mkdir()
            (home / 'Maildir' / folder).mkdir(parents=True)
        (source / 'cur' / 'restored:2,S').write_bytes(b'recovered message')
        (home / 'Maildir' / 'cur' / 'current:2,S').write_bytes(b'current message')
        entries.append({'domain': 'alpha.example.test', 'local_part': local,
                        'metadata': {'local_part': local}, 'prepared': str(source)})
    monkeypatch.setattr(mail, 'list_mailboxes', lambda domain: [{'local_part': 'one'}, {'local_part': 'two'}])
    from daemon import snapshot_mail_guard as guard
    acquired = restore.acquire_guards(account, {'work': str(work), 'entries': entries}, 17)
    persisted = json.loads(Path(acquired['index']).read_text())
    assert persisted['entries'] == acquired['entries']
    assert Path(acquired['index']).stat().st_mode & 0o777 == 0o600
    with guard.owned_guards(persisted['entries'], 17):
        pass
    with pytest.raises(FileExistsError):
        restore.acquire_guards(account, {'work': str(work), 'entries': entries}, 17)
    original = files.stage_for_exchange
    def interrupted(source, root, domain, local, **kwargs):
        if local == 'two':
            raise RuntimeError('simulated interruption')
        return original(source, root, domain, local, **kwargs)
    monkeypatch.setattr(files, 'stage_for_exchange', interrupted)
    with pytest.raises(RuntimeError, match='simulated'):
        restore.stage(account, {'work': str(work), 'entries': entries}, 17)
    index = work / 'placement-index.json'
    inventory = json.loads(index.read_text())
    assert index.stat().st_mode & 0o777 == 0o600
    assert inventory['account_id'] == account.id and inventory['restore_id'] == 17
    assert len(inventory['entries']) == 2
    first, second = inventory['entries']
    assert files.inspect_placement(first['receipt'], work) == 'ready'
    assert not Path(second['receipt']).exists()
    for local in ('one', 'two'):
        live = Path(settings.mail_base) / 'alpha.example.test' / local / 'Maildir'
        assert (live / 'cur' / 'current:2,S').read_bytes() == b'current message'
        assert not (live / 'cur' / 'restored:2,S').exists()
    before = index.read_bytes()
    with pytest.raises(FileExistsError):
        restore.stage(account, {'work': str(work), 'entries': entries}, 17)
    assert index.read_bytes() == before
    observed = restore.inspect_staging(account, work, 17)
    assert [entry['state'] for entry in observed['mailboxes']] == ['ready', 'not_started']
    with pytest.raises(ValidationError, match='inventory'):
        restore.inspect_staging(account, work, 18)
    receipt = Path(first['receipt'])
    saved_receipt = receipt.read_bytes()
    changed = json.loads(saved_receipt)
    changed['restore_id'] = 18
    receipt.write_text(json.dumps(changed))
    with pytest.raises(ValidationError, match='does not match'):
        restore.inspect_staging(account, work, 17)
    receipt.write_bytes(saved_receipt)
    # A path with no receipt is never adopted just because its name matches.
    unknown = Path(settings.mail_base) / second['domain'] / second['local_part'] / second['prepared']
    unknown.mkdir()
    assert restore.inspect_staging(account, work, 17)['mailboxes'][1]['state'] == 'unconfirmed'
    with write_session() as session:
        other = Account(username='bravo', status='active', uid=65533, gid=65533)
        session.add(other); session.flush()
        from sqlalchemy import select
        domain = session.scalar(select(MailDomain).where(MailDomain.domain == 'alpha.example.test'))
        domain.account_id = other.id
    with pytest.raises(ValidationError, match='no longer owned'):
        restore.inspect_staging(account, work, 17)
