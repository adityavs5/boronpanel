import json
import os
import shutil

import pytest

from daemon import snapshot_mail_displaced as cleanup, snapshot_mail_exchange as exchange
from shared.config import settings
from shared.validation import ValidationError


@pytest.fixture
def displaced(tmp_path, monkeypatch):
    private = tmp_path / 'private'; private.mkdir(mode=0o700)
    monkeypatch.setattr(settings, 'snapshot_private_dir', str(private))
    monkeypatch.setattr(settings, 'mail_base', str(tmp_path / 'mail'))
    home = tmp_path / 'mail/example.test/inbox'
    prepared = '.boron-mail-ready-' + 'a'*32
    for name, content in [('Maildir', 'old'), (prepared, 'restored')]:
        for part in ('cur', 'new', 'tmp'):
            (home / name / part).mkdir(parents=True)
        (home / name / 'cur/message').write_text(content)
    plan = exchange.plan('example.test', 'inbox', prepared)
    digest = cleanup.tree_digest(home / 'Maildir')
    exchange.apply('example.test', 'inbox', plan)
    entry = dict(domain='example.test', local_part='inbox', plan=plan)
    return home, entry, private / 'cleanup.json', digest


def test_only_verified_displaced_tree_removed(displaced):
    home, entry, receipt, digest = displaced
    cleanup.remove_verified(entry, receipt, digest)
    assert (home / 'Maildir/cur/message').read_text() == 'restored'
    assert not (home / entry['plan']['prepared']).exists()
    assert not list(home.glob('.boron-mail-cleanup-*'))
    assert json.loads(receipt.read_text())['phase'] == 'deleted'
    cleanup.remove_verified(entry, receipt, digest)


def test_changed_mail_preserved(displaced):
    home, entry, receipt, digest = displaced
    (home / entry['plan']['prepared'] / 'cur/message').write_text('new unsaved content')
    with pytest.raises(ValidationError, match='differs'):
        cleanup.remove_verified(entry, receipt, digest)
    assert (home / entry['plan']['prepared'] / 'cur/message').read_text() == 'new unsaved content'
    assert not receipt.exists()


def test_crash_after_quarantine_resumes_without_replaying_move(displaced, monkeypatch):
    home, entry, receipt, digest = displaced
    original = cleanup._move
    def interrupted(*args):
        original(*args)
        raise OSError('crash after rename')
    monkeypatch.setattr(cleanup, '_move', interrupted)
    with pytest.raises(OSError, match='crash'):
        cleanup.remove_verified(entry, receipt, digest)
    assert not (home / entry['plan']['prepared']).exists()
    monkeypatch.setattr(cleanup, '_move', lambda *args: pytest.fail('Replayed move'))
    cleanup.remove_verified(entry, receipt, digest)
    assert (home / 'Maildir/cur/message').read_text() == 'restored'


def test_partial_deletion_resumes_from_private_receipt(displaced, monkeypatch):
    home, entry, receipt, digest = displaced
    original = shutil.rmtree
    def partial(name, *, dir_fd):
        child = os.open(name, exchange.FLAGS, dir_fd=dir_fd)
        try:
            os.unlink('cur/message', dir_fd=child)
        finally:
            os.close(child)
        raise OSError('interrupted deletion')
    partial.avoids_symlink_attacks = True
    monkeypatch.setattr(shutil, 'rmtree', partial)
    with pytest.raises(OSError, match='interrupted'):
        cleanup.remove_verified(entry, receipt, digest)
    assert json.loads(receipt.read_text())['phase'] == 'deleting'
    monkeypatch.setattr(shutil, 'rmtree', original)
    cleanup.remove_verified(entry, receipt, digest)
    assert (home / 'Maildir/cur/message').read_text() == 'restored'


def test_changed_quarantine_is_not_deleted(displaced, monkeypatch):
    home, entry, receipt, digest = displaced
    original = cleanup._move
    def interrupted(*args):
        original(*args)
        raise OSError()
    monkeypatch.setattr(cleanup, '_move', interrupted)
    with pytest.raises(OSError):
        cleanup.remove_verified(entry, receipt, digest)
    record = json.loads(receipt.read_text())
    q = home / record['quarantine']
    q.rename(home / 'retained')
    q.mkdir(mode=0o700)
    (q / 'keep').write_text('other data')
    with pytest.raises(ValidationError, match='quarantine changed'):
        cleanup.remove_verified(entry, receipt, digest)
    assert (q / 'keep').read_text() == 'other data'
    assert (home / 'retained/displaced/cur/message').read_text() == 'old'


def test_symlink_never_followed(displaced, tmp_path):
    home, entry, receipt, digest = displaced
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'keep').write_text('keep')
    source = home / entry['plan']['prepared']
    (source / 'link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValidationError, match='link'):
        cleanup.remove_verified(entry, receipt, digest)
    assert (outside / 'keep').read_text() == 'keep'


def test_directory_identity_replacement_preserved(displaced):
    home, entry, receipt, digest = displaced
    source = home / entry['plan']['prepared']
    source.rename(home / 'retained')
    shutil.copytree(home / 'retained', source)
    with pytest.raises(ValidationError, match='journal'):
        cleanup.remove_verified(entry, receipt, digest)
    assert (source / 'cur/message').read_text() == 'old'


def test_post_move_content_change_is_retained(displaced, monkeypatch):
    home, entry, receipt, digest = displaced
    original = cleanup._move
    def changed(source, name, destination):
        original(source, name, destination)
        fd = os.open('displaced/cur/message', os.O_WRONLY | os.O_TRUNC, dir_fd=destination)
        os.write(fd, b'changed after verification'); os.close(fd)
    monkeypatch.setattr(cleanup, '_move', changed)
    with pytest.raises(ValidationError, match='Quarantined mailbox differs'):
        cleanup.remove_verified(entry, receipt, digest)
    record = json.loads(receipt.read_text())
    assert (home / record['quarantine'] / 'displaced/cur/message').read_text() == 'changed after verification'
    assert (home / 'Maildir/cur/message').read_text() == 'restored'


def test_crash_after_deleted_receipt_removes_empty_container_on_retry(displaced, monkeypatch):
    home, entry, receipt, digest = displaced
    original = cleanup.os.rmdir
    def interrupted(path, **kwargs):
        if str(path).startswith('.boron-mail-cleanup-'):
            raise OSError('crash before empty quarantine removal')
        return original(path, **kwargs)
    monkeypatch.setattr(cleanup.os, 'rmdir', interrupted)
    with pytest.raises(OSError, match='empty quarantine'):
        cleanup.remove_verified(entry, receipt, digest)
    assert json.loads(receipt.read_text())['phase'] == 'deleted'
    monkeypatch.setattr(cleanup.os, 'rmdir', original)
    cleanup.remove_verified(entry, receipt, digest)
    assert not list(home.glob('.boron-mail-cleanup-*'))
