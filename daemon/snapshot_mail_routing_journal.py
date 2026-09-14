"""Private durable checkpoints for supervised SQL/Sieve routing application.

The coordinator owns account/repository locks and service supervision. This
worker never releases guards, retries a mutation, or marks a panel job complete.
"""
import json
import os
from pathlib import Path
import re
import secrets
import stat
import uuid

from daemon import snapshot_jobs as jobs, snapshot_mail_routing as routing, snapshot_mail_sieve as sieve
from daemon import snapshot_mail_routing_recovery as recovery, snapshot_mail_guard as guard
from daemon.database_operations import serialized, serialized_worker
from daemon.snapshot_mail_files import _placement_receipt
from daemon.snapshot_mail_journal import _path
from daemon.snapshot_mail_service import require_stopped
from shared.config import settings
from shared.validation import ValidationError

PHASES = {'prepared', 'guarding', 'guarded', 'applying_sql', 'sql_applied', 'applying_scripts',
          'verified', 'release_intent', 'completed'}
MAX_BYTES = 64 * 1024 * 1024


def _scripts(account, payload):
    return sieve._documents(account, payload, allow_empty=True)


def _rules(payload):
    result = []
    for entry in sorted(payload['domains'], key=lambda row: row['domain']):
        result.append(dict(entry, forwards=sorted(entry['forwards'], key=lambda row: (row['source_local_part'], row['destination'])),
                           autoresponders=sorted(entry['autoresponders'], key=lambda row: row['local_part'])))
    return dict(payload, domains=result)


def _validate(account, payload):
    if (not isinstance(payload, dict) or type(payload.get('format')) is not int or payload['format'] != 1
            or type(payload.get('account_id')) is not int or payload['account_id'] != account.id
            or payload.get('username') != account.username or not isinstance(payload.get('phase'), str)
            or payload['phase'] not in PHASES):
        raise ValidationError('Invalid private mail-routing journal identity or phase')
    if payload.get('direction', 'restore') not in ('restore', 'rollback'):
        raise ValidationError('Invalid mail-routing journal direction')
    if payload.get('direction') == 'rollback' and (not isinstance(payload.get('source_operation_id'), str)
            or not re.fullmatch(r'[a-f0-9]{32}', payload['source_operation_id'])):
        raise ValidationError('Invalid rollback source operation')
    recovery._job_id(payload.get('restore_id'))
    for key, pattern in [('operation_id', r'[a-f0-9]{32}'), ('safety_snapshot_id', r'[a-f0-9]{64}')]:
        if not isinstance(payload.get(key), str) or not re.fullmatch(pattern, payload[key]):
            raise ValidationError('Invalid mail-routing operation or safety identifier')
    desired = recovery._validate(account, dict(routing=payload.get('desired_routing'), scripts=payload.get('desired_scripts')))
    previous = recovery._validate(account, payload.get('previous'))
    if {row['domain'] for row in desired['routing']['domains']} != {row['domain'] for row in previous['routing']['domains']}:
        raise ValidationError('Mail-routing journal domain selection changed')
    keys = _scripts(account, desired['scripts']).keys()
    if keys != _scripts(account, previous['scripts']).keys():
        raise ValidationError('Mail-routing journal script selection changed')
    entries = payload.get('guards')
    if not isinstance(entries, list) or len(entries) != len(keys):
        raise ValidationError('Mail-routing journal guards do not match scripts')
    seen = set()
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get('domain'), str)
                or not isinstance(entry.get('local_part'), str) or not isinstance(entry.get('token'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', entry['token'])):
            raise ValidationError('Invalid private routing guard entry')
        seen.add((entry['domain'], entry['local_part']))
    if seen != keys:
        raise ValidationError('Mail-routing journal guards do not match scripts')
    completed = payload.get('completed_scripts')
    addresses = {local + '@' + domain for domain, local in keys}
    if (not isinstance(completed, list) or any(not isinstance(address, str) for address in completed)
            or len(set(completed)) != len(completed) or not set(completed) <= addresses):
        raise ValidationError('Invalid mail-routing script checkpoints')
    if (payload['phase'] not in ('applying_scripts', 'verified', 'release_intent', 'completed') and completed
            or payload['phase'] in ('verified', 'release_intent', 'completed') and set(completed) != addresses):
        raise ValidationError('Mail-routing script checkpoints do not match the journal phase')
    return payload


def _write(path, payload, *, create=False):
    if len(json.dumps(payload, ensure_ascii=True).encode()) > MAX_BYTES:
        raise ValidationError('Mail-routing journal is too large')
    _placement_receipt(_path(path), payload, create=create)


def read(account, path):
    path = _path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > MAX_BYTES:
            raise ValidationError('Unsafe private mail-routing journal')
        try:
            payload = json.loads(handle.read(MAX_BYTES + 1))
        except (ValueError, UnicodeError):
            raise ValidationError('Invalid private mail-routing journal') from None
    payload = _validate(account, payload)
    filename = 'mail-routing-rollback-operation.json' if payload.get('direction') == 'rollback' else 'mail-routing-operation.json'
    expected = Path(settings.snapshot_private_dir) / 'restores' / f"restore-{payload['restore_id']}" / filename
    if path != expected:
        raise ValidationError('Mail-routing journal path does not match its job')
    return payload


@serialized_worker
def create(repo, account, restore_id, plan, safety_snapshot_id):
    """Prove encrypted prior state exists before publishing any guard tokens."""
    restore_id = recovery._job_id(restore_id)
    saved = recovery.load_previous(repo, account, safety_snapshot_id, restore_id)
    if saved != recovery._validate(account, plan['previous']):
        raise ValidationError('Encrypted mail-routing safety does not match this operation')
    scripts = _scripts(account, plan['desired_scripts'])
    payload = dict(format=1, account_id=account.id, username=account.username, restore_id=restore_id,
        operation_id=uuid.uuid4().hex, safety_snapshot_id=safety_snapshot_id, phase='prepared',
        desired_routing=plan['desired_routing'], desired_scripts=plan['desired_scripts'], previous=saved,
        guards=[dict(domain=domain, local_part=local, token=secrets.token_hex(32)) for domain, local in sorted(scripts)],
        completed_scripts=[])
    _validate(account, payload)
    path = jobs.private_directory('restores', f'restore-{restore_id}') / 'mail-routing-operation.json'
    _write(path, payload, create=True)
    return path


@serialized_worker
def acquire_guards(account, path):
    """Resume guard acquisition using already persisted ownership tokens only."""
    payload = read(account, path)
    if payload['phase'] not in ('prepared', 'guarding'):
        raise ValidationError('Mail-routing guard acquisition requires recovery inspection')
    payload['phase'] = 'guarding'; _write(path, payload)
    for entry in payload['guards']:
        try:
            with guard.owned_guards([entry], payload['restore_id']): pass
        except FileNotFoundError:
            guard.block(entry['domain'], entry['local_part'], payload['restore_id'], token=entry['token'])
    payload['phase'] = 'guarded'; _write(path, payload)
    return {'mailboxes': len(payload['guards'])}


def _observed(account, payload):
    actual = routing.capture(account, [row['domain'] for row in payload['desired_routing']['domains']])
    addresses = [entry['local_part'] + '@' + entry['domain'] for entry in payload['guards']]
    scripts = (_scripts(account, sieve.capture(account, addresses)) if addresses else {})
    desired_scripts = _scripts(account, payload['desired_scripts'])
    previous_scripts = _scripts(account, payload['previous']['scripts'])
    return dict(sql_matches_desired=_rules(actual) == _rules(payload['desired_routing']),
                sql_matches_previous=_rules(actual) == _rules(payload['previous']['routing']),
                scripts_match_desired=scripts == desired_scripts, scripts_match_previous=scripts == previous_scripts)


@serialized_worker
def inspect(account, path):
    """Read actual state, not checkpoint claims. Caller first proves worker exit."""
    payload = read(account, path)
    with guard.owned_guards(payload['guards'], payload['restore_id']):
        return dict(phase=payload['phase'], **_observed(account, payload))


@serialized
def execute(account, path, *, service='dovecot.service'):
    """Fresh offline application only; a partial journal cannot be replayed."""
    payload = read(account, path)
    _not_superseded(path, payload)
    if payload['phase'] != 'guarded':
        raise ValidationError('Mail-routing operation requires inspection, not replay')
    require_stopped(service)
    with guard.owned_guards(payload['guards'], payload['restore_id']):
        observed = _observed(account, payload)
        if not observed['sql_matches_previous'] or not observed['scripts_match_previous']:
            raise ValidationError('Mail-routing state changed after safety capture')
        payload['phase'] = 'applying_sql'; _write(path, payload)
        routing._replace_sql(account, payload['desired_routing'])
        payload['phase'] = 'sql_applied'; _write(path, payload)
    payload['phase'] = 'applying_scripts'; _write(path, payload)
    def checkpoint(address):
        payload['completed_scripts'].append(address)
        _write(path, payload)
    if payload['guards']:
        sieve._apply(account, payload['desired_scripts'], payload['previous']['scripts'],
                     payload['restore_id'], payload['guards'], checkpoint)
    observed = _observed(account, payload)
    if not observed['sql_matches_desired'] or not observed['scripts_match_desired']:
        raise ValidationError('Mail-routing application could not be verified; guards retained')
    payload['phase'] = 'verified'; _write(path, payload)
    return {'domains': [entry['domain'] for entry in payload['desired_routing']['domains']],
            'mailboxes': len(payload['guards'])}


@serialized_worker
def _ready_for_launch(account, path):
    payload = read(account, path)
    _not_superseded(path, payload)
    if payload['phase'] != 'guarded':
        raise ValidationError('Mail-routing operation requires inspection, not replay')
    with guard.owned_guards(payload['guards'], payload['restore_id']):
        observed = _observed(account, payload)
        if not observed['sql_matches_previous'] or not observed['scripts_match_previous']:
            raise ValidationError('Mail-routing state changed before supervised application')
    return payload


def launch(account, path):
    """Caller holds account/repository locks, but must release the SQL lock.

    The independent worker acquires SQL coordination itself. Contention makes it
    fail promptly, so it cannot wait on another backup with Dovecot stopped.
    An observation failure requires inspection of this same operation/unit.
    """
    import sys
    from daemon.snapshot_mail_guard_config import verify
    from daemon.snapshot_mail_service import supervised_command
    verify()
    payload = _ready_for_launch(account, path)
    if payload.get('direction') == 'rollback':
        from daemon.snapshot_mail_service import retire_switch
        retire_switch(payload['source_operation_id'])
    return supervised_command([sys.executable, '-m', 'daemon.snapshot_mail_routing_journal',
                               str(account.id), str(_path(path))], payload['operation_id'])


def recovery_state(account, path, *, service='dovecot.service'):
    """Check the existing worker before interpreting observed routing state."""
    from daemon.snapshot_mail_service import inspect_switch
    payload = read(account, path)
    replacement = Path(path).with_name('mail-routing-rollback-operation.json')
    if payload.get('direction', 'restore') == 'restore' and replacement.exists():
        return recovery_state(account, replacement, service=service)
    if payload['phase'] == 'completed':
        return {'state': 'completed', 'guards_released': True, 'safety_snapshot_id': payload['safety_snapshot_id'],
                'rolled_back': payload.get('direction') == 'rollback'}
    worker = inspect_switch(payload['operation_id'], service=service)
    if worker['state'] == 'running':
        return {'state': 'waiting', 'worker': worker}
    if payload['phase'] == 'release_intent':
        # Some/all markers may already be gone. Finalization owns the remaining
        # token checks; ordinary inspection requires every guard to be present.
        return {'state': 'finalizing', 'worker': worker, 'phase': payload['phase']}
    return {'state': 'observed', 'worker': worker, **inspect(account, path)}


@serialized_worker
def finalize(repo, account, path, *, service='dovecot.service'):
    """Verify recovery before durably authorizing guard release.

    Caller holds account/repository locks. Resume an interrupted release only
    while actual state still matches the verified operation. Never overwrite
    changes made after that operation or release another job's guard.
    """
    from daemon.snapshot_mail_service import inspect_switch, service_status
    payload = read(account, path)
    _not_superseded(path, payload)
    result = {'state': 'completed', 'safety_snapshot_id': payload['safety_snapshot_id'],
              'domains': [entry['domain'] for entry in payload['desired_routing']['domains']],
              'mailboxes': len(payload['guards']), 'guards_released': True,
              'rolled_back': payload.get('direction') == 'rollback'}
    if payload['phase'] == 'completed':
        return result
    if payload['phase'] not in ('verified', 'release_intent'):
        raise ValidationError('Mail-routing application needs recovery before finalization')
    worker = inspect_switch(payload['operation_id'], service=service)
    if worker['state'] == 'running':
        return {'state': 'waiting', 'worker': worker}
    state = service_status(service)
    if state.get('ActiveState') != 'active' or state.get('SubState') != 'running':
        raise ValidationError('Mail service has not resumed; routing guards retained')
    kwargs = {'kind': 'rollback'} if payload.get('direction') == 'rollback' else {}
    previous = recovery.load_previous(repo, account, payload['safety_snapshot_id'], payload['restore_id'], **kwargs)
    if previous != payload['previous']:
        raise ValidationError('Encrypted routing safety changed; routing guards retained')
    observed = _observed(account, payload)
    if not observed['sql_matches_desired'] or not observed['scripts_match_desired']:
        raise ValidationError('Mail-routing state changed before finalization; inspect recovery')
    if payload['phase'] == 'verified':
        with guard.owned_guards(payload['guards'], payload['restore_id']):
            payload['phase'] = 'release_intent'
            _write(path, payload)
    # release_batch validates every remaining token before the first unlink and
    # accepts missing markers only because release intent is already durable.
    guard.release_batch(payload['guards'], payload['restore_id'])
    payload['phase'] = 'completed'; _write(path, payload)
    return result


def _not_superseded(path, payload):
    replacement = Path(path).with_name('mail-routing-rollback-operation.json')
    if payload.get('direction', 'restore') == 'restore' and (replacement.exists() or replacement.is_symlink()):
        raise ValidationError('Use the prepared rollback journal; the original operation is superseded')


@serialized_worker
def prepare_rollback(repo, account, path, *, service='dovecot.service'):
    """Encrypt the observed partial state and prepare an explicit reverse operation.

    Caller retains account/repository locks. Only known original/desired states
    may be reversed; later unrelated edits require inspection instead. Existing
    guard tokens remain owned by the same job throughout reversal.
    """
    from daemon.snapshot_mail_service import inspect_switch
    original = read(account, path)
    if original.get('direction', 'restore') != 'restore' or original['phase'] not in (
            'guarded', 'applying_sql', 'sql_applied', 'applying_scripts', 'verified'):
        raise ValidationError('This mail-routing operation cannot prepare a fresh rollback')
    _not_superseded(path, original)
    worker = inspect_switch(original['operation_id'], service=service)
    if worker['state'] == 'running':
        raise ValidationError('Mail-routing worker is still running; wait before rollback')
    target = recovery.load_previous(repo, account, original['safety_snapshot_id'], original['restore_id'])
    if target != original['previous']:
        raise ValidationError('Original encrypted safety does not match rollback target')
    with guard.owned_guards(original['guards'], original['restore_id']):
        actual_rules = routing.capture(account, [entry['domain'] for entry in original['desired_routing']['domains']])
        if _rules(actual_rules) not in (_rules(original['previous']['routing']), _rules(original['desired_routing'])):
            raise ValidationError('Routing has unrelated changes; rollback requires inspection')
        addresses = [entry['local_part'] + '@' + entry['domain'] for entry in original['guards']]
        actual_scripts = (sieve.capture(account, addresses) if addresses else
                          dict(format=1, account_id=account.id, username=account.username, scripts=[]))
        current = _scripts(account, actual_scripts)
        old, desired = _scripts(account, original['previous']['scripts']), _scripts(account, original['desired_scripts'])
        if any(content not in (old[key], desired[key]) for key, content in current.items()):
            raise ValidationError('Scripts have unrelated changes; rollback requires inspection')
        for content in _scripts(account, target['scripts']).values():
            if content is not None:
                sieve._compile_bytes(content)
        previous = recovery._validate(account, dict(routing=actual_rules, scripts=actual_scripts))
        saved = recovery.save_previous(repo, account, original['restore_id'], previous, kind='rollback')
        # Confirm the encrypted observation before authorizing the reverse write.
        if recovery.load_previous(repo, account, saved['snapshot_id'], original['restore_id'], kind='rollback') != previous:
            raise ValidationError('Encrypted rollback observation could not be verified')
        payload = dict(format=1, account_id=account.id, username=account.username,
            restore_id=original['restore_id'], operation_id=uuid.uuid4().hex, direction='rollback',
            source_operation_id=original['operation_id'], safety_snapshot_id=saved['snapshot_id'],
            phase='guarded', desired_routing=target['routing'], desired_scripts=target['scripts'],
            previous=previous, guards=original['guards'], completed_scripts=[])
        _validate(account, payload)
        destination = Path(path).with_name('mail-routing-rollback-operation.json')
        _write(destination, payload, create=True)
    return destination


if __name__ == '__main__':
    import sys
    from shared.models import Account
    try:
        if len(sys.argv) != 3 or not sys.argv[1].isdigit():
            raise ValidationError('An account and private routing journal are required')
        account = jobs._row(Account, int(sys.argv[1]))
        from daemon.snapshot_mail_guard_config import verify
        verify()
        execute(account, sys.argv[2])
    except Exception:
        print('Mail-routing application failed; inspect private recovery state.', file=sys.stderr)
        raise SystemExit(1)
