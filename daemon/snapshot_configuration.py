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


def load_dns(repo, account, snapshot_id, *, source_restore_id=None, selected_zones=None):
    from daemon import snapshot_dns
    payload = _load_metadata(repo, account, snapshot_id, source_restore_id=source_restore_id)
    if 'dns_configuration' not in payload:
        raise ValidationError('This recovery point has no complete DNS settings; create a new configuration backup')
    return snapshot_dns.validate_for_restore(account, payload['dns_configuration'], selected_zones)


def restore_dns(ident, account, row, repo, snapshot_id, work, update):
    from daemon import snapshot_dns
    from shared.models import utcnow
    selected = load_dns(repo, account, snapshot_id,
                        source_restore_id=row.selection.get('source_restore_id'),
                        selected_zones=row.selection.get('dns_zones'))

    def save_previous(previous):
        path = work/'config-recovery.json'
        write_metadata(path, dict(format=1, account_id=account.id, username=account.username,
                                  restore_id=ident, dns_configuration=previous))
        result = storage.backup(repo, account.id, [str(path)])
        update(ident, safety_snapshot_id=result['snapshot_id'], progress_message='Restoring DNS records')

    completed = snapshot_dns.apply_configuration(account, selected, save_previous,
        lambda names: update(ident, summary={'config_sections': ['dns'], 'dns_zones': names}))
    update(ident, status='completed', summary={'config_sections': ['dns'], 'dns_zones': completed},
           progress_message='DNS records restored', completed_at=utcnow())


def configuration_catalog(repo, account, snapshot_id):
    """One decryption for all configuration previews; expose counts, not values."""
    from daemon import snapshot_php, snapshot_dns
    payload = _load_metadata(repo, account, snapshot_id)
    result = {'cron_available': False, 'php_available': False, 'dns_available': False, 'dns_zones': []}
    for section, key, validator in (
        ('cron', 'cron_configuration', lambda data: cron.validate_configuration(account.username, data)),
        ('php', 'php_configuration', lambda data: snapshot_php.validate_for_restore(account, data)),
    ):
        if key not in payload:
            result[section + '_reason'] = 'This recovery point has no complete saved settings for this section'
            continue
        try:
            saved = validator(payload[key])
        except ValidationError as exc:
            result[section + '_reason'] = str(exc)
            continue
        result[section + '_available'] = True
        if section == 'cron':
            result['managed_jobs'] = len(cron.parse_jobs(saved['lines']))
        else:
            result.update(php_sites=len(saved['sites']), php_default_version=saved['default_version'])
    dns = payload.get('dns_configuration')
    try:
        snapshot_dns.validate_for_restore(account, dns, [])
    except ValidationError as exc:
        result['dns_reason'] = str(exc) if dns is not None else 'This recovery point has no complete DNS settings'
        return result
    for zone in dns['zones']:
        item = dict(zone=zone['zone'], provider=zone.get('provider'), available=False)
        try:
            normalized = snapshot_dns.validate_for_restore(account, dns, [zone['zone']])
            item.update(available=True, record_count=len(normalized['zones'][0]['records']))
        except ValidationError as exc:
            item['reason'] = str(exc)
        result['dns_zones'].append(item)
    result['dns_available'] = any(zone['available'] for zone in result['dns_zones'])
    if not result['dns_available']:
        result['dns_reason'] = 'No DNS zones in this recovery point are currently available for restore'
    return result
