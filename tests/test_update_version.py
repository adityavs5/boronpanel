"""Panel update system -- version tracking (version.py + GET /api/v1/version).

version.py is the single source of truth; these tests pin the places that
must stay in sync with it (API response, installer parsing, vite build
injection) without duplicating the version string itself anywhere.
"""
from __future__ import annotations

import re
import subprocess
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore")

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import api.main as main  # noqa: E402
from api.security import Identity, get_identity  # noqa: E402
from version import BORON_VERSION  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _client_as(identity: Identity | None) -> TestClient:
    if identity is None:
        main.app.dependency_overrides.pop(get_identity, None)
    else:
        main.app.dependency_overrides[get_identity] = lambda: identity
    return TestClient(main.app)


def _cleanup_overrides():
    main.app.dependency_overrides.pop(get_identity, None)


def test_version_constant_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", BORON_VERSION)


def test_version_endpoint_returns_single_source_of_truth():
    admin = Identity(panel_user_id=1, username="adminuser", role="admin",
                     account_id=None, auth_method="session")
    try:
        client = _client_as(admin)
        r = client.get("/api/v1/version")
        assert r.status_code == 200
        assert r.json() == {"version": BORON_VERSION}
    finally:
        _cleanup_overrides()


def test_version_endpoint_visible_to_customers_too():
    customer = Identity(panel_user_id=2, username="custuser", role="customer",
                        account_id=7, auth_method="session")
    try:
        client = _client_as(customer)
        r = client.get("/api/v1/version")
        assert r.status_code == 200
        assert r.json()["version"] == BORON_VERSION
    finally:
        _cleanup_overrides()


def test_version_endpoint_requires_auth():
    """Deliberately NOT public: the exact panel version is not advertised to
    anonymous scanners (the login page shows the build-time bundle version
    instead)."""
    client = _client_as(None)
    r = client.get("/api/v1/version")
    assert r.status_code == 401


def test_installer_sed_extracts_the_version():
    """install.sh greps version.py with a sed expression; run the identical
    expression so a reformat of version.py that would silently break the
    installer's output fails here instead."""
    out = subprocess.run(
        ["sed", "-n", r's/^BORON_VERSION = "\(.*\)"$/\1/p', str(REPO_ROOT / "version.py")],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == BORON_VERSION


def test_vite_config_reads_version_py():
    """The SPA bundle bakes the version in at build time; pin the regex the
    vite config uses against the actual file format."""
    vite_path = REPO_ROOT / "frontend" / "vite.config.js"
    if not vite_path.exists():
        pytest.skip("frontend build configuration is only present in source checkouts")
    vite = vite_path.read_text()
    assert "version.py" in vite
    # The JS regex literal /BORON_VERSION\s*=\s*"([^"]+)"/ must match
    # version.py's actual assignment line.
    js_regex = re.compile(r'BORON_VERSION\s*=\s*"([^"]+)"')
    version_text = (REPO_ROOT / "version.py").read_text()
    found = js_regex.search(version_text)
    assert found and found.group(1) == BORON_VERSION
