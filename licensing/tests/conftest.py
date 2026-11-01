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


def call(client, endpoint, key, ip="203.0.113.10", install_id="install-0001", **extra):
    body = {"license_key": key, "install_id": install_id, "hostname": "panel.acme.example",
            "version": "3.3.0"} | extra
    return client.post(f"/v1/{endpoint}", json=body, headers={"X-Real-IP": ip})
