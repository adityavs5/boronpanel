import os

import pytest

from daemon import snapshot_mail_exchange as exchange
from shared.config import settings
from shared.validation import ValidationError


@pytest.fixture
def mailbox(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'mail_base', str(tmp_path))
    home = tmp_path / 'example.test/inbox'
    prepared = '.boron-mail-ready-' + 'a' * 32
    for name, content in [('Maildir', b'current message'), (prepared, b'saved message')]:
        for part in ('cur', 'new', 'tmp'):
            (home / name / part).mkdir(parents=True)
        (home / name / 'cur/message:2,S').write_bytes(content)
    return home, prepared


def test_exchange_keeps_both_trees_and_undo_restores_current_mail(mailbox):
    home, prepared = mailbox
    saved = exchange.plan('example.test', 'inbox', prepared)
    assert exchange.inspect('example.test', 'inbox', saved) == 'ready'
    assert exchange.apply('example.test', 'inbox', saved) == 'applied'
    assert (home / 'Maildir/cur/message:2,S').read_bytes() == b'saved message'
    assert (home / prepared / 'cur/message:2,S').read_bytes() == b'current message'
    with pytest.raises(ValidationError):
        exchange.apply('example.test', 'inbox', saved)
    assert exchange.apply('example.test', 'inbox', saved, undo=True) == 'ready'
    assert (home / 'Maildir/cur/message:2,S').read_bytes() == b'current message'
    assert (home / prepared / 'cur/message:2,S').read_bytes() == b'saved message'


def test_post_exchange_fsync_failure_is_detected_without_replaying(mailbox, monkeypatch):
    home, prepared = mailbox
    saved = exchange.plan('example.test', 'inbox', prepared)
    real = exchange.os.fsync
    def fail(fd):
        raise OSError('simulated failure after atomic exchange')
    monkeypatch.setattr(exchange.os, 'fsync', fail)
    with pytest.raises(OSError):
        exchange.apply('example.test', 'inbox', saved)
    assert exchange.inspect('example.test', 'inbox', saved) == 'applied'
    monkeypatch.setattr(exchange.os, 'fsync', real)
    assert exchange.apply('example.test', 'inbox', saved, undo=True) == 'ready'


def test_worker_exit_after_exchange_is_recoverable(mailbox):
    home, prepared = mailbox
    saved = exchange.plan('example.test', 'inbox', prepared)
    pid = os.fork()
    if pid == 0:
        try:
            exchange.os.fsync = lambda fd: os._exit(91)
            exchange.apply('example.test', 'inbox', saved)
        finally:
            os._exit(92)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 91
    assert exchange.inspect('example.test', 'inbox', saved) == 'applied'
    assert (home / 'Maildir/cur/message:2,S').read_bytes() == b'saved message'
    assert (home / prepared / 'cur/message:2,S').read_bytes() == b'current message'
    assert exchange.apply('example.test', 'inbox', saved, undo=True) == 'ready'


def test_replaced_directory_rejects_stale_plan(mailbox):
    home, prepared = mailbox
    saved = exchange.plan('example.test', 'inbox', prepared)
    (home / prepared).rename(home / 'retained')
    for name in ('cur', 'new', 'tmp'):
        (home / prepared / name).mkdir(parents=True)
    with pytest.raises(ValidationError):
        exchange.apply('example.test', 'inbox', saved)
    assert (home / 'Maildir/cur/message:2,S').read_bytes() == b'current message'


@pytest.mark.parametrize('name', ['../outside', 'Maildir', '.boron-mail-ready-bad'])
def test_invalid_prepared_identifier_rejected(mailbox, name):
    with pytest.raises(ValidationError):
        exchange.plan('example.test', 'inbox', name)


@pytest.mark.parametrize('location', ['ancestor', 'maildir', 'child'])
def test_symbolic_links_rejected(mailbox, location):
    home, prepared = mailbox
    if location == 'ancestor':
        actual = home.with_name('retained')
        home.rename(actual)
        home.symlink_to(actual, target_is_directory=True)
    elif location == 'maildir':
        (home / 'Maildir').rename(home / 'retained')
        (home / 'Maildir').symlink_to(home / 'retained', target_is_directory=True)
    else:
        (home / 'Maildir/new').rmdir()
        (home / 'Maildir/new').symlink_to(home / 'Maildir/cur', target_is_directory=True)
    with pytest.raises(OSError):
        exchange.plan('example.test', 'inbox', prepared)


def test_other_mailbox_plan_cannot_be_applied(mailbox):
    home, prepared = mailbox
    saved = exchange.plan('example.test', 'inbox', prepared)
    other = home.with_name('other')
    for name in ('Maildir', prepared):
        for part in ('cur', 'new', 'tmp'):
            (other / name / part).mkdir(parents=True)
    with pytest.raises(ValidationError):
        exchange.apply('example.test', 'other', saved)
    assert (home / 'Maildir/cur/message:2,S').read_bytes() == b'current message'
