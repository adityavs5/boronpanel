from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import tarfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import httpx
import pytest

from api.routers.update import StartUpdateBody
from daemon import test_update_override as lab_override
from daemon import updates
from shared.config import settings, settings_from_mapping
from shared.models import UpdateJob
from shared.release_signature import signed_message
from shared.validation import ValidationError


VERSION = "99.0.0"


def _archive() -> bytes:
    output = io.BytesIO()
    files = {
        f"boron-{VERSION}/version.py": f'BORON_VERSION = "{VERSION}"\n',
        f"boron-{VERSION}/release-metadata.json": json.dumps({
            "format": 1,
            "minimum_source_version": "0.0.0",
            "maximum_source_version": None,
            "data_plane_reloads": [],
            "control_plane_restarts": ["boron-provisiond.service", "boron-api.service"],
            "runtime_rewrites": [],
        }),
    }
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, value in files.items():
            payload = value.encode()
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return output.getvalue()


def _metadata() -> bytes:
    return json.dumps({
        "format": 1,
        "minimum_source_version": "0.0.0",
        "maximum_source_version": None,
        "data_plane_reloads": [],
        "control_plane_restarts": ["boron-provisiond.service", "boron-api.service"],
        "runtime_rewrites": [],
    }, sort_keys=True).encode()


def _install_gate(tmp_path: Path, monkeypatch, signer: Ed25519PrivateKey, base_url: str) -> None:
    marker = tmp_path / "enabled"
    source = tmp_path / "source.json"
    marker.touch(mode=0o600)
    public = signer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    source.write_text(json.dumps({
        "format": 1,
        "base_url": base_url,
        "public_key_hex": public,
    }))
    source.chmod(0o600)
    monkeypatch.setattr(lab_override, "MARKER_PATH", marker)
    monkeypatch.setattr(lab_override, "SOURCE_PATH", source)
    monkeypatch.setattr(lab_override, "ROOT_UID", os.geteuid())


def _disable_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(lab_override, "MARKER_PATH", tmp_path / "absent-marker")
    monkeypatch.setattr(lab_override, "SOURCE_PATH", tmp_path / "absent-source")
    monkeypatch.setattr(lab_override, "ROOT_UID", os.geteuid())


def _job() -> int:
    from shared.db import write_session
    with write_session() as session:
        row = UpdateJob(
            kind="update", status="running", from_version="1.0.0",
            to_version=VERSION, initiated_by="test",
        )
        session.add(row)
        session.flush()
        return row.id


@pytest.fixture
def update_paths(isolated_db, tmp_path, monkeypatch):
    versions = tmp_path / "opt"
    versions.mkdir()
    old = versions / "boron-1.0.0"
    old.mkdir()
    live = versions / "boron"
    live.symlink_to(old)
    monkeypatch.setattr(settings, "update_versions_root", str(versions))
    monkeypatch.setattr(settings, "update_live_dir", str(live))
    monkeypatch.setattr(settings, "update_download_dir", str(tmp_path / "downloads"))
    monkeypatch.setattr(settings, "update_max_download_bytes", 10 * 1024 * 1024)
    return tmp_path


def test_root_gate_loads_only_root_owned_private_loopback_descriptor(tmp_path, monkeypatch):
    signer = Ed25519PrivateKey.generate()
    _install_gate(tmp_path, monkeypatch, signer, "http://127.0.0.1:8765/releases")
    active = lab_override.load_test_update_override()
    assert active is not None
    assert active.base_url == "http://127.0.0.1:8765/releases"
    assert len(active.public_key) == 32

    lab_override.SOURCE_PATH.chmod(0o644)
    with pytest.raises(ValidationError, match="mode 0600 or stricter"):
        lab_override.load_test_update_override()


def test_marker_with_github_or_hostname_source_is_rejected(tmp_path, monkeypatch):
    signer = Ed25519PrivateKey.generate()
    for source in (
        "https://github.com/example/boron/releases",
        "http://localhost:8765/releases",
        "http://192.0.2.10:8765/releases",
    ):
        _install_gate(tmp_path, monkeypatch, signer, source)
        with pytest.raises(ValidationError, match="literal loopback"):
            lab_override.load_test_update_override()


def test_panel_config_api_and_ui_cannot_enable_override(tmp_path):
    configured = settings_from_mapping({
        "test_update_override": True,
        "test_update_url": "http://127.0.0.1:8765/releases",
        "test_update_public_key": "00" * 32,
    }, source="test")
    assert not hasattr(configured, "test_update_override")
    assert set(configured.unknown_config) == {
        "test_update_override", "test_update_url", "test_update_public_key",
    }
    body = StartUpdateBody.model_validate({
        "confirm": True,
        "test_update_override": True,
        "test_update_url": "http://127.0.0.1:8765/releases",
    })
    assert set(body.model_dump()) == {
        "confirm", "totp_code", "to_version", "acknowledge_data_plane",
        "override_active_tasks",
    }
    ui = Path("frontend/src/pages/admin/Updates.jsx").read_text()
    assert "test-update-override.enabled" not in ui
    assert "test-update-source.json" not in ui


def test_loopback_override_verifies_throwaway_release_end_to_end(
    update_paths, monkeypatch,
):
    signer = Ed25519PrivateKey.generate()
    base = "http://127.0.0.1:8765/releases"
    _install_gate(update_paths, monkeypatch, signer, base)
    archive = _archive()
    metadata = _metadata()
    digest = hashlib.sha256(archive).digest()
    assets = {
        "latest.json": json.dumps({"version": VERSION, "has_metadata": True}).encode(),
        f"v{VERSION}.json": json.dumps({"version": VERSION, "has_metadata": True}).encode(),
        f"boron-{VERSION}.tar.gz": archive,
        f"boron-{VERSION}.sha256": (
            digest.hex() + f"  boron-{VERSION}.tar.gz\n"
        ).encode(),
        f"boron-{VERSION}.tar.gz.sig": signer.sign(signed_message(VERSION, digest)),
        f"boron-{VERSION}.metadata.json": metadata,
        f"boron-{VERSION}.metadata.json.sig": signer.sign(
            signed_message(VERSION, hashlib.sha256(metadata).digest())
        ),
    }

    def serve(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        return httpx.Response(200, content=assets[request.url.path.rsplit("/", 1)[-1]])

    monkeypatch.setattr(updates, "_transport", httpx.MockTransport(serve))
    latest = updates._fetch_release()
    assert latest["source"] == "test-loopback"
    assert updates._fetch_release_metadata(latest)["signed"] is True
    job = _job()
    path = updates._download_and_verify(job, VERSION)
    target = updates._extract_staged(
        job, path, VERSION, signature_file=path + ".sig",
        public_key=updates._active_release_public_key(),
    )
    assert (Path(target) / "version.py").is_file()


def test_production_without_marker_rejects_throwaway_signature(
    update_paths, monkeypatch,
):
    _disable_gate(update_paths, monkeypatch)
    signer = Ed25519PrivateKey.generate()
    archive = _archive()
    digest = hashlib.sha256(archive).digest()
    signature = signer.sign(signed_message(VERSION, digest))
    repo = "example/boron"
    prefix = f"https://github.com/{repo}/releases/download/v{VERSION}"
    release = {
        "tag_name": f"v{VERSION}",
        "html_url": f"https://github.com/{repo}/releases/tag/v{VERSION}",
        "assets": [
            {"name": f"boron-{VERSION}.tar.gz", "browser_download_url": f"{prefix}/boron-{VERSION}.tar.gz"},
            {"name": f"boron-{VERSION}.sha256", "browser_download_url": f"{prefix}/boron-{VERSION}.sha256"},
            {"name": f"boron-{VERSION}.tar.gz.sig", "browser_download_url": f"{prefix}/boron-{VERSION}.tar.gz.sig"},
        ],
    }

    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(200, json=release)
        if request.url.host == "github.com":
            return httpx.Response(302, headers={
                "location": f"https://objects.githubusercontent.com/{request.url.path.rsplit('/', 1)[-1]}"
            })
        name = request.url.path.rsplit("/", 1)[-1]
        if name.endswith(".sha256"):
            return httpx.Response(200, content=(digest.hex() + "  archive\n").encode())
        if name.endswith(".sig"):
            return httpx.Response(200, content=signature)
        return httpx.Response(200, content=archive)

    monkeypatch.setattr(updates, "_transport", httpx.MockTransport(serve))
    monkeypatch.setattr(settings, "update_github_repo", repo)
    job = _job()
    with pytest.raises(updates._StepFailed):
        updates._download_and_verify(job, VERSION)
    from shared.db import write_session
    with write_session() as session:
        assert "publisher authentication failed" in session.get(UpdateJob, job).error


def test_production_verifier_still_uses_shipped_pin(monkeypatch):
    calls = []
    monkeypatch.setattr(updates, "verify_signature", lambda *args: calls.append(args))
    updates._verify_release_signature("1.2.3", b"d" * 32, b"s" * 64, None)
    assert calls == [("1.2.3", b"d" * 32, b"s" * 64)]
