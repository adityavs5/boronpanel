"""Account-authorized, private preparation for selected mailbox restores.

These functions do not alter SQL users, live Maildirs or Dovecot service state.
The coordinator must keep the account and repository locks throughout preparation
and recheck ownership before the later guarded exchange.
"""
from pathlib import Path
import json
import os
import stat
import secrets
import uuid

from sqlalchemy import select

from daemon import mail, snapshot_jobs as jobs, snapshot_storage as storage
from daemon.snapshot_mail_files import build_maildir
from shared.config import settings
from shared.db import write_session
from shared.models import Account, MailDomain
from daemon.database_operations import serialized_worker
from shared.validation import ValidationError, validate_domain, validate_mailbox_local_part


def selected_mailboxes(account, metadata, addresses):
    """Return private restore entries after checking every selected domain owner."""
    if not isinstance(addresses, list) or not 1 <= len(addresses) <= 1000:
        raise ValidationError('Select between one and 1000 mailboxes')
    selected = set()
    for address in addresses:
        if not isinstance(address, str) or address.count('@') != 1:
            raise ValidationError('Invalid selected mailbox address')
        local, domain = address.split('@')
        selected.add((validate_domain(domain), validate_mailbox_local_part(local)))
    with write_session() as session:
        current = session.get(Account, account.id)
        if current is None or current.username != account.username or current.status != 'active':
            raise ValidationError('Account is no longer active')
        owners = {row.domain: row.account_id for row in session.scalars(
            select(MailDomain).where(MailDomain.domain.in_({domain for domain, _ in selected})))}
    # Reject the whole selection before looking up any mail-domain SQL users.
    for domain, local in selected:
        if owners.get(domain) != account.id:
            raise ValidationError('Selected mail domain is no longer owned by this account')
        if local not in metadata.get(domain, {}).get('mailboxes', {}):
            raise ValidationError('Selected mailbox is missing from recovery metadata')
    current_mailboxes = {domain: {entry['local_part'] for entry in mail.list_mailboxes(domain)}
                         for domain in sorted({domain for domain, _ in selected})}
    return [dict(domain=domain, local_part=local,
                 action='existing' if local in current_mailboxes[domain] else 'recreate',
                 metadata=metadata[domain]['mailboxes'][local]) for domain, local in sorted(selected)]


def prepare(account, repo, snapshot_id, addresses):
    """Decrypt and rebuild selected mail in exclusive private staging.

    Result includes private password hashes needed only for deleted mailboxes;
    never serialize it into API responses, public job summaries or logs.
    Caller owns cleanup and must persist placement receipts before live staging.
    """
    from daemon.snapshot_restores import _mail_recovery_metadata
    storage.owned_snapshot(repo, account.id, snapshot_id)
    metadata = _mail_recovery_metadata(repo, account, snapshot_id)
    entries = selected_mailboxes(account, metadata, addresses)
    paths = [Path(settings.mail_base) / entry['domain'] / entry['local_part'] / 'Maildir'
             for entry in entries]
    # Missing trees may reflect a backup filter rather than an empty mailbox.
    # Never turn that absence into an instruction to erase existing messages.
    for path in paths:
        nodes = storage.entries(repo, account.id, snapshot_id, str(path))
        if not any(node.get('path') == str(path) and node.get('type') == 'dir' for node in nodes):
            raise ValidationError('Selected mailbox has no Maildir in this recovery point')
    work = jobs.private_directory('mail-preparation', uuid.uuid4().hex)
    try:
        data = storage.restore_to(repo, account.id, snapshot_id, str(work / 'data'),
                                  selected_paths=[str(path) for path in paths])
        for index, (entry, path) in enumerate(zip(entries, paths)):
            destination = work / ('ready-' + str(index))
            build_maildir(data / str(path).lstrip('/'), destination, work)
            entry['prepared'] = str(destination)
        return {'work': str(work), 'entries': entries}
    except Exception:
        import shutil
        shutil.rmtree(work)
        raise


def stage(account, prepared, restore_id):
    """Place prepared mail beside live Maildirs with a durable batch inventory.

    Caller owns account/repository locks and has provisioned missing mailbox
    homes. On failure retain the work directory and inventory for recovery;
    earlier copies may have completed. Never repeat this call for that work dir.
    """
    from daemon.snapshot_mail_files import stage_for_exchange, _placement_receipt
    from daemon.snapshot_mail_journal import _path
    if type(restore_id) is not int or restore_id <= 0:
        raise ValidationError('Invalid mailbox restore identifier')
    work = Path(prepared['work'])
    index = _path(work / 'placement-index.json')
    entries = prepared['entries']
    metadata = {}
    for entry in entries:
        metadata.setdefault(entry['domain'], {'mailboxes': {}})['mailboxes'][entry['local_part']] = entry['metadata']
    addresses = [entry['local_part'] + '@' + entry['domain'] for entry in entries]
    selected_mailboxes(account, metadata, addresses)
    inventory = []
    for position, entry in enumerate(entries):
        source = _path(Path(entry['prepared']))
        if not source.is_relative_to(work) or not source.is_dir():
            raise ValidationError('Prepared mailbox is outside its recovery work directory')
        inventory.append({'domain': entry['domain'], 'local_part': entry['local_part'],
                          'source': str(source), 'prepared': '.boron-mail-ready-' + uuid.uuid4().hex,
                          'receipt': str(work / ('placement-' + str(position) + '.json'))})
    # Exclusive creation is also the retry barrier. Inventory survives a crash
    # before the first receipt or between two independently durable copies.
    _placement_receipt(index, dict(format=1, account_id=account.id, restore_id=restore_id,
                                   entries=inventory), create=True)
    for entry in inventory:
        stage_for_exchange(entry['source'], work, entry['domain'], entry['local_part'],
                           prepared=entry['prepared'], receipt=entry['receipt'], restore_id=restore_id)
    return {'index': str(index), 'entries': inventory}


def acquire_guards(account, prepared, restore_id):
    """Persist recovery ownership before publishing any mailbox lookup guard.

    Keep the private work directory after every failure: some guards may already
    be active. The inventory permits recovery to verify precisely those tokens.
    """
    from daemon import snapshot_mail_guard as guard
    from daemon.snapshot_mail_files import _placement_receipt
    from daemon.snapshot_mail_journal import _path
    if type(restore_id) is not int or restore_id <= 0:
        raise ValidationError('Invalid mailbox restore identifier')
    index = _path(Path(prepared['work']) / 'guard-index.json')
    metadata = {}
    for entry in prepared['entries']:
        metadata.setdefault(entry['domain'], {'mailboxes': {}})['mailboxes'][entry['local_part']] = entry['metadata']
    selected = selected_mailboxes(account, metadata, [entry['local_part'] + '@' + entry['domain']
                                                     for entry in prepared['entries']])
    entries = [dict(domain=entry['domain'], local_part=entry['local_part'], token=secrets.token_hex(32))
               for entry in selected]
    _placement_receipt(index, dict(format=1, account_id=account.id, restore_id=restore_id,
                                   entries=entries), create=True)
    for entry in entries:
        guard.block(entry['domain'], entry['local_part'], restore_id, token=entry['token'])
    return {'index': str(index), 'entries': entries}


@serialized_worker
def provision_mailboxes(account, prepared, acquired, restore_id):
    """Initialize guarded mail storage and recreate only absent SQL mailboxes.

    The durable provisioning intent survives SQL/cache failures. Existing users
    keep their current credentials, quota and active status. Caller retains the
    account lock; interrupted attempts require inspection rather than replay.
    """
    from daemon import snapshot_mail_guard as guard, snapshot_mail_metadata
    from daemon.snapshot_mail_files import initialize_maildir, _placement_receipt
    from daemon.snapshot_mail_journal import _path
    from shared.models import MailUser
    metadata = {}
    for entry in prepared['entries']:
        metadata.setdefault(entry['domain'], {'mailboxes': {}})['mailboxes'][entry['local_part']] = entry['metadata']
    selected = selected_mailboxes(account, metadata, [entry['local_part'] + '@' + entry['domain']
                                                     for entry in prepared['entries']])
    tokens = {(entry['domain'], entry['local_part']): entry['token'] for entry in acquired['entries']}
    if set(tokens) != {(entry['domain'], entry['local_part']) for entry in selected}:
        raise ValidationError('Mailbox guard selection does not match preparation')
    with guard.owned_guards(acquired['entries'], restore_id):
        pass
    receipt = _path(Path(prepared['work']) / 'provisioning.json')
    record = dict(format=1, account_id=account.id, restore_id=restore_id, status='planned',
                  mailboxes=[dict(domain=entry['domain'], local_part=entry['local_part'], action=entry['action'])
                             for entry in selected])
    _placement_receipt(receipt, record, create=True)
    for entry in selected:
        domain, local = entry['domain'], entry['local_part']
        initialize_maildir(domain, local, restore_id, tokens[domain, local])
        with guard.owned_guards(acquired['entries'], restore_id):
            if entry['action'] == 'recreate':
                snapshot_mail_metadata.recreate_mailbox(domain, entry['metadata'])
            actual = next((row for row in mail.list_mailboxes(domain) if row['local_part'] == local), None)
            if actual is None:
                raise ValidationError('Mailbox provisioning did not produce a SQL mailbox')
            with write_session() as session:
                owned = session.scalar(select(MailDomain).where(MailDomain.domain == domain,
                                                               MailDomain.account_id == account.id))
                if owned is None:
                    raise ValidationError('Mail domain ownership changed during provisioning')
                cached = session.scalar(select(MailUser).where(MailUser.domain == domain, MailUser.local_part == local))
                if cached is not None and cached.mail_domain_id != owned.id:
                    raise ValidationError('Mailbox cache belongs to a different domain registration')
                if cached is None:
                    cached = MailUser(mail_domain_id=owned.id, domain=domain, local_part=local)
                    session.add(cached)
                cached.quota_mb = actual['quota_mb']
    record['status'] = 'completed'
    _placement_receipt(receipt, record)
    return {'mailboxes': record['mailboxes']}


def inspect_staging(account, work, restore_id):
    """Read interrupted placement state after the caller proves worker termination.

    This does not authorize cleanup, retry or exchange. Caller holds the account
    lock and must separately establish that no placement worker is still copying.
    """
    from daemon.snapshot_mail_journal import _path
    from daemon.snapshot_mail_exchange import _prepared_name, _home
    from daemon.snapshot_mail_files import inspect_placement
    work = Path(work)
    index = _path(work / 'placement-index.json')
    fd = os.open(index, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 1024*1024:
            raise ValidationError('Invalid mailbox placement inventory')
        try:
            payload = json.loads(handle.read(1024*1024+1))
            if (type(restore_id) is not int or restore_id <= 0 or type(payload['format']) is not int
                    or payload['format'] != 1 or type(payload['account_id']) is not int
                    or payload['account_id'] != account.id or type(payload['restore_id']) is not int
                    or payload['restore_id'] != restore_id):
                raise ValueError()
            entries = payload['entries']
            if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
                raise ValueError()
            seen = set()
            for position, entry in enumerate(entries):
                address = (validate_domain(entry['domain']), validate_mailbox_local_part(entry['local_part']))
                if address in seen or address != (entry['domain'], entry['local_part']):
                    raise ValueError()
                seen.add(address)
                _prepared_name(entry['prepared'])
                if entry['receipt'] != str(work / ('placement-' + str(position) + '.json')):
                    raise ValueError()
        except (KeyError, ValueError, TypeError, UnicodeError):
            raise ValidationError('Invalid mailbox placement inventory') from None
    with write_session() as session:
        current = session.get(Account, account.id)
        if current is None or current.username != account.username:
            raise ValidationError('Restore account no longer exists')
        owners = {row.domain: row.account_id for row in session.scalars(
            select(MailDomain).where(MailDomain.domain.in_({domain for domain, _ in seen})))}
    if any(owners.get(domain) != account.id for domain, _ in seen):
        raise ValidationError('Selected mail domain is no longer owned by this account')
    result = []
    for entry in entries:
        receipt = Path(entry['receipt'])
        if receipt.exists() or receipt.is_symlink():
            state = inspect_placement(receipt, work, expected=dict(entry, restore_id=restore_id))
        else:
            with _home(entry['domain'], entry['local_part']) as home:
                try:
                    os.stat(entry['prepared'], dir_fd=home, follow_symlinks=False)
                    state = 'unconfirmed'
                except FileNotFoundError:
                    state = 'not_started'
        result.append({'domain': entry['domain'], 'local_part': entry['local_part'], 'state': state})
    return {'mailboxes': result}


def create_switch(account, work, acquired, restore_id):
    """Bind verified staging and guard ownership into the executable journal.

    Caller holds the account lock. This only records identities; the supervised
    worker must stop Dovecot and validate them again before exchanging anything.
    """
    from daemon import snapshot_mail_guard as guard, snapshot_mail_journal as journal
    from daemon import snapshot_mail_exchange as exchange
    work = Path(work)
    observed = inspect_staging(account, work, restore_id)
    if any(entry['state'] != 'ready' for entry in observed['mailboxes']):
        raise ValidationError('All selected mailbox copies must be ready before switching')
    # inspect_staging has already validated the bounded, private inventory and
    # current ownership. The account lock keeps this work directory single-writer.
    inventory = json.loads(journal._path(work / 'placement-index.json').read_text())
    tokens = {(entry['domain'], entry['local_part']): entry['token'] for entry in acquired['entries']}
    addresses = {(entry['domain'], entry['local_part']) for entry in inventory['entries']}
    if set(tokens) != addresses or len(acquired['entries']) != len(addresses):
        raise ValidationError('Mailbox guards do not match the staged selection')
    with guard.owned_guards(acquired['entries'], restore_id):
        entries = [dict(domain=entry['domain'], local_part=entry['local_part'],
                        token=tokens[entry['domain'], entry['local_part']],
                        plan=exchange.plan(entry['domain'], entry['local_part'], entry['prepared']))
                   for entry in inventory['entries']]
        return journal.create(work / 'switch.json', dict(format=1, restore_id=restore_id,
                              operation_id=uuid.uuid4().hex, undo=False, entries=entries))
