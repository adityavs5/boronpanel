"""Prepare restored Maildirs inside private snapshot staging.

Never pass an unvalidated restored tree directly to Dovecot. This module only
copies into a new private directory; it does not switch or modify live mailboxes.
"""
import os
from pathlib import Path
import shutil
import stat
import tempfile
import uuid
import json

from daemon.procutil import run
from shared.validation import ValidationError


def prepare_maildir(source, destination, private_root):
    """Copy regular files/directories, retaining Dovecot metadata and message names.

    The caller owns the private root for the operation's lifetime. Requiring both
    trees beneath it prevents an account from racing validation and copying.
    Symlinks and special files are rejected before any destination is created.
    The returned files remain service-owned until the Dovecot worker is started.
    """
    source, destination, private_root = map(Path, (source, destination, private_root))
    info = private_root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or private_root.resolve() != private_root
            or info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise ValidationError('Mail restore staging must be private and service-owned')
    for path in (source, destination.parent):
        if path.resolve() != path or not path.is_relative_to(private_root):
            raise ValidationError('Mail restore paths must remain inside private staging')
    if source == private_root or destination == source or destination.is_relative_to(source):
        raise ValidationError('Mail restore requires separate source and destination trees')
    if destination.exists() or destination.is_symlink():
        raise ValidationError('Mail restore destination already exists')
    entries = []
    def walk_error(error):
        raise error
    for base, directories, files in os.walk(source, followlinks=False, onerror=walk_error):
        for path in [Path(base), *[Path(base) / name for name in directories + files]]:
            value = path.lstat()
            if not (stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode)):
                raise ValidationError('Mail restore contains a symbolic link or special file')
        # Each item is recorded once; os.walk will visit directories separately.
        entries.append((Path(base).relative_to(source), files))
    if not source.is_dir():
        raise ValidationError('Mail restore source is not a directory')
    for name in ('cur', 'new', 'tmp'):
        if not (source / name).is_dir():
            raise ValidationError('Mail restore source is missing Maildir directories')
    destination.mkdir(mode=0o700)
    count = size = 0
    try:
        for relative, files in entries:
            target = destination / relative
            if relative != Path('.'):
                target.mkdir(mode=0o700)
            for name in files:
                fd = os.open(source / relative / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as incoming:
                    if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                        raise ValidationError('Mail restore source changed during preparation')
                    out = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(out, 'wb') as outgoing:
                        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                        size += outgoing.tell()
                    count += 1
        return {'files': count, 'bytes': size}
    except BaseException:
        shutil.rmtree(destination)
        raise


def build_maildir(source, destination, private_root, *, uid=150, gid=150, timeout=3600):
    """Build a Dovecot-consistent replacement without opening a live mailbox.

    The restore service must still authorize the snapshot, preserve a safety
    snapshot and quiesce delivery/clients before applying the resulting tree.
    Dovecot runs with no supplementary groups and an isolated configuration.
    Only the mail-service identity can traverse its temporary working directory.
    Neither the original snapshot nor its private ancestors are made accessible.
    """
    if os.geteuid() != 0:
        raise ValidationError('Mail recovery preparation requires the backup service')
    if any(type(value) is not int or value <= 0 for value in (uid, gid)):
        raise ValidationError('Mail recovery worker must use an unprivileged identity')
    destination = Path(destination)
    # This also validates the destination boundary and rejects existing targets.
    prepare_maildir(source, destination, private_root)
    try:
        with tempfile.TemporaryDirectory(prefix='boron-mail-build-', dir='/tmp') as directory:
            work = Path(directory)
            shutil.move(str(destination), work / 'source')
            for name in ('home', 'run'):
                (work / name).mkdir(mode=0o700)
            for base, directories, files in os.walk(work):
                for path in [Path(base), *[Path(base) / name for name in files]]:
                    if path != work:
                        os.chown(path, uid, gid, follow_symlinks=False)
            config = work / 'dovecot.conf'
            config.write_text(
                f'base_dir = {work}/run\nmail_home = {work}/home\n'
                f'mail_location = maildir:{work}/source\n'
                f'mail_uid = {uid}\nmail_gid = {gid}\nfirst_valid_uid = 1\n'
                'log_path = /dev/stderr\nssl = no\nmail_plugins =\n'
                'namespace inbox {\n inbox = yes\n separator = /\n}\n'
            )
            os.chown(config, 0, gid)
            config.chmod(0o640)
            os.chown(work, 0, gid)
            work.chmod(0o710)
            prefix = ['/usr/bin/setpriv', f'--reuid={uid}', f'--regid={gid}',
                      '--clear-groups', '--no-new-privs', '/usr/bin/doveadm', '-c', str(config)]
            try:
                # Raw snapshots can precede Dovecot's first index creation.
                result = run([*prefix, 'mailbox', 'status', 'messages', '*'],
                             cwd=str(work), timeout=timeout, discard_stdout=True)
                if not result.ok:
                    raise ValidationError('Dovecot could not open the saved mailbox')
                result = run([*prefix, 'backup', '-f', 'maildir:' + str(work / 'home/prepared')],
                             cwd=str(work), timeout=timeout, discard_stdout=True)
                if not result.ok:
                    raise ValidationError('Dovecot could not prepare the saved mailbox')
            finally:
                work.chmod(0o700)
            result = prepare_maildir(work / 'home/prepared', work / 'exported', work)
            shutil.move(str(work / 'exported'), destination)
            return result
    except BaseException:
        # Destination was created by this call only. The source is never changed.
        if destination.exists():
            shutil.rmtree(destination)
        raise


def stage_for_exchange(source, private_root, domain, local_part, *, uid=150, gid=150, prepared=None,
                       receipt=None, restore_id=None):
    """Place a durable prepared sibling on the mailbox filesystem before pausing.

    Caller verifies account ownership first. Source must be within private,
    service-owned staging. The destination is created exclusively through the
    validated mailbox home descriptor. A job should persist its generated name
    before calling and pass it as prepared so crash cleanup can identify it.
    Never modify the live Maildir here.
    """
    from daemon.snapshot_mail_exchange import _home, _prepared_name
    if os.geteuid() != 0 or any(type(value) is not int or value <= 0 for value in (uid, gid)):
        raise ValidationError('Mail preparation requires an unprivileged mailbox identity')
    source, private_root = Path(source), Path(private_root)
    info = private_root.lstat()
    if (private_root.resolve() != private_root or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0 or info.st_mode & 0o077 or source.resolve() != source
            or source == private_root or not source.is_relative_to(private_root)):
        raise ValidationError('Prepared mail must come from private service staging')
    name = _prepared_name(prepared if prepared is not None else '.boron-mail-ready-' + uuid.uuid4().hex)
    counts = {'files': 0, 'bytes': 0}
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if receipt is not None:
        receipt = Path(receipt)
        if (type(restore_id) is not int or restore_id <= 0 or receipt.resolve() != receipt
                or receipt == private_root or not receipt.is_relative_to(private_root)):
            raise ValidationError('Invalid private mailbox placement receipt')
    with _home(domain, local_part) as home:
        incoming = os.open(source, flags)
        try:
            for part in ('cur', 'new', 'tmp'):
                check = os.open(part, flags, dir_fd=incoming)
                os.close(check)
            parent = os.fstat(home)
            record = {'format': 1, 'restore_id': restore_id, 'domain': domain, 'local_part': local_part,
                      'prepared': name, 'home': [parent.st_dev, parent.st_ino],
                      'identity': None, 'status': 'planned'}
            if receipt is not None:
                _placement_receipt(receipt, record, create=True)
            os.mkdir(name, 0o700, dir_fd=home)
            outgoing = os.open(name, flags, dir_fd=home)
            try:
                identity = os.fstat(outgoing)
                record.update(identity=[identity.st_dev, identity.st_ino], status='copying')
                os.fsync(home)
                if receipt is not None:
                    _placement_receipt(receipt, record)
                _copy_mail_tree(incoming, outgoing, uid, gid, counts)
                os.fsync(home)
                record.update(status='ready', **counts)
                if receipt is not None:
                    _placement_receipt(receipt, record)
            except BaseException:
                # Do not delete a substituted directory after a path race.
                actual = os.stat(name, dir_fd=home, follow_symlinks=False)
                expected = os.fstat(outgoing)
                if (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino):
                    shutil.rmtree(name, dir_fd=home)
                    os.fsync(home)
                raise
            finally:
                os.close(outgoing)
        finally:
            os.close(incoming)
    return {'prepared': name, **counts}


def _placement_receipt(path, payload, *, create=False):
    """Persist placement phases atomically; an older phase means inspect first.

    Only the placement worker writes this private file. Recovery must establish
    that worker termination is authoritative before using it for cleanup.
    """
    temporary = path if create else path.with_name('.placement-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(payload, handle, separators=(',', ':'))
            handle.flush()
            os.fsync(handle.fileno())
        if not create:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
                raise ValidationError('Mailbox placement receipt changed unexpectedly')
            os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if not create:
            temporary.unlink(missing_ok=True)


def inspect_placement(receipt, private_root):
    """Inspect a receipt without deleting/adopting files or deciding job status.

    The coordinator must confirm worker termination and account ownership before
    making a recovery decision from this observation.
    """
    from daemon.snapshot_mail_exchange import _home, _prepared_name
    receipt, private_root = Path(receipt), Path(private_root)
    info = private_root.lstat()
    if (private_root.resolve() != private_root or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0 or info.st_mode & 0o077 or receipt.resolve() != receipt
            or receipt == private_root or not receipt.is_relative_to(private_root)):
        raise ValidationError('Invalid private mailbox placement receipt')
    fd = os.open(receipt, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 8192:
            raise ValidationError('Invalid private mailbox placement receipt')
        try:
            record = json.loads(handle.read(8193))
            if type(record['format']) is not int or record['format'] != 1 or type(record['restore_id']) is not int or record['restore_id'] <= 0:
                raise ValueError()
            name = _prepared_name(record['prepared'])
            if record['status'] not in ('planned', 'copying', 'ready'):
                raise ValueError()
            for key in ('home', 'identity'):
                value = record[key]
                if key == 'identity' and value is None and record['status'] == 'planned':
                    continue
                if not isinstance(value, list) or len(value) != 2 or any(type(v) is not int or v < 0 for v in value):
                    raise ValueError()
            domain, local_part = record['domain'], record['local_part']
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise ValidationError('Invalid private mailbox placement receipt') from None
    with _home(domain, local_part) as home:
        parent = os.fstat(home)
        if record['home'] != [parent.st_dev, parent.st_ino]:
            raise ValidationError('Mailbox home no longer matches its placement receipt')
        if record['identity'] is None:
            return 'unconfirmed'
        def identity(name):
            try:
                info = os.stat(name, dir_fd=home, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if not stat.S_ISDIR(info.st_mode):
                raise ValidationError('Mailbox placement path is no longer a directory')
            return [info.st_dev, info.st_ino]
        prepared = identity(name)
        if prepared == record['identity']:
            return record['status']
        if identity('Maildir') == record['identity']:
            return 'exchanged'
        if prepared is None:
            return 'missing'
        raise ValidationError('Prepared mailbox no longer matches its placement receipt')


def _copy_mail_tree(source, target, uid, gid, counts):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    for name in os.listdir(source):
        fd = os.open(name, flags, dir_fd=source)
        try:
            info = os.fstat(fd)
            if stat.S_ISDIR(info.st_mode):
                os.mkdir(name, 0o700, dir_fd=target)
                child = os.open(name, flags | os.O_DIRECTORY, dir_fd=target)
                try:
                    _copy_mail_tree(fd, child, uid, gid, counts)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target)
                with os.fdopen(out, 'wb') as destination, os.fdopen(os.dup(fd), 'rb') as incoming:
                    shutil.copyfileobj(incoming, destination, length=1024*1024)
                    destination.flush()
                    os.fchown(destination.fileno(), uid, gid)
                    os.fchmod(destination.fileno(), 0o600)
                    os.fsync(destination.fileno())
                    counts['bytes'] += destination.tell()
                    counts['files'] += 1
            else:
                raise ValidationError('Prepared mailbox contains a special file')
        finally:
            os.close(fd)
    os.fchown(target, uid, gid)
    os.fchmod(target, 0o700)
    os.fsync(target)
