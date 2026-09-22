"""GeoLite download keeps credentials out of argv and bounds archive input."""
from __future__ import annotations

import io
import subprocess
import tarfile

from daemon import geoip
from daemon.procutil import ProcResult


def _archive(data: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        member = tarfile.TarInfo("GeoLite2-Country_20260922/GeoLite2-Country.mmdb")
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


def test_license_key_goes_to_curl_stdin_and_database_is_bounded(tmp_path, monkeypatch):
    key = "License+ key"
    monkeypatch.setattr(geoip, "GEOLITE_DB_PATH", str(tmp_path / "GeoLite2-Country.mmdb"))
    recorded = {}

    def fake_run(args, **kwargs):
        recorded.update(args=args, kwargs=kwargs)
        (tmp_path / "downloaded.tar.gz").write_bytes(_archive(b"MMDB"))
        import shutil
        shutil.copyfile(tmp_path / "downloaded.tar.gz", args[-1])
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(geoip, "run", fake_run)
    geoip.download_database(key)
    assert (tmp_path / "GeoLite2-Country.mmdb").read_bytes() == b"MMDB"
    assert key not in " ".join(recorded["args"])
    assert "License%2B%20key" in recorded["kwargs"]["input_text"]
    assert recorded["args"][1:3] == ["--config", "-"]
    assert "--max-filesize" in recorded["args"]


def test_oversized_database_member_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(geoip, "GEOLITE_DB_PATH", str(tmp_path / "GeoLite2-Country.mmdb"))
    monkeypatch.setattr(geoip, "MAX_GEOLITE_DATABASE_BYTES", 3)

    def fake_run(args, **kwargs):
        from pathlib import Path
        Path(args[-1]).write_bytes(_archive(b"MMDB"))
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(geoip, "run", fake_run)
    import pytest
    with pytest.raises(geoip.GeoIpError, match="too large"):
        geoip.download_database("license")
    assert not (tmp_path / "GeoLite2-Country.mmdb").exists()


def test_curl_accepts_url_in_stdin_config(tmp_path):
    source = tmp_path / "source.txt"
    target = tmp_path / "download.txt"
    source.write_text("test-data")
    result = subprocess.run(
        ["curl", "--config", "-", "--silent", "--show-error", "--output", str(target)],
        input=f'url = "{source.as_uri()}"\n', capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert target.read_text() == "test-data"
