"""Install the panel's current certificate for mandatory FTPS, with rollback.

Used by the updater for existing installations whose FTP service still has
the bootstrap certificate. Panel certificate renewals use panel_ssl_deploy.
"""
from __future__ import annotations

import subprocess

from scripts import panel_ssl_deploy as tls
from shared.config import settings


def reconcile(hostname: str) -> bool:
    certificate = (tls.TLS_DIRECTORY / "panel.crt").read_bytes()
    key = (tls.TLS_DIRECTORY / "panel.key").read_bytes()
    tls.validate_pair(hostname, certificate, key)
    target = tls.FTP_CERT_PATH
    combined = certificate + b"\n" + key
    old = target.read_bytes() if target.exists() else None
    if old == combined:
        tls._wait_for_ftps_certificate(hostname, certificate)
        return False
    try:
        tls._replace(target, combined, 0, 0, 0o600)
        subprocess.run(["systemctl", "restart", "pure-ftpd.service"], check=True, timeout=45)
        subprocess.run(["systemctl", "is-active", "--quiet", "pure-ftpd.service"], check=True, timeout=10)
        tls._wait_for_ftps_certificate(hostname, certificate)
    except Exception:
        if old is None:
            target.unlink(missing_ok=True)
        else:
            tls._replace(target, old, 0, 0, 0o600)
        subprocess.run(["systemctl", "restart", "pure-ftpd.service"], check=False, timeout=45)
        raise
    return True


if __name__ == "__main__":
    print("FTPS certificate updated" if reconcile(settings.panel_hostname) else "FTPS certificate already current")
