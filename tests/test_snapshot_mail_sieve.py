import base64
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from daemon import mail, snapshot_mail_sieve as sieve
from shared.config import settings
from shared.db import write_session
from shared.models import Account, MailDomain
from shared.validation import ValidationError


@pytest.fixture
def mail_home(isolated_db, tmp_path, monkeypatch):
    with write_session() as session:
        account = Account(username='alpha', status='active')
        other = Account(username='bravo', status='active')
        session.add_all([account, other]); session.flush()
        session.add_all([MailDomain(account_id=account.id, domain='alpha.example.test'),
                         MailDomain(account_id=other.id, domain='bravo.example.test')])
    root = tmp_path / 'vmail'
    home = root / 'alpha.example.test' / 'inbox'
    home.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(settings, 'mail_base', str(root))
    monkeypatch.setattr(mail, 'VMAIL_UID', os.geteuid())
    monkeypatch.setattr(mail, 'list_mailboxes', lambda domain: [dict(local_part='inbox'), dict(local_part='unused')])
    return account, other, home


def test_capture_preserves_custom_script_bytes_and_absence(mail_home):
    account, _, home = mail_home
    content = b'# custom rules\r\nrequire ["fileinto"];\r\n# non-UTF8 comment \xff\r\nkeep;\r\n'
    path = home / sieve.SCRIPT_NAME
    path.write_bytes(content); path.chmod(0o600)
    (home / '.dovecot.svbin').write_bytes(b'derived binary cache')
    captured = sieve.capture(account, ['unused@alpha.example.test', 'inbox@alpha.example.test'])
    assert captured == dict(format=1, account_id=account.id, username='alpha', scripts=[
        dict(domain='alpha.example.test', local_part='inbox', script_base64=base64.b64encode(content).decode('ascii')),
        dict(domain='alpha.example.test', local_part='unused', script_base64=None)])
    assert path.read_bytes() == content
    assert (home / '.dovecot.svbin').read_bytes() == b'derived binary cache'
    assert not (home.parent / 'unused').exists()


def test_empty_script_is_distinct_from_absent_script(mail_home):
    account, _, home = mail_home
    assert sieve.capture(account, ['inbox@alpha.example.test'])['scripts'][0]['script_base64'] is None
    (home / sieve.SCRIPT_NAME).write_bytes(b'')
    assert sieve.capture(account, ['inbox@alpha.example.test'])['scripts'][0]['script_base64'] == ''


@pytest.mark.parametrize('kind', ['script_link', 'home_link', 'domain_link', 'base_link', 'fifo', 'hardlink', 'writable_script', 'writable_home', 'oversized'])
def test_capture_rejects_unsafe_storage(mail_home, tmp_path, monkeypatch, kind):
    account, _, home = mail_home
    path = home / sieve.SCRIPT_NAME
    path.write_bytes(b'keep;\n'); path.chmod(0o600)
    if kind == 'script_link':
        target = tmp_path / 'outside'; target.write_bytes(b'private marker')
        path.unlink(); path.symlink_to(target)
    elif kind in ('home_link', 'domain_link', 'base_link'):
        target = {'home_link': home, 'domain_link': home.parent, 'base_link': home.parent.parent}[kind]
        moved = target.with_name(target.name + '-moved')
        target.rename(moved); target.symlink_to(moved, target_is_directory=True)
    elif kind == 'fifo':
        path.unlink(); os.mkfifo(path)
    elif kind == 'hardlink':
        os.link(path, tmp_path / 'linked-script')
    elif kind == 'writable_script':
        path.chmod(0o666)
    elif kind == 'writable_home':
        home.chmod(0o777)
    elif kind == 'oversized':
        monkeypatch.setattr(sieve, 'MAX_SCRIPT_BYTES', 4)
    with pytest.raises(ValidationError):
        sieve.capture(account, ['inbox@alpha.example.test'])


@pytest.mark.parametrize('addresses', [[], ['inbox@bravo.example.test'], ['missing@alpha.example.test'],
                                       ['inbox@alpha.example.test'] * 2, ['../inbox@alpha.example.test']])
def test_capture_rejects_unauthorized_or_invalid_selection_before_files(mail_home, monkeypatch, addresses):
    account, _, _ = mail_home
    def forbidden(*args): raise AssertionError('Must authorize before opening storage')
    monkeypatch.setattr(sieve, '_home', forbidden)
    with pytest.raises(ValidationError): sieve.capture(account, addresses)


def test_capture_rejects_mail_domain_transfer_during_read(mail_home, monkeypatch):
    account, other, _ = mail_home
    read = sieve._read_script
    def transferring_read(descriptor):
        result = read(descriptor)
        with write_session() as session:
            session.scalar(select(MailDomain).where(MailDomain.domain == 'alpha.example.test')).account_id = other.id
        return result
    monkeypatch.setattr(sieve, '_read_script', transferring_read)
    with pytest.raises(ValidationError, match='another account'):
        sieve.capture(account, ['inbox@alpha.example.test'])


def test_capture_detects_script_replacement_during_read(mail_home, monkeypatch):
    account, _, home = mail_home
    path = home / sieve.SCRIPT_NAME
    path.write_bytes(b'keep;\n')
    original_stat = os.stat
    def replaced(name, *args, **kwargs):
        if name == sieve.SCRIPT_NAME and kwargs.get('dir_fd') is not None:
            replacement = home / 'replacement'
            replacement.write_bytes(b'keep;\n')
            replacement.replace(path)
        return original_stat(name, *args, **kwargs)
    monkeypatch.setattr(os, 'stat', replaced)
    with pytest.raises(ValidationError, match='changed during capture'):
        sieve.capture(account, ['inbox@alpha.example.test'])


def test_capture_enforces_total_limit(mail_home, monkeypatch):
    account, _, home = mail_home
    (home / sieve.SCRIPT_NAME).write_bytes(b'keep;\n')
    monkeypatch.setattr(sieve, 'MAX_TOTAL_BYTES', 4)
    with pytest.raises(ValidationError, match='exceed the recovery limit'):
        sieve.capture(account, ['inbox@alpha.example.test'])


def routing_document(account, responders):
    return dict(format=1, username=account.username, domains=[dict(domain='alpha.example.test',
        forwards=[], catchall=None, autoresponders=responders)])


def reply(local='inbox', active=True):
    return dict(local_part=local, subject='Away "today"', body='First line\n.\nLast line',
                active=active, start_date='2026-09-01', end_date='2026-09-30')


def test_prepare_compiles_new_reply_and_removes_post_backup_reply(mail_home):
    account, _, home = mail_home
    custom = b'# existing custom script\nkeep;\n'
    (home / sieve.SCRIPT_NAME).write_bytes(custom)
    desired = routing_document(account, [reply()])
    current = routing_document(account, [reply('unused')])
    prepared = sieve.prepare_changes(account, desired, current)
    assert len(prepared['scripts']) == 2
    first, second = prepared['scripts']
    generated = base64.b64decode(first['script_base64']).decode('utf-8')
    assert first['local_part'] == 'inbox'
    assert ':addresses ["inbox@alpha.example.test"]' in generated
    assert '2026-09-30' in generated and '\n..\n' in generated
    assert second == dict(domain='alpha.example.test', local_part='unused', script_base64=None)
    assert (home / sieve.SCRIPT_NAME).read_bytes() == custom
    assert not (home.parent / 'unused').exists()


def test_prepare_disabled_reply_records_script_absence(mail_home):
    account, _, _ = mail_home
    prepared = sieve.prepare_changes(account, routing_document(account, [reply(active=False)]),
                                      routing_document(account, [reply()]))
    assert prepared['scripts'][0]['script_base64'] is None


def test_prepare_without_panel_replies_leaves_custom_scripts_untouched(mail_home):
    account, _, home = mail_home
    (home / sieve.SCRIPT_NAME).write_bytes(b'keep;\n')
    empty = routing_document(account, [])
    assert sieve.prepare_changes(account, empty, empty)['scripts'] == []
    assert (home / sieve.SCRIPT_NAME).read_bytes() == b'keep;\n'


def test_prepare_compiler_failure_is_sanitized_and_leaves_live_script(mail_home, monkeypatch):
    from daemon import autoresponder
    account, _, home = mail_home
    path = home / sieve.SCRIPT_NAME
    path.write_bytes(b'keep;\n')
    def failed(text): raise RuntimeError('private compiler diagnostic marker')
    monkeypatch.setattr(autoresponder, '_validate_sieve_content', failed)
    with pytest.raises(ValidationError, match='no live scripts changed') as error:
        sieve.prepare_changes(account, routing_document(account, [reply()]), routing_document(account, []))
    assert 'private compiler diagnostic marker' not in str(error.value)
    assert path.read_bytes() == b'keep;\n'


def test_prepare_rejects_mismatched_domain_selection(mail_home):
    account, _, _ = mail_home
    with pytest.raises(ValidationError, match='matching selected mail domains'):
        sieve.prepare_changes(account, routing_document(account, []), dict(format=1, username='alpha', domains=[]))


def test_prepare_rejects_invalid_text_encoding(mail_home):
    account, _, _ = mail_home
    invalid = reply(); invalid['body'] = '\ud800'
    with pytest.raises(ValidationError, match='invalid text encoding'):
        sieve.prepare_changes(account, routing_document(account, [invalid]), routing_document(account, []))
