"""Account-bound configuration recovery metadata and scheduled-task restore."""
import json
import os
from pathlib import Path
import shutil
import stat
import uuid

from daemon import cron, snapshot_jobs as jobs, snapshot_storage as storage
from daemon.snapshot_db_metadata import write_metadata
from shared.config import settings
from shared.validation import ValidationError


def _load_metadata(repo, account, snapshot_id, *, source_restore_id=None):
    """Decrypt only owned metadata; never return raw configuration through RPC."""
    snapshot = storage.owned_snapshot(repo, account.id, snapshot_id)
    if source_restore_id is None:
        source = Path(settings.snapshot_private_dir)/'sources'/f'account-{account.id}'/'manifest.json'
    else:
        if type(source_restore_id) is not int or source_restore_id <= 0:
            raise ValidationError('Invalid configuration recovery identifier')
        source = Path(settings.snapshot_private_dir)/'restores'/f'restore-{source_restore_id}'/'config-recovery.json'
        if snapshot.get('paths') != [str(source)]:
            raise ValidationError('Configuration recovery paths do not match the source job')
    work = jobs.private_directory('config-metadata', uuid.uuid4().hex)
    try:
        data = storage.restore_to(repo, account.id, snapshot_id, str(work/'data'), selected_paths=[str(source)])
        path = data/str(source).lstrip('/')
        if path.resolve() != path or not path.is_relative_to(data):
            raise ValidationError('Unsafe configuration metadata path')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_size > 8*1024*1024:
                raise ValidationError('Invalid configuration recovery metadata')
            try:
                payload = json.loads(handle.read(8*1024*1024+1))
            except (ValueError, UnicodeError):
                raise ValidationError('Invalid configuration recovery metadata') from None
        if (not isinstance(payload, dict) or type(payload.get('format')) is not int or payload['format'] != 1
                or type(payload.get('account_id')) is not int or payload['account_id'] != account.id
                or payload.get('username') != account.username):
            raise ValidationError('Configuration belongs to another account or format')
        if source_restore_id is not None and payload.get('restore_id') != source_restore_id:
            raise ValidationError('Configuration recovery belongs to another restore')
        if source_restore_id is None and 'config' not in payload.get('components', []):
            raise ValidationError('Snapshot does not contain account configuration')
        return payload
    finally:
        shutil.rmtree(work)



def load_cron(repo, account, snapshot_id, *, source_restore_id=None):
    payload = _load_metadata(repo, account, snapshot_id, source_restore_id=source_restore_id)
    if 'cron_configuration' not in payload:
        raise ValidationError('This older recovery point has no complete crontab; create a new configuration backup')
    return cron.validate_configuration(account.username, payload['cron_configuration'])


def load_php(repo, account, snapshot_id, *, source_restore_id=None):
    from daemon import snapshot_php
    payload = _load_metadata(repo, account, snapshot_id, source_restore_id=source_restore_id)
    if 'php_configuration' not in payload:
        raise ValidationError('This recovery point has no complete PHP settings; create a new configuration backup')
    return snapshot_php.validate_for_restore(account, payload['php_configuration'])


def restore_cron(ident, account, row, repo, snapshot_id, work, update):
    selected = load_cron(repo, account, snapshot_id, source_restore_id=row.selection.get('source_restore_id'))
    current = cron.capture_configuration(account.username)
    path = work/'config-recovery.json'
    write_metadata(path, dict(format=1, account_id=account.id, username=account.username,
                              restore_id=ident, cron_configuration=current))
    result = storage.backup(repo, account.id, [str(path)])
    update(ident, safety_snapshot_id=result['snapshot_id'], progress_message='Restoring scheduled tasks')
    # Current state is durably backed up before the single crontab installation.
    try:
        cron.restore_configuration(account.username, selected)
    except Exception:
        raise ValidationError('Could not confirm crontab installation. The previous configuration is retained for recovery.') from None
    from shared.models import utcnow
    update(ident, status='completed', summary={'config_sections': ['cron']},
           progress_message='Scheduled tasks restored', completed_at=utcnow())


def restore_php(ident, account, row, repo, snapshot_id, work, update):
    """PHP worker entry point, invoked under account and repository locks."""
    from daemon import snapshot_php
    from shared.models import utcnow
    selected = load_php(repo, account, snapshot_id,
                        source_restore_id=row.selection.get('source_restore_id'))

    def save_previous(previous):
        path = work/'config-recovery.json'
        write_metadata(path, dict(format=1, account_id=account.id, username=account.username,
                                  restore_id=ident, php_configuration=previous))
        result = storage.backup(repo, account.id, [str(path)])
        update(ident, safety_snapshot_id=result['snapshot_id'], progress_message='Restoring PHP settings')

    snapshot_php.apply_configuration(account, selected, save_previous)
    update(ident, status='completed', summary={'config_sections': ['php']},
           progress_message='PHP settings restored', completed_at=utcnow())
