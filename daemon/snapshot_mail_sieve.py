"""Private capture of active Sieve scripts for routing recovery and exact undo.

Only fixed mailbox script paths are accessed. Activation is an internal primitive;
its coordinator owns safety encryption, delivery quiescence and durable recovery.
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
def _home(domain, local, *, create=False):
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
                if index != 1:
                    raise
                if not create:
                    # Dovecot creates mailbox homes lazily, after SQL creation.
                    yield None
                    return
                os.mkdir(component, 0o700, dir_fd=descriptor)
                child = os.open(component, flags, dir_fd=descriptor)
                try:
                    os.fchown(child, mail.VMAIL_UID, mail.VMAIL_GID)
                    os.fchmod(child, 0o700)
                    os.fsync(child)
                    os.fsync(descriptor)
                except BaseException:
                    os.close(child)
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


def _documents(account, payload, *, allow_empty=False):
    """Validate private script data before decoding or opening live files."""
    if (not isinstance(payload, dict) or type(payload.get('format')) is not int or payload['format'] != 1
            or type(payload.get('account_id')) is not int or payload['account_id'] != account.id
            or payload.get('username') != account.username or not isinstance(payload.get('scripts'), list)
            or not (0 if allow_empty else 1) <= len(payload['scripts']) <= 1000):
        raise ValidationError('Invalid private automatic-reply recovery document')
    result = {}
    size = 0
    for entry in payload['scripts']:
        if (not isinstance(entry, dict) or not isinstance(entry.get('domain'), str)
                or not isinstance(entry.get('local_part'), str) or 'script_base64' not in entry):
            raise ValidationError('Incomplete automatic-reply recovery script')
        key = (validate_domain(entry['domain']), validate_mailbox_local_part(entry['local_part']))
        if key in result:
            raise ValidationError('Duplicate automatic-reply recovery script')
        encoded = entry['script_base64']
        content = None
        if encoded is not None:
            if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_SCRIPT_BYTES + 2) // 3):
                raise ValidationError('Invalid automatic-reply recovery script encoding')
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, UnicodeError):
                raise ValidationError('Invalid automatic-reply recovery script encoding') from None
            if base64.b64encode(content).decode('ascii') != encoded or len(content) > MAX_SCRIPT_BYTES:
                raise ValidationError('Invalid automatic-reply recovery script encoding')
            size += len(content)
            if size > MAX_TOTAL_BYTES:
                raise ValidationError('Selected automatic-reply scripts exceed the recovery limit')
        result[key] = content
    if result:
        _selection(account, [local + '@' + domain for domain, local in result])
    return result


def _compile_bytes(content):
    import tempfile
    from daemon import autoresponder
    from daemon.procutil import run
    with tempfile.TemporaryDirectory(prefix='boron-sieve-compile-') as temporary:
        source = Path(temporary) / 'script.sieve'
        source.write_bytes(content)
        source.chmod(0o600)
        try:
            result = run([autoresponder.SIEVEC_BIN, str(source), str(Path(temporary) / 'script.svbin')], timeout=15)
            if not result.ok:
                raise ValidationError('Restored automatic-reply script did not compile')
        except Exception:
            raise ValidationError('Could not compile restored automatic-reply script; no live scripts changed') from None


def _check_cache(descriptor):
    if descriptor is None:
        return
    try:
        info = os.stat('.dovecot.svbin', dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode):
        raise ValidationError('Automatic-reply compiled cache has an unsafe file type')


def _write_script(descriptor, content):
    """Atomic file replacement inside an already authorized, guarded home."""
    import secrets
    if content is None:
        try:
            os.unlink(SCRIPT_NAME, dir_fd=descriptor)
        except FileNotFoundError:
            pass
    else:
        temporary = '.boron-sieve-' + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(content)
                handle.flush()
                os.fchown(handle.fileno(), mail.VMAIL_UID, mail.VMAIL_GID)
                os.fchmod(handle.fileno(), 0o600)
                os.fsync(handle.fileno())
            os.replace(temporary, SCRIPT_NAME, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        finally:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass
    try:
        os.unlink('.dovecot.svbin', dir_fd=descriptor)
    except FileNotFoundError:
        pass
    os.fsync(descriptor)


@serialized_worker
def _apply(account, desired, expected, restore_id, guards, on_applied):
    """Guarded internal activation; not a public restore or retry entry point.

    Caller must hold the account lock, persist encrypted SQL/script safety state
    and guard tokens, and quiesce delivery before calling. A mailbox guard alone
    does not drain existing Dovecot sessions. All guards remain owned on return
    or failure; only the recovery coordinator may release them after verification.
    Partial/uncertain changes require journal-based inspection, not blind replay.
    """
    from daemon import snapshot_mail_guard as guard
    if type(restore_id) is not int or restore_id <= 0 or not callable(on_applied):
        raise ValidationError('Automatic-reply activation requires a job and durable checkpoints')
    wanted, before = _documents(account, desired), _documents(account, expected)
    if wanted.keys() != before.keys():
        raise ValidationError('Automatic-reply safety selection does not match activation')
    if (not isinstance(guards, list) or len(guards) != len(wanted)
            or any(not isinstance(entry, dict) or not isinstance(entry.get('token'), str) for entry in guards)):
        raise ValidationError('Automatic-reply activation requires matching owned mailbox guards')
    keys = [(entry.get('domain'), entry.get('local_part')) for entry in guards]
    if any(not isinstance(domain, str) or not isinstance(local, str) for domain, local in keys) or set(keys) != wanted.keys():
        raise ValidationError('Automatic-reply guard selection does not match activation')
    with guard.owned_guards(guards, restore_id):
        # Compile and preflight the entire batch before the first live mutation.
        for content in wanted.values():
            if content is not None:
                _compile_bytes(content)
        for domain, local in wanted:
            with _home(domain, local) as descriptor:
                if _read_script(descriptor) != before[domain, local]:
                    raise ValidationError('Automatic-reply script changed after its safety copy')
                _check_cache(descriptor)
        completed = []
        for domain, local in sorted(wanted):
            content = wanted[domain, local]
            with _home(domain, local, create=content is not None) as descriptor:
                if _read_script(descriptor) != before[domain, local]:
                    raise ValidationError('Automatic-reply script changed before activation')
                _check_cache(descriptor)
                if descriptor is not None:
                    _write_script(descriptor, content)
                if _read_script(descriptor) != content:
                    raise ValidationError('Automatic-reply activation could not be verified')
            address = local + '@' + domain
            on_applied(address)
            completed.append(address)
    return {'mailboxes': completed}
