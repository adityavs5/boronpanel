"""Prepare restored Maildirs inside private snapshot staging.

Never pass an unvalidated restored tree directly to Dovecot. This module only
copies into a new private directory; it does not switch or modify live mailboxes.
"""
import os
from pathlib import Path
import shutil
import stat
import tempfile

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
