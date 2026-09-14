"""Remove completed mailbox preparation copies, retaining recovery evidence.

This does not remove live Maildirs, displaced mailbox siblings or encrypted
snapshots. Callers hold the account/repository locks. Failed and interrupted
jobs are ineligible; absent preparation directories allow crash-safe retries.
"""
import os
from pathlib import Path
import shutil
import stat

from daemon import snapshot_mail_journal as journal
from daemon.snapshot_mail_restore import _private_document
from shared.db import write_session
from shared.models import SnapshotMailRecovery, SnapshotRestore
from shared.validation import ValidationError


def cleanup_preparation(ident):
    with write_session() as session:
        row = session.get(SnapshotRestore, ident)
        checkpoint = session.get(SnapshotMailRecovery, ident)
        if (row is None or row.status != 'completed' or row.selection.get('kind') != 'mail'
                or row.summary.get('guards_released') is not True
                or checkpoint is None or checkpoint.phase != 'completed'
                or not checkpoint.work or not checkpoint.journal):
            raise ValidationError('Only completed mailbox preparation can be removed')
        if row.summary.get('preparation_cleaned') is True:
            return
        account_id = row.account_id
        work = Path(checkpoint.work)
        path = journal._path(Path(checkpoint.journal))
        if path != work / 'switch.json':
            raise ValidationError('Mailbox cleanup journal does not match its work directory')
        payload = journal.read(path)
        if payload['restore_id'] != ident or payload['undo']:
            raise ValidationError('Mailbox cleanup journal does not match its job')
        receipt = _private_document(work / 'mail-safety-result.json')
        expected = dict(format=1, account_id=account_id, restore_id=ident,
                        snapshot_id=receipt.get('snapshot_id'))
        if receipt != expected or not receipt.get('snapshot_id'):
            raise ValidationError('Mailbox cleanup lacks a safety receipt')
        # The encrypted copy may since have expired under the policy. That does
        # not make redundant preparation data part of a completed recovery.
        if row.safety_snapshot_id and row.safety_snapshot_id != receipt['snapshot_id']:
            raise ValidationError('Mailbox cleanup safety snapshot changed')
        intent = _private_document(work / 'release-intent.json')
        if intent != dict(expected, operation_id=payload['operation_id']):
            raise ValidationError('Mailbox cleanup lacks a completed release intent')

    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValidationError('Safe directory cleanup is unavailable')
    # _path validates every private ancestor. Pin the work directory before
    # removal; only fixed preparation names are allowed, never journal paths.
    journal._path(work / 'cleanup-check')
    fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        names = ['data', *('ready-' + str(i) for i in range(len(payload['entries'])))]
        # Validate all roots before removing any. Nested links are unlinked by
        # descriptor-based rmtree, never followed outside the staging tree.
        for name in names:
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(info.st_mode):
                raise ValidationError('Mailbox preparation root is not a directory')
        for name in names:
            try:
                shutil.rmtree(name, dir_fd=fd)
            except FileNotFoundError:
                if os.path.lexists(work / name):
                    raise
        os.fsync(fd)
    finally:
        os.close(fd)
    with write_session() as session:
        row = session.get(SnapshotRestore, ident)
        row.summary = {**row.summary, 'preparation_cleaned': True}
