"""Prepare restored Maildirs inside private snapshot staging.

Never pass an unvalidated restored tree directly to Dovecot. This module only
copies into a new private directory; it does not switch or modify live mailboxes.
"""
import os
from pathlib import Path
import shutil
import stat

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
