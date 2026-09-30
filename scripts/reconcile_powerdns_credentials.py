#!/usr/bin/env python3
"""Repair/rotate the panel's local DNS credential without exposing it in argv.

All replacements are atomic and private from creation. If validation fails,
restore the original values (with safe permissions) and restart the consumers.
This script deliberately does not import cached panel settings.
"""
import argparse
import grp
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile
import urllib.request


def _replace(path, content, mode, gid):
    fd, temporary = tempfile.mkstemp(prefix='.boron-key-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, 0, gid)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
            raise RuntimeError('DNS credentials must be root-owned regular files')
        content = stream.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise RuntimeError('DNS credential configuration is unexpectedly large')
        return content, info


def _setting(content, name, value):
    text = content.decode('utf-8')
    pattern = r'^\s*' + re.escape(name) + r'\s*=.*$'
    # Remove duplicates as well: all consumers must read the same new value.
    lines = [line for line in text.splitlines() if not re.match(pattern, line)]
    return ('\n'.join(lines) + '\n' + name + '=' + value + '\n').encode()


def _service(action, unit):
    subprocess.run(['systemctl', action, unit], check=True, timeout=60,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _healthy(key):
    request = urllib.request.Request('http://127.0.0.1:8081/api/v1/servers/localhost',
                                     headers={'X-API-Key': key})
    # No proxy or redirect may receive the global API credential.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError('PowerDNS API validation failed')


def reconcile(pdns=Path('/etc/powerdns/pdns.d/boron.conf'), conf=Path('/etc/boron'),
              *, rotate=False, restart_panel=True, service=_service, healthy=_healthy, gid=None):
    if not pdns.exists():
        return False
    gid = grp.getgrnam('pdns').gr_gid if gid is None else gid
    targets = [(pdns, 'api-key', 0o640, gid),
               (conf / 'secrets.env', 'POWERDNS_API_KEY', 0o600, 0),
               (conf / 'ssl/powerdns-credentials.ini', 'dns_powerdns_api_key', 0o600, 0)]
    pending = pdns.with_name('.boron-key-rotation-pending')
    rotate = rotate or pending.exists()
    originals = []
    for path, name, mode, group in targets:
        for parent in path.parents:
            if parent.is_symlink() or parent.stat().st_uid != 0 or (parent.stat().st_mode & 0o022 and not parent.stat().st_mode & stat.S_ISVTX):
                raise RuntimeError('DNS configuration parent is not trusted')
        content, info = _read(path)
        originals.append(content)
        rotate = rotate or bool(info.st_mode & 0o007) or bool(info.st_mode & 0o020)
        if info.st_gid != group and info.st_mode & 0o040:
            rotate = True
    if not rotate:
        for (path, _, mode, group), content in zip(targets, originals):
            _replace(path, content, mode, group)
        return False
    # Keep retry intent across rollback, process interruption, and reboot.
    _replace(pending, b'rotation required\n', 0o600, 0)
    key = secrets.token_hex(32)
    try:
        for (path, name, mode, group), content in zip(targets, originals):
            _replace(path, _setting(content, name, key), mode, group)
        service('restart', 'pdns')
        healthy(key)
        if restart_panel:
            service('try-restart', 'boron-provisiond')
    except BaseException:
        for (path, _, mode, group), content in zip(targets, originals):
            _replace(path, content, mode, group)
        service('restart', 'pdns')
        if restart_panel:
            service('try-restart', 'boron-provisiond')
        raise RuntimeError('DNS credential rotation failed; original values restored with private permissions') from None
    pending.unlink()
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rotate', action='store_true')
    parser.add_argument('--defer-panel-restart', action='store_true')
    args = parser.parse_args()
    rotated = reconcile(rotate=args.rotate, restart_panel=not args.defer_panel_restart)
    print('PowerDNS credentials rotated and verified' if rotated else 'PowerDNS credential permissions checked')
