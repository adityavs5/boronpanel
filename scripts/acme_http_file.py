#!/usr/bin/env python3
"""Unprivileged HTTP challenge writer. Never run this helper as root."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from daemon import safeio


def main():
    if os.geteuid() == 0:
        raise RuntimeError('ACME file helper must run as the hosting user')
    raw = sys.stdin.read(4097)
    if len(raw) > 4096:
        raise ValueError('Oversized ACME file request')
    data = json.loads(raw)
    home = Path(data['home'])
    relative = Path(data['docroot']).relative_to(home) / '.well-known' / 'acme-challenge'
    token = data['token']
    safeio._reject_name(token)
    if data['action'] == 'auth':
        directory = safeio._open_dir_nofollow(data['docroot'])
        try:
            for part in ('.well-known', 'acme-challenge'):
                try:
                    os.mkdir(part, 0o755, dir_fd=directory)
                except FileExistsError:
                    pass
                child = safeio._open_dir_nofollow(part, dir_fd=directory)
                os.close(directory)
                directory = child
            safeio._replace_file_at_fd(directory, token, data['validation'].encode(),
                                      os.geteuid(), os.getegid(), 0o644)
        finally:
            os.close(directory)
    elif data['action'] == 'cleanup':
        existing = safeio.secure_read_text(str(home / relative), token, max_bytes=1024)
        if existing == data['validation']:
            safeio.secure_unlink(str(home / relative), token)
    else:
        raise ValueError('Invalid ACME file action')


if __name__ == '__main__':
    main()
