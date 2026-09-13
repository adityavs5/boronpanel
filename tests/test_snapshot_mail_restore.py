from pathlib import Path
import json

import pytest

from daemon import mail, snapshot_mail_restore as restore, snapshot_mail_files as files
from shared.config import settings
from shared.db import write_session
from shared.models import Account, MailDomain


def test_batch_staging_retains_inventory_on_interruption(isolated_db, tmp_path, monkeypatch):
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    work = private / 'work'
    work.mkdir(mode=0o700)
    monkeypatch.setattr(settings, 'snapshot_private_dir', str(private))
    monkeypatch.setattr(settings, 'mail_base', str(tmp_path / 'mail'))
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
