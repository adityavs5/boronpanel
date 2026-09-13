"""Real offline Dovecot recovery semantics; no mail delivery or live config."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest


@pytest.fixture
def mailbox_store():
    if not shutil.which('doveadm'):
        pytest.skip('Dovecot required')
    uid, gid = (65534, 65534) if os.geteuid() == 0 else (os.getuid(), os.getgid())
    with tempfile.TemporaryDirectory(prefix='boron-mail-recovery-', dir='/tmp') as directory:
        root = Path(directory)
        if os.geteuid() == 0:
            os.chown(root, uid, gid)
        for name in ('home', 'live', 'run'):
            path = root / name
            path.mkdir()
            if os.geteuid() == 0:
                os.chown(path, uid, gid)
        config = root / 'dovecot.conf'
        config.write_text(
            f'base_dir = {root}/run\nmail_home = {root}/home\n'
            f'mail_location = maildir:{root}/live\nmail_uid = {uid}\nmail_gid = {gid}\n'
            'first_valid_uid = 1\nlog_path = /dev/stderr\nssl = no\n'
            'namespace inbox {\n inbox = yes\n separator = /\n}\n'
        )

        def command(*args):
            privilege = dict(user=uid, group=gid, extra_groups=[]) if os.geteuid() == 0 else {}
            result = subprocess.run(['doveadm', '-c', str(config), *args], cwd=root,
                                    capture_output=True, text=True, timeout=30, **privilege)
            assert result.returncode == 0, result.stderr
            return result.stdout

        def message(folder, identity, flags=''):
            target = root / 'live' / ('.' + folder if folder != 'INBOX' else '')
            for child in ('cur', 'new', 'tmp'):
                path = target / child
                path.mkdir(parents=True, exist_ok=True)
            path = target / 'cur' / f'{identity}.fixture:2,{flags}'
            path.write_text(f'From: sender@example.test\nTo: recipient@example.test\n'
                            f'Message-ID: <{identity}@example.test>\nSubject: {identity}\n\n{identity}\n')
            if os.geteuid() == 0:
                for item in [target, *target.iterdir(), path]:
                    os.chown(item, uid, gid)

        yield root, command, message


def test_point_in_time_mail_restore_and_safety_undo(mailbox_store):
    root, command, message = mailbox_store
    message('INBOX', 'original', 'S')
    message('Archive', 'archived', 'SF')
    command('mailbox', 'subscribe', 'Archive')
    before = command('mailbox', 'status', 'messages uidvalidity uidnext', '*')
    command('backup', '-f', 'maildir:' + str(root / 'snapshot'))
    # Simulate a deletion and new delivery after the saved point, only on disk.
    for path in (root / 'live/cur').iterdir():
        path.unlink()
    message('INBOX', 'newer')
    command('mailbox', 'status', 'messages', '*')
    command('backup', '-f', 'maildir:' + str(root / 'safety'))
    # Maildir cannot reinsert expunged IMAP UIDs into INBOX in place. Build
    # a fresh replacement first. This fixture has no connected clients or LMTP;
    # production must quiesce the selected mailbox before any directory switch.
    command('-o', 'mail_location=maildir:' + str(root / 'snapshot'),
            'backup', '-f', 'maildir:' + str(root / 'prepared'))
    (root / 'live').rename(root / 'displaced')
    (root / 'prepared').rename(root / 'live')
    messages = command('fetch', 'hdr.message-id flags', 'ALL')
    assert '<original@example.test>' in messages
    assert '<archived@example.test>' in messages
    assert '<newer@example.test>' not in messages
    assert '\\Seen' in messages and '\\Flagged' in messages
    assert 'Archive' in command('mailbox', 'list', '-s')
    assert command('mailbox', 'status', 'messages uidvalidity uidnext', '*') == before
    command('-o', 'mail_location=maildir:' + str(root / 'safety'),
            'backup', '-f', 'maildir:' + str(root / 'prepared-undo'))
    (root / 'live').rename(root / 'displaced-undo')
    (root / 'prepared-undo').rename(root / 'live')
    undone = command('fetch', 'hdr.message-id', 'ALL')
    assert '<newer@example.test>' in undone
    assert '<original@example.test>' not in undone
    assert '<archived@example.test>' in undone
