"""Filesystem half of installation: always execute after dropping to the tenant UID."""
from pathlib import Path
import json
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if os.geteuid() == 0:
        raise RuntimeError('WordPress filesystem worker must not run as root')
    # This worker needs no panel configuration or root secrets. Missing
    # paths select defaults without granting access to /etc/boron.
    for key in ("BORON_CONFIG", "BORON_SECRETS", "BORON_API_SECRETS"):
        os.environ[key] = "/nonexistent/boron-wordpress-worker/" + key
    from daemon import wordpress
    action, target = sys.argv[1:3]
    if action == 'prepare':
        Path(target).mkdir(parents=True, exist_ok=True, mode=0o750)
    elif action == 'extract':
        # Parent supplies an already-open, seekable archive fd. The tenant
        # needs no access to the root staging directory or its other files.
        wordpress._extract_wordpress(sys.stdin.buffer, target)
    elif action == 'config':
        data = json.load(sys.stdin)
        wordpress.settings.mariadb_socket = data['db_socket']
        wordpress._write_wp_config(target, data['db_name'], data['db_user'], data['db_password'])
    else:
        raise RuntimeError('Unknown WordPress filesystem action')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Database credentials arrive on stdin and must never enter logs.
        print('WordPress filesystem operation failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
