import sys
from pathlib import Path

import pytest

from daemon import snapshot_mail_restore as restore, snapshot_mail_journal as journal
from daemon import snapshot_mail_exchange as exchange, snapshot_mail_service as supervisor
from shared.config import settings
from shared.db import write_session
from shared.models import Account, MailDomain
from shared.validation import ValidationError
from tests.test_snapshot_mail_journal import saved
from tests.test_snapshot_mail_service import isolated_service


@pytest.mark.parametrize('count,interrupt_undo', [(0, False), (1, False), (2, True)])
def test_rollback_orchestration_recovers_partial_undo(isolated_db, saved, isolated_service, tmp_path, monkeypatch, count, interrupt_undo):
    path, payload = saved
    name, operations = isolated_service
    with write_session() as session:
        account = Account(username='alpha', status='active', uid=65534, gid=65534)
        session.add(account); session.flush()
        session.add(MailDomain(account_id=account.id, domain='example.test'))
    for index, entry in enumerate(payload['entries']):
        home = Path(settings.mail_base) / entry['domain'] / entry['local_part']
        (home / 'Maildir/cur/original').write_text('original mail')
        (home / entry['plan']['prepared'] / 'cur/replacement').write_text('replacement mail')
        if index < count:
            exchange.apply(entry['domain'], entry['local_part'], entry['plan'])
    attempts = []
    if count == 0:
        operations.append(payload['operation_id'])
        with pytest.raises(ValidationError, match='Mail switch failed'):
            supervisor.supervised_command(['/usr/bin/false'], payload['operation_id'], service=name)
    def launch(undo):
        operation = journal.read(undo)['operation_id']
        operations.append(operation)
        attempts.append(undo)
        script = tmp_path / ('undo-' + str(len(attempts)) + '.py')
        repo = Path(__file__).parents[1]
        code = (f'import sys\nsys.path.insert(0, {str(repo)!r})\n'
                'from shared.config import settings\n'
                'from daemon import snapshot_mail_journal as journal, snapshot_mail_exchange as exchange\n'
                f'settings.snapshot_private_dir = {settings.snapshot_private_dir!r}\n'
                f'settings.mail_restore_guard_dir = {settings.mail_restore_guard_dir!r}\n'
                f'settings.mail_base = {settings.mail_base!r}\n')
        if interrupt_undo and len(attempts) == 1:
            code += ('original = exchange.apply\n'
                     'def interrupt(domain, local, plan, **kwargs):\n'
                     ' if local == "two": raise SystemExit(93)\n'
                     ' return original(domain, local, plan, **kwargs)\n'
                     'exchange.apply = interrupt\n')
        script.write_text(code + f'journal.execute({str(undo)!r}, service={name!r})\n')
        return supervisor.supervised_command([sys.executable, str(script)], operation, service=name)
    monkeypatch.setattr(journal, 'launch', launch)
    if interrupt_undo:
        with pytest.raises(ValidationError, match='Mail switch failed'):
            restore.rollback_restore(account, path, 1, service=name)
        assert restore.rollback_journal(path) == attempts[0]
        assert [row['state'] for row in journal.inspect(path)] == ['ready', 'applied']
    result = restore.rollback_restore(account, path, 1, service=name)
    assert result == {'rolled_back': True, 'mailboxes': 2}
    assert [row['state'] for row in journal.inspect(path)] == ['ready', 'ready']
    assert not list(Path(settings.mail_restore_guard_dir).iterdir())
    assert restore.rollback_restore(account, path, 1, service=name) == result
    assert len(attempts) == (2 if interrupt_undo else (1 if count else 0))
    if interrupt_undo:
        assert len(journal.read(attempts[1])['entries']) == 1
    for entry in payload['entries']:
        home = Path(settings.mail_base) / entry['domain'] / entry['local_part']
        assert (home / 'Maildir/cur/original').read_text() == 'original mail'
