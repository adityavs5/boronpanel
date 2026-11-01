import json

import pytest
from boron_license import config, service
from boron_license.api import create_app
from boron_license.db import create_schema, make_engine, make_sessionmaker
from boron_license.signing import Signer
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

PROXY = ("127.0.0.1", 40000)


@pytest.fixture
def sessions(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'licenses.db'}")
    create_schema(engine)
    maker = make_sessionmaker(engine)
    with maker() as session:
        service.seed_tiers(session)
        session.commit()
    return maker


@pytest.fixture
def signer():
    return Signer(Ed25519PrivateKey.generate())


@pytest.fixture
def client(sessions, signer):
    settings = config.Settings(database_url="unused", grace_days=14)
    return TestClient(create_app(settings, sessions, signer), client=PROXY)


@pytest.fixture
def admin_headers(sessions):
    with sessions() as session:
        token = service.create_admin_token(session, "tests", actor="tests")
        session.commit()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def issue(sessions):
    def _issue(**overrides):
        values = {"tier": "pro", "customer_name": "Acme Hosting", "customer_email": "ops@acme.example",
                  "actor": "tests"} | overrides
        with sessions() as session:
            license_, key = service.issue(session, **values)
            session.commit()
            return license_.id, key
    return _issue


DOWNLOAD_SECRET = "test-download-secret-0123456789abcdefghijklmnop"


def write_release(root, version, channel="stable", *, publish=True, overrides=None):
    """Lay out releases/<ver>/release.json (and channels/<channel>.json) like the release tooling."""
    files = {"archive": f"boron-{version}.tar.gz", "checksum": f"boron-{version}.sha256",
             "signature": f"boron-{version}.tar.gz.sig", "metadata": f"boron-{version}.metadata.json",
             "metadata_signature": f"boron-{version}.metadata.json.sig"}
    index = {"format": 1, "channel": channel, "version": version, "published_at": "2026-11-01T18:00:00Z",
             "changelog": f"## {version}\n- Changes", "files": files, "sizes": {"archive": 123456}} | (overrides or {})
    release_dir = root / "releases" / version
    release_dir.mkdir(parents=True, exist_ok=True)
    (release_dir / "release.json").write_text(json.dumps(index))
    if publish:
        (root / "channels").mkdir(parents=True, exist_ok=True)
        (root / "channels" / f"{channel}.json").write_text(json.dumps(index))
    return index


@pytest.fixture
def releases_dir(tmp_path):
    root = tmp_path / "protected"
    (root / "releases").mkdir(parents=True)
    (root / "channels").mkdir()
    return root


@pytest.fixture
def updates_client(sessions, signer, releases_dir):
    settings = config.Settings(database_url="unused", download_secret=DOWNLOAD_SECRET, releases_dir=releases_dir)
    return TestClient(create_app(settings, sessions, signer), client=PROXY)


def call(client, endpoint, key, ip="203.0.113.10", install_id="install-0001", **extra):
    body = {"license_key": key, "install_id": install_id, "hostname": "panel.acme.example",
            "version": "3.3.0"} | extra
    return client.post(f"/v1/{endpoint}", json=body, headers={"X-Real-IP": ip})
