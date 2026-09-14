"""Private capture of active Sieve scripts for routing recovery and exact undo.

Only fixed mailbox script paths are read. This module does not activate scripts;
its caller must hold the account lock and encrypt the result before live changes.
"""
import base64
from contextlib import contextmanager
import os
from pathlib import Path
import stat

from sqlalchemy import select

from daemon import mail
from daemon.database_operations import serialized_worker
from shared.config import settings
from shared.db import write_session
from shared.models import Account, MailDomain
from shared.validation import ValidationError, validate_domain, validate_mailbox_local_part

SCRIPT_NAME = '.dovecot.sieve'
MAX_SCRIPT_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024


def _selection(account, addresses):
    if (not isinstance(addresses, list) or not 1 <= len(addresses) <= 1000
            or any(not isinstance(address, str) for address in addresses)):
        raise ValidationError('Select automatic-reply mailboxes for recovery')
    selected = []
    for address in addresses:
        local, separator, domain = address.rpartition('@')
        if not separator:
            raise ValidationError('Invalid automatic-reply mailbox selection')
        selected.append((validate_domain(domain), validate_mailbox_local_part(local)))
    if len(set(selected)) != len(selected):
        raise ValidationError('Duplicate automatic-reply mailbox selection')
    with write_session() as session:
        current = session.get(Account, account.id)
        if current is None or current.username != account.username or current.status != 'active':
            raise ValidationError('Account is no longer available for automatic-reply recovery')
        domains = set(session.scalars(select(MailDomain.domain).where(MailDomain.account_id == account.id)).all())
    if any(domain not in domains for domain, _ in selected):
        raise ValidationError('Automatic-reply mailbox belongs to another account')
    users = {domain: {row['local_part'] for row in mail.list_mailboxes(domain)}
             for domain in {domain for domain, _ in selected}}
    if any(local not in users[domain] for domain, local in selected):
        raise ValidationError('Restore the missing mailbox before its automatic reply')
    return sorted(selected)


@contextmanager
def _home(domain, local):
    """Traverse fixed roots with directory descriptors; never follow symlinks."""
    base = Path(settings.mail_base)
    if not base.is_absolute() or '..' in base.parts or base == Path('/'):
        raise ValidationError('Invalid mail storage root')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open('/', flags)
    try:
        for component in base.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid not in (0, mail.VMAIL_UID) or info.st_mode & 0o022:
            raise ValidationError('Mail storage root has unsafe ownership or permissions')
        for index, component in enumerate((domain, local)):
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if index == 1:
                    # Dovecot creates mailbox homes lazily, after SQL creation.
                    yield None
                    return
                raise
            info = os.fstat(child)
            if info.st_uid != mail.VMAIL_UID or info.st_mode & 0o022:
                os.close(child)
                raise ValidationError('Mailbox storage has unsafe ownership or permissions')
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _read_script(descriptor):
    if descriptor is None:
        return None
    try:
        fd = os.open(SCRIPT_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as handle:
        before = os.fstat(handle.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid not in (0, mail.VMAIL_UID) or before.st_mode & 0o022
                or before.st_size > MAX_SCRIPT_BYTES):
            raise ValidationError('Active automatic-reply script is unsafe or too large')
        content = handle.read(MAX_SCRIPT_BYTES + 1)
        after = os.fstat(handle.fileno())
        current = os.stat(SCRIPT_NAME, dir_fd=descriptor, follow_symlinks=False)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if (len(content) > MAX_SCRIPT_BYTES or len(content) != before.st_size
                or identity(before) != identity(after) or identity(after) != identity(current)):
            raise ValidationError('Active automatic-reply script changed during capture')
        return content


@serialized_worker
def capture(account, addresses):
    """Return byte-exact scripts or explicit absence, without mailbox credentials.

    The coordinator must retain the shared SQL lock through safety encryption and
    application. Custom scripts are preserved as bytes; capture does not compile
    or rewrite them. Compiled Dovecot caches are derived data, not undo sources.
    """
    selected = _selection(account, addresses)
    scripts = []
    size = 0
    try:
        for domain, local in selected:
            with _home(domain, local) as descriptor:
                content = _read_script(descriptor)
            if content is not None:
                size += len(content)
                if size > MAX_TOTAL_BYTES:
                    raise ValidationError('Selected automatic-reply scripts exceed the recovery limit')
            scripts.append(dict(domain=domain, local_part=local,
                                script_base64=base64.b64encode(content).decode('ascii') if content is not None else None))
    except OSError:
        raise ValidationError('Could not safely read active automatic-reply scripts') from None
    if _selection(account, addresses) != selected:
        raise ValidationError('Automatic-reply mailbox ownership changed during capture')
    return dict(format=1, account_id=account.id, username=account.username, scripts=scripts)


@serialized_worker
def prepare_changes(account, desired_routing, current_routing):
    """Compile a complete Sieve change plan, without writing live mailbox files.

    Only mailboxes represented by current or saved panel automatic-reply records
    are affected. Other custom scripts remain outside this routing operation.
    The caller captures/encrypts actual prior scripts for the returned addresses;
    current SQL reply records are not a substitute for that exact safety copy.
    """
    from daemon import autoresponder, snapshot_mail_routing
    desired = snapshot_mail_routing.validate_for_restore(account, desired_routing)
    current = snapshot_mail_routing.validate_for_restore(account, current_routing)
    desired_domains = {entry['domain'] for entry in desired['domains']}
    if not desired_domains or desired_domains != {entry['domain'] for entry in current['domains']}:
        raise ValidationError('Automatic-reply recovery must use matching selected mail domains')
    def responders(document):
        return {(entry['domain'], reply['local_part']): reply
                for entry in document['domains'] for reply in entry['autoresponders']}
    saved, existing = responders(desired), responders(current)
    selected = sorted(saved.keys() | existing.keys())
    if len(selected) > 1000:
        raise ValidationError('Too many automatic-reply mailboxes selected for recovery')
    scripts = []
    size = 0
    for domain, local in selected:
        reply = saved.get((domain, local))
        content = None
        if reply is not None and reply['active']:
            text = autoresponder.render_sieve_script(reply['subject'], reply['body'],
                reply['start_date'], reply['end_date'], mailbox_address=f'{local}@{domain}')
            try:
                content = text.encode('utf-8')
            except UnicodeError:
                raise ValidationError('Saved automatic reply contains invalid text encoding') from None
            size += len(content)
            if len(content) > MAX_SCRIPT_BYTES or size > MAX_TOTAL_BYTES:
                raise ValidationError('Selected automatic-reply scripts exceed the recovery limit')
            try:
                autoresponder._validate_sieve_content(text)
            except Exception:
                raise ValidationError('Could not compile a restored automatic reply; no live scripts changed') from None
        scripts.append(dict(domain=domain, local_part=local,
                            script_base64=base64.b64encode(content).decode('ascii') if content is not None else None))
    return dict(format=1, account_id=account.id, username=account.username, scripts=scripts)
