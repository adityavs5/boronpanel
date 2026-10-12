"""Pre-parser limits for privileged tar readers."""
from __future__ import annotations

import tarfile

MAX_TAR_METADATA_READ = 16 * 1024 * 1024
MAX_TAR_RAW_HEADERS = 100_000


class ArchiveMetadataTooLarge(ValueError):
    pass


class _CumulativeReadGuard:
    def __init__(self, wrapped, archive):
        self._wrapped = wrapped
        self._archive = archive

    def read(self, size=-1):
        if size is None or size < 0:
            raise ArchiveMetadataTooLarge("archive metadata entry is too large")
        consumed = int(getattr(self._archive, "_boron_metadata_bytes", 0)) + size
        if consumed > MAX_TAR_METADATA_READ:
            raise ArchiveMetadataTooLarge("archive metadata entry is too large")
        value = self._wrapped.read(size)
        self._archive._boron_metadata_bytes = (
            int(getattr(self._archive, "_boron_metadata_bytes", 0)) + len(value)
        )
        return value

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


class LimitedTarInfo(tarfile.TarInfo):
    """Reject extension-header bombs before tarfile reads their bodies."""

    def _proc_member(self, archive):
        # TarFile reads its first member during construction, so this guard
        # must live in the metadata parser rather than wrap fileobj afterward.
        count = int(getattr(archive, "_boron_raw_header_count", 0)) + 1
        archive._boron_raw_header_count = count
        if count > MAX_TAR_RAW_HEADERS:
            raise ArchiveMetadataTooLarge("archive contains too many raw headers")
        extension_types = (
            tarfile.GNUTYPE_LONGNAME,
            tarfile.GNUTYPE_LONGLINK,
            tarfile.XHDTYPE,
            tarfile.XGLTYPE,
            tarfile.SOLARIS_XHDTYPE,
        )
        if self.type in extension_types:
            metadata_bytes = int(getattr(archive, "_boron_metadata_bytes", 0)) + self._block(self.size)
            if metadata_bytes > MAX_TAR_METADATA_READ:
                raise ArchiveMetadataTooLarge("archive metadata entries are too large")
            # Account before tarfile recursively parses the next header. A
            # chain of individually-small GNU/PAX headers otherwise retains
            # every body on the Python stack and defeats a per-header cap.
            archive._boron_metadata_bytes = metadata_bytes
        if self.type == tarfile.GNUTYPE_SPARSE:
            original = archive.fileobj
            archive.fileobj = _CumulativeReadGuard(original, archive)
            try:
                return super()._proc_member(archive)
            finally:
                archive.fileobj = original
        return super()._proc_member(archive)


def open_guarded_tar(*args, **kwargs):
    if "tarinfo" in kwargs:
        raise ValueError("custom tar metadata parser is not permitted")
    return tarfile.open(*args, tarinfo=LimitedTarInfo, **kwargs)
