"""Free MaxMind GeoLite2 IP geolocation (missing-features batch, goal
feature 6's "top countries" breakdown).

**Not auto-provisioned**: MaxMind has required a free account + license key
for GeoLite2 downloads since ~2019 -- there is no anonymous/keyless download
path. This project's own standing policy (ARCHITECTURE.md SS8: did not
register a domain on the operator's behalf, used sslip.io instead) is to
never create external accounts or acquire third-party credentials
autonomously on the operator's behalf. `download_database` therefore takes
an operator-supplied `MAXMIND_LICENSE_KEY` (secrets.env, the same
root-only-secrets-file pattern every other third-party credential in this
project uses -- CLOUDFLARE_API_TOKEN, MARIADB_DAEMON_PASSWORD, ...) and does
nothing until the operator provides one; `lookup_country`/`is_configured`
degrade gracefully (empty top_countries, not an error) in the meantime --
same "absence means default/disabled" convention this project uses
everywhere else a feature depends on operator-supplied config
(webmail_hostname, pma_hostname, update_github_repo).
"""
from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path

from daemon.procutil import run

GEOLITE_DB_PATH = "/var/lib/boron/GeoLite2-Country.mmdb"
GEOLITE_DOWNLOAD_URL = "https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-Country&suffix=tar.gz&license_key="


class GeoIpError(Exception):
    pass


def is_configured() -> bool:
    return Path(GEOLITE_DB_PATH).exists()


def download_database(license_key: str) -> None:
    """Admin-triggered (not automatic) -- fetches and installs the
    GeoLite2-Country database using an operator-supplied license key.
    Never logs the key (redact=) and never persists it (the caller,
    daemon/sitestats.py's RPC entry point, writes it straight into
    secrets.env via the same mechanism every other operator-supplied
    secret in this project uses, not into the SQLite control plane)."""
    if not license_key:
        raise GeoIpError("a MaxMind license key is required")
    with tempfile.TemporaryDirectory() as tmp:
        archive_path = f"{tmp}/geolite2.tar.gz"
        result = run(
            ["curl", "-sSL", "-f", "-o", archive_path, f"{GEOLITE_DOWNLOAD_URL}{license_key}"],
            timeout=60, redact=[license_key],
        )
        if not result.ok:
            raise GeoIpError("failed to download GeoLite2-Country database -- check the license key")
        try:
            with tarfile.open(archive_path) as tar:
                mmdb_member = next((m for m in tar.getmembers() if m.name.endswith(".mmdb")), None)
                if mmdb_member is None:
                    raise GeoIpError("downloaded archive did not contain a .mmdb file")
                extracted = tar.extractfile(mmdb_member)
                if extracted is None:
                    raise GeoIpError("could not read .mmdb file from downloaded archive")
                data = extracted.read()
        except tarfile.TarError as exc:
            raise GeoIpError(f"downloaded file is not a valid archive: {exc}") from exc

    Path(GEOLITE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(f"{GEOLITE_DB_PATH}.tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(GEOLITE_DB_PATH)


_reader = None
_reader_checked_path: str | None = None


def _get_reader():
    global _reader, _reader_checked_path
    if not is_configured():
        return None
    if _reader is None or _reader_checked_path != GEOLITE_DB_PATH:
        try:
            import geoip2.database
        except ImportError:
            return None
        _reader = geoip2.database.Reader(GEOLITE_DB_PATH)
        _reader_checked_path = GEOLITE_DB_PATH
    return _reader


def lookup_country(ip: str) -> str | None:
    """Returns a 2-letter ISO country code, or None if unconfigured, the IP
    isn't in the database (private/reserved ranges, or a genuine miss), or
    the geoip2 package isn't installed -- always a soft failure, never
    raises, since this is a best-effort analytics enrichment, not something
    that should ever break a stats refresh pass."""
    reader = _get_reader()
    if reader is None:
        return None
    try:
        return reader.country(ip).country.iso_code
    except Exception:  # noqa: BLE001 - geoip2.errors.AddressNotFoundError + any other lookup failure
        return None
