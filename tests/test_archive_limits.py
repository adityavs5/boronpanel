import io
import tarfile

import pytest

from daemon import archive_limits


def test_first_pax_header_is_rejected_before_its_declared_body_is_read(tmp_path):
    path = tmp_path / "metadata-bomb.tar"
    header = tarfile.TarInfo("pax")
    header.type = tarfile.XHDTYPE
    header.size = archive_limits.MAX_TAR_METADATA_READ + 1
    path.write_bytes(header.tobuf())

    with pytest.raises(archive_limits.ArchiveMetadataTooLarge):
        archive_limits.open_guarded_tar(path)


def test_normal_pax_archive_remains_readable(tmp_path):
    path = tmp_path / "normal.tar"
    payload = b"ok"
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("nested/" + "a" * 120)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    with archive_limits.open_guarded_tar(path) as archive:
        member = archive.next()
        assert member is not None
        assert archive.extractfile(member).read() == payload


def test_consecutive_extension_headers_share_one_metadata_budget(tmp_path):
    path = tmp_path / "metadata-chain.tar"
    size = archive_limits.MAX_TAR_METADATA_READ // 2 + 1
    first = tarfile.TarInfo("long-one")
    first.type = tarfile.GNUTYPE_LONGNAME
    first.size = size
    second = tarfile.TarInfo("long-two")
    second.type = tarfile.GNUTYPE_LONGNAME
    second.size = size
    padding = b"x" * size + b"\0" * (first._block(size) - size)
    path.write_bytes(first.tobuf() + padding + second.tobuf())

    with pytest.raises(archive_limits.ArchiveMetadataTooLarge):
        archive_limits.open_guarded_tar(path)
