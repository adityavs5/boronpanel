#!/usr/bin/python3
"""Idempotently connect OpenLiteSpeed WebAdmin to Boron's host policy."""
from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import tempfile
from pathlib import Path

OLS_CONF = Path('/usr/local/lsws/conf/httpd_config.conf')
OLS_ADMIN = Path('/usr/local/lsws/admin/conf')
PANEL_TLS = Path('/etc/boron/ssl/api')


def atomic_install(source: Path, target: Path, uid: int, gid: int, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix='.boron-webadmin-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
            os.fchown(handle.fileno(), uid, gid)
            os.fchmod(handle.fileno(), mode)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> None:
    if not OLS_ADMIN.is_dir():
        return
    lsadm = pwd.getpwnam('lsadm')
    if OLS_CONF.exists():
        os.chown(OLS_CONF, lsadm.pw_uid, lsadm.pw_gid)
        os.chmod(OLS_CONF, 0o640)
    certificate, key = PANEL_TLS / 'panel.crt', PANEL_TLS / 'panel.key'
    if certificate.is_file() and key.is_file():
        atomic_install(certificate, OLS_ADMIN / 'webadmin.crt', lsadm.pw_uid, lsadm.pw_gid, 0o400)
        atomic_install(key, OLS_ADMIN / 'webadmin.key', lsadm.pw_uid, lsadm.pw_gid, 0o400)
    status = subprocess.run(['ufw', 'status'], capture_output=True, text=True, timeout=20)
    if status.returncode == 0 and 'Status: active' in status.stdout and '7080/tcp' not in status.stdout:
        subprocess.run(['ufw', 'allow', '7080/tcp', 'comment', 'boron-ols-webadmin'], check=True, timeout=20)
    subprocess.run(['systemctl', 'restart', 'lshttpd.service'], check=True, timeout=60)


if __name__ == '__main__':
    main()
