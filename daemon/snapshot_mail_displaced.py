"""Descriptor-bound disposal of an already verified displaced Maildir.

The coordinator authorizes the completed job and supplies a digest obtained by
restoring its encrypted safety snapshot. Private receipts make deletion resumable.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

from daemon import snapshot_mail_exchange as exchange, snapshot_mail_journal as journal
from daemon.snapshot_mail_files import _placement_receipt
from daemon.snapshot_mail_restore import _private_document
from shared.validation import ValidationError


def tree_digest(directory):
    """Hash names, directory structure and regular-file contents without links."""
    result = hashlib.sha256()
    def walk(fd, prefix):
        for name in sorted(os.listdir(fd)):
            relative = prefix + [name]
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            result.update(json.dumps(relative, ensure_ascii=True).encode() + b'\0')
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, exchange.FLAGS, dir_fd=fd)
                try:
                    result.update(b'D')
                    walk(child, relative)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    before = os.fstat(child)
                    if not stat.S_ISREG(before.st_mode):
                        raise ValidationError('Displaced mailbox file changed')
                    content = hashlib.sha256()
                    while block := os.read(child, 1024 * 1024):
                        content.update(block)
                    after = os.fstat(child)
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValidationError('Displaced mailbox file changed while verifying')
                    result.update(b'F' + content.digest())
                finally:
                    os.close(child)
            else:
                raise ValidationError('Displaced mailbox contains a link or special file')
    fd = os.dup(directory) if isinstance(directory, int) else os.open(directory, exchange.FLAGS)
    try:
        walk(fd, [])
    finally:
        os.close(fd)
    return result.hexdigest()


def _info(parent, name):
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise ValidationError('Displaced mailbox directory was replaced')
    return [info.st_dev, info.st_ino]


def _move(source, name, destination):
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source, name.encode(), destination, b'displaced', 1) != 0:  # RENAME_NOREPLACE
        raise OSError(ctypes.get_errno(), 'Could not quarantine displaced mailbox')
    os.fsync(source)
    os.fsync(destination)


def remove_verified(entry, receipt_path, digest):
    """Remove exactly the journal's former Maildir, preserving current Maildir."""
    receipt_path = journal._path(Path(receipt_path))
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValidationError('Invalid displaced mailbox digest')
    plan = entry['plan']
    prepared = exchange._prepared_name(plan['prepared'])
    expected = dict(format=1, domain=entry['domain'], local_part=entry['local_part'],
                    prepared=prepared, home=plan['home'], identity=plan['current'], digest=digest)
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValidationError('Safe mailbox cleanup is unavailable')
    with exchange._home(entry['domain'], entry['local_part']) as home:
        info = os.fstat(home)
        if [info.st_dev, info.st_ino] != plan['home']:
            raise ValidationError('Mailbox home changed before cleanup')
        if receipt_path.exists() or receipt_path.is_symlink():
            record = _private_document(receipt_path)
            if (any(record.get(k) != v for k, v in expected.items())
                    or record.get('phase') not in ('ready', 'deleting', 'deleted')
                    or not re.fullmatch(r'\.boron-mail-cleanup-[a-f0-9]{32}', record.get('quarantine', ''))):
                raise ValidationError('Displaced mailbox cleanup receipt changed')
            if record['phase'] == 'deleted':
                if _info(home, record['quarantine']) == record.get('quarantine_identity'):
                    os.rmdir(record['quarantine'], dir_fd=home)
                    os.fsync(home)
                return
        else:
            if _info(home, prepared) != plan['current']:
                raise ValidationError('Displaced mailbox no longer matches its journal')
            source = os.open(prepared, exchange.FLAGS, dir_fd=home)
            try:
                if tree_digest(source) != digest:
                    raise ValidationError('Displaced mailbox differs from its encrypted recovery copy')
            finally:
                os.close(source)
            name = '.boron-mail-cleanup-' + uuid.uuid4().hex
            os.mkdir(name, 0o700, dir_fd=home)
            fd = os.open(name, exchange.FLAGS, dir_fd=home)
            try:
                info = os.fstat(fd)
                if info.st_uid != 0 or info.st_mode & 0o077:
                    raise ValidationError('Cleanup quarantine must be root-private')
                record = dict(expected, quarantine=name, quarantine_identity=[info.st_dev, info.st_ino], phase='ready')
                os.fsync(home)
                _placement_receipt(receipt_path, record, create=True)
            finally:
                os.close(fd)
        quarantine = os.open(record['quarantine'], exchange.FLAGS, dir_fd=home)
        try:
            info = os.fstat(quarantine)
            if (info.st_uid != 0 or info.st_mode & 0o077
                    or [info.st_dev, info.st_ino] != record.get('quarantine_identity')):
                raise ValidationError('Cleanup quarantine changed')
            target = _info(quarantine, 'displaced')
            if target is None and record['phase'] == 'ready':
                if _info(home, prepared) != plan['current']:
                    raise ValidationError('Displaced mailbox disappeared before cleanup')
                _move(home, prepared, quarantine)
                target = _info(quarantine, 'displaced')
            if target is not None and target != plan['current']:
                raise ValidationError('Quarantined mailbox differs from its journal')
            if record['phase'] == 'ready':
                fd = os.open('displaced', exchange.FLAGS, dir_fd=quarantine)
                try:
                    if tree_digest(fd) != digest:
                        raise ValidationError('Quarantined mailbox differs from its encrypted recovery copy')
                finally:
                    os.close(fd)
                record['phase'] = 'deleting'
                _placement_receipt(receipt_path, record)
            if target is not None:
                shutil.rmtree('displaced', dir_fd=quarantine)
            os.fsync(quarantine)
            record['phase'] = 'deleted'
            _placement_receipt(receipt_path, record)
        finally:
            os.close(quarantine)
        # The empty root-private container may be removed only when still bound
        # to this receipt. A moved/replaced container is left for inspection.
        if _info(home, record['quarantine']) == record['quarantine_identity']:
            os.rmdir(record['quarantine'], dir_fd=home)
            os.fsync(home)
