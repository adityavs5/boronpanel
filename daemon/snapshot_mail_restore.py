"""Account-authorized, private preparation for selected mailbox restores.

These functions do not alter SQL users, live Maildirs or Dovecot service state.
The coordinator must keep the account and repository locks throughout preparation
and recheck ownership before the later guarded exchange.
"""
from pathlib import Path
import uuid

from sqlalchemy import select

from daemon import mail, snapshot_jobs as jobs, snapshot_storage as storage
from daemon.snapshot_mail_files import build_maildir
from shared.config import settings
from shared.db import write_session
from shared.models import Account, MailDomain
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
