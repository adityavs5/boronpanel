"""Streaming authenticated encryption for downloadable portable backups.

The destination recovery key is deliberately reused as the passphrase so an
administrator has one independently exported secret per destination.  The
format is versioned and self-contained; encryption and decryption never load
the archive into memory.
"""
from pathlib import Path
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


MAGIC = b"BORON-AES256-GCM\x01"
SALT_SIZE = 16
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024


class PortableEncryptionError(RuntimeError):
    pass


def _derive(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or len(passphrase) < 16:
        raise PortableEncryptionError("A valid destination recovery key is required")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def encrypt(source: Path, target: Path, passphrase: str) -> None:
    salt, nonce = os.urandom(SALT_SIZE), os.urandom(NONCE_SIZE)
    header = MAGIC + salt + nonce
    encryptor = Cipher(algorithms.AES(_derive(passphrase, salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    created = False
    try:
        with source.open("rb") as readable, target.open("xb") as writable:
            created = True
            writable.write(header)
            for chunk in iter(lambda: readable.read(CHUNK_SIZE), b""):
                writable.write(encryptor.update(chunk))
            writable.write(encryptor.finalize())
            writable.write(encryptor.tag)
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise


def decrypt(source: Path, target: Path, passphrase: str) -> None:
    size = source.stat().st_size
    header_size = len(MAGIC) + SALT_SIZE + NONCE_SIZE
    if size < header_size + TAG_SIZE:
        raise PortableEncryptionError("This is not a complete Boron encrypted archive")
    created = False
    try:
        with source.open("rb") as readable:
            header = readable.read(header_size)
            if not header.startswith(MAGIC):
                raise PortableEncryptionError("This is not a Boron encrypted archive")
            salt = header[len(MAGIC):len(MAGIC) + SALT_SIZE]
            nonce = header[-NONCE_SIZE:]
            readable.seek(-TAG_SIZE, os.SEEK_END)
            tag = readable.read(TAG_SIZE)
            readable.seek(header_size)
            remaining = size - header_size - TAG_SIZE
            decryptor = Cipher(algorithms.AES(_derive(passphrase, salt)), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(header)
            with target.open("xb") as writable:
                created = True
                while remaining:
                    chunk = readable.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise PortableEncryptionError("Encrypted archive ended unexpectedly")
                    remaining -= len(chunk)
                    writable.write(decryptor.update(chunk))
                writable.write(decryptor.finalize())
    except InvalidTag as exc:
        if created:
            target.unlink(missing_ok=True)
        raise PortableEncryptionError("Recovery key is incorrect or the archive was modified") from exc
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise
