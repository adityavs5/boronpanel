"""Clean completed mailbox staging and verified displaced duplicates.

Callers hold account/repository locks. Live Maildirs, encrypted snapshots and
private recovery journals are retained. Incomplete jobs are never eligible.
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


def cleanup_displaced(ident, repo):
    """Verify encrypted recovery contents before disposing of local duplicates."""
    import uuid
    from daemon import snapshot_jobs as jobs, snapshot_storage as storage
    from daemon.snapshot_mail_restore import safety_inventory
    from daemon.snapshot_mail_displaced import tree_digest, remove_verified
    from shared.models import Account
    with write_session() as session:
        row = session.get(SnapshotRestore, ident)
        checkpoint = session.get(SnapshotMailRecovery, ident)
        if (row is None or row.status != 'completed' or row.selection.get('kind') != 'mail'
                or row.summary.get('guards_released') is not True
                or checkpoint is None or checkpoint.phase != 'completed'):
            raise ValidationError('Only completed displaced mailbox copies can be removed')
        if row.summary.get('displaced_cleaned') is True:
            return
        if not row.safety_snapshot_id or not checkpoint.work or not checkpoint.journal:
            raise ValidationError('Displaced mailbox cleanup lacks recovery evidence')
        account = session.get(Account, row.account_id)
        snapshot_id = row.safety_snapshot_id
        work = Path(checkpoint.work)
        path = journal._path(Path(checkpoint.journal))
        if path != work / 'switch.json':
            raise ValidationError('Displaced cleanup journal changed')
        payload = journal.read(path)
        if payload['restore_id'] != ident or payload['undo']:
            raise ValidationError('Displaced cleanup job changed')
        expected = dict(format=1, account_id=row.account_id, restore_id=ident, snapshot_id=snapshot_id)
        if (_private_document(work / 'mail-safety-result.json') != expected
                or _private_document(work / 'release-intent.json') != dict(expected, operation_id=payload['operation_id'])):
            raise ValidationError('Displaced cleanup release evidence changed')
    inventory = safety_inventory(account, repo, snapshot_id, ident)
    from shared.config import settings
    expected_paths = [str(Path(settings.mail_base) / entry['domain'] / entry['local_part'] / entry['plan']['prepared'])
                      for entry in payload['entries']]
    if {(entry['domain'], entry['local_part'], entry['path']) for entry in inventory} != {
            (entry['domain'], entry['local_part'], path) for entry, path in zip(payload['entries'], expected_paths)}:
        raise ValidationError('Encrypted displaced inventory differs from the journal')
    verification = jobs.private_directory('mail-cleanup-verification', uuid.uuid4().hex)
    try:
        data = storage.restore_to(repo, account.id, snapshot_id, str(verification / 'data'),
                                  selected_paths=expected_paths)
        digests = []
        for path in expected_paths:
            restored = data / path.lstrip('/')
            if restored.resolve() != restored or not restored.is_relative_to(data):
                raise ValidationError('Unsafe restored mailbox verification path')
            digests.append(tree_digest(restored))
        for index, (entry, digest) in enumerate(zip(payload['entries'], digests)):
            remove_verified(entry, work / ('displaced-cleanup-' + str(index) + '.json'), digest)
    finally:
        shutil.rmtree(verification)
    with write_session() as session:
        row = session.get(SnapshotRestore, ident)
        row.summary = {**row.summary, 'displaced_cleaned': True}
