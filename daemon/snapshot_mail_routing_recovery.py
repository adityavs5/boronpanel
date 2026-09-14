"""Encrypted routing and exact-script safety bundles for the restore coordinator.

No live routing or scripts are changed here. The coordinator retains account,
repository and SQL mutation locks through safety capture, encryption and apply.
"""
import base64
import json
import os
from pathlib import Path
import shutil
import stat
import uuid

from daemon import snapshot_jobs as jobs, snapshot_storage as storage
from daemon import snapshot_mail_routing as routing, snapshot_mail_sieve as sieve
from daemon.database_operations import serialized_worker
from shared.config import settings
from shared.validation import ValidationError

MAX_BUNDLE_BYTES = 24 * 1024 * 1024


def _safety_name(kind):
    if kind == 'restore':
        return 'mail-routing-recovery.json'
    if kind == 'rollback':
        return 'mail-routing-rollback-safety.json'
    raise ValidationError('Invalid mail-routing safety purpose')


def _job_id(value):
    if type(value) is not int or value <= 0:
        raise ValidationError('Invalid mail-routing recovery job')
    return value


def _validate(account, bundle):
    if not isinstance(bundle, dict) or set(bundle) != {'routing', 'scripts'}:
        raise ValidationError('Incomplete mail-routing safety bundle')
    rules = routing.validate_for_restore(account, bundle['routing'])
    if not rules['domains']:
        raise ValidationError('Mail-routing safety requires selected domains')
    scripts = sieve._documents(account, bundle['scripts'], allow_empty=True)
    domains = {entry['domain'] for entry in rules['domains']}
    if any(domain not in domains for domain, _ in scripts):
        raise ValidationError('Safety scripts are outside the selected routing domains')
    responders = {(entry['domain'], reply['local_part']) for entry in rules['domains']
                  for reply in entry['autoresponders']}
    if not responders <= scripts.keys():
        raise ValidationError('Mail-routing safety is missing an automatic-reply script')
    script_document = dict(format=1, account_id=account.id, username=account.username, scripts=[
        dict(domain=domain, local_part=local,
             script_base64=base64.b64encode(content).decode('ascii') if content is not None else None)
        for (domain, local), content in sorted(scripts.items())])
    return dict(routing=rules, scripts=script_document)


@serialized_worker
def prepare(account, desired):
    """Capture current selected routing and actual affected scripts together."""
    desired = routing.validate_for_restore(account, desired)
    current = routing.capture(account, [entry['domain'] for entry in desired['domains']])
    planned = sieve.prepare_changes(account, desired, current)
    addresses = [entry['local_part'] + '@' + entry['domain'] for entry in planned['scripts']]
    previous_scripts = (sieve.capture(account, addresses) if addresses else
                        dict(format=1, account_id=account.id, username=account.username, scripts=[]))
    return dict(desired_routing=desired, desired_scripts=planned,
                previous=_validate(account, dict(routing=current, scripts=previous_scripts)))


@serialized_worker
def save_previous(repo, account, restore_id, bundle, *, kind='restore'):
    """Persist a fresh immutable safety source and encrypt it before activation.

    A failed/uncertain backup retains the private source. The coordinator records
    the returned snapshot identifier durably before any guard or live mutation.
    Do not blindly repeat this function for the same job after interruption.
    """
    restore_id = _job_id(restore_id)
    filename = _safety_name(kind)
    selected = _validate(account, bundle)
    payload = dict(format=1, account_id=account.id, username=account.username,
                   restore_id=restore_id, safety_kind=kind, **selected)
    content = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode('utf-8')
    if len(content) > MAX_BUNDLE_BYTES:
        raise ValidationError('Mail-routing safety bundle is too large')
    work = jobs.private_directory('restores', f'restore-{restore_id}')
    path = work / filename
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(content); handle.flush(); os.fsync(handle.fileno())
    directory = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    result = storage.backup(repo, account.id, [str(path)])
    return {'snapshot_id': result['snapshot_id']}


def _read(path):
    if path.resolve() != path:
        raise ValidationError('Unsafe private mail-routing safety path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
                or info.st_size > MAX_BUNDLE_BYTES):
            raise ValidationError('Unsafe private mail-routing safety file')
        try:
            return json.loads(handle.read(MAX_BUNDLE_BYTES + 1))
        except (ValueError, UnicodeError):
            raise ValidationError('Invalid private mail-routing safety data') from None


def load_previous(repo, account, snapshot_id, restore_id, *, kind='restore'):
    """Read an account- and job-bound safety copy; never return it through RPC."""
    restore_id = _job_id(restore_id)
    filename = _safety_name(kind)
    snapshot = storage.owned_snapshot(repo, account.id, snapshot_id)
    source = Path(settings.snapshot_private_dir) / 'restores' / f'restore-{restore_id}' / filename
    if snapshot.get('paths') != [str(source)]:
        raise ValidationError('Mail-routing safety paths do not match the source job')
    work = jobs.private_directory('mail-routing-metadata', uuid.uuid4().hex)
    try:
        data = storage.restore_to(repo, account.id, snapshot_id, str(work / 'data'), selected_paths=[str(source)])
        payload = _read(data / str(source).lstrip('/'))
        if (not isinstance(payload, dict) or type(payload.get('format')) is not int or payload['format'] != 1
                or type(payload.get('account_id')) is not int or payload['account_id'] != account.id
                or payload.get('username') != account.username or type(payload.get('restore_id')) is not int
                or payload['restore_id'] != restore_id or payload.get('safety_kind', 'restore') != kind):
            raise ValidationError('Mail-routing safety belongs to another account or job')
        return _validate(account, dict(routing=payload.get('routing'), scripts=payload.get('scripts')))
    finally:
        shutil.rmtree(work)


def load_routing(repo, account, snapshot_id, selected_domains):
    """Load existing mail backup metadata after checking snapshot ownership."""
    storage.owned_snapshot(repo, account.id, snapshot_id)
    source = Path(settings.snapshot_private_dir) / 'sources' / f'account-{account.id}' / 'mail-recovery.json'
    work = jobs.private_directory('mail-routing-metadata', uuid.uuid4().hex)
    try:
        data = storage.restore_to(repo, account.id, snapshot_id, str(work / 'data'), selected_paths=[str(source)])
        return routing.read_routing(data / str(source).lstrip('/'), account, selected_domains)
    finally:
        shutil.rmtree(work)
