#!/usr/bin/env python3
"""Account-user WordPress worker. Never run as root. Input is private stdin JSON."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time


def main(c):
    if os.geteuid() == 0:
        raise RuntimeError('WordPress operations must run as the hosting account')
    root = Path(c['root']).resolve(strict=True)
    home = Path.home().resolve()
    if not root.is_relative_to(home) or root == home:
        raise RuntimeError('Site directory must be inside the account home')
    store = home / '.boron-wordpress' / hashlib.sha256(str(root).encode()).hexdigest()[:24]
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(store, 0o700)

    def wp(*args, at=root):
        r = subprocess.run([c['php'], c['phar'], f'--path={at}', '--no-color', *args], capture_output=True, text=True, timeout=600)
        if r.returncode:
            raise RuntimeError((r.stderr or r.stdout or 'WordPress command failed')[-2000:])
        return r.stdout.strip()

    # Subfolder installations have their own database and lifecycle.
    nested_sites = {p.name for p in root.iterdir() if p.is_dir() and (p / 'wp-config.php').is_file()}

    def web_access(at):
        result = subprocess.run(['setfacl', '-R', '-m', 'u:nobody:rX', '-d', '-m', 'u:nobody:rX', str(at)], capture_output=True, text=True, timeout=120)
        if result.returncode: raise RuntimeError('Could not restore web-server file access: ' + result.stderr[-500:])
        (at / 'wp-config.php').chmod(0o600)

    def capture():
        name = time.strftime('%Y%m%d-%H%M%S-') + secrets.token_hex(4) + '.tar.gz'
        dest = store / (name + '.partial')
        with tempfile.TemporaryDirectory(dir=store) as scratch:
            sql = Path(scratch) / 'database.sql'
            wp('db', 'export', str(sql), '--single-transaction')
            with tarfile.open(dest, 'w:gz') as archive:
                def safe(info):
                    if info.issym() or info.islnk() or not (info.isfile() or info.isdir()):
                        return None
                    if '/boron-login-' in info.name: return None
                    parts = Path(info.name).parts
                    if len(parts) > 1 and parts[0] == 'files' and parts[1] in nested_sites: return None
                    return info
                archive.add(root, arcname='files', filter=safe)
                archive.add(sql, arcname='database.sql')
        dest.chmod(0o600)
        dest = dest.rename(store / name)
        return {'name': name, 'size': dest.stat().st_size, 'created_at': dest.stat().st_mtime}

    action = c['action']
    if action == 'backup': return capture()
    if action == 'backups':
        return {'backups': [{'name': f.name, 'size': f.stat().st_size, 'created_at': f.stat().st_mtime} for f in sorted(store.glob('*.tar.gz'), reverse=True) if f.is_file() and not f.is_symlink()]}
    if action == 'restore':
        name = c['backup']
        if not re.fullmatch(r'[0-9]{8}-[0-9]{6}-[a-f0-9]{8}\.tar\.gz', name): raise RuntimeError('Invalid backup name')
        source = store / name
        if source.is_symlink(): raise RuntimeError('Invalid backup file')
        with tempfile.TemporaryDirectory(dir=store) as scratch:
            with tarfile.open(source) as archive:
                total = 0
                for m in archive.getmembers():
                    p = Path(m.name)
                    if p.is_absolute() or '..' in p.parts or p.parts[0] not in ('files', 'database.sql') or not (m.isfile() or m.isdir()):
                        raise RuntimeError('Backup contains unsafe paths or links')
                    total += m.size
                    if total > 10 * 1024 ** 3: raise RuntimeError('Backup exceeds the 10 GB restore limit')
                archive.extractall(scratch, filter='data')
            extracted = Path(scratch) / 'files'
            if not (extracted / 'wp-config.php').is_file() or not (Path(scratch) / 'database.sql').is_file():
                raise RuntimeError('Backup is missing WordPress files or its database')
            safety = capture()
            # Keep existing DB credentials: a backup restores content into this site.
            config = (root / 'wp-config.php').read_bytes()
            def replace_files(tree):
                for f in root.iterdir():
                    if f.name in nested_sites: continue
                    if f.is_dir() and not f.is_symlink(): shutil.rmtree(f)
                    else: f.unlink()
                shutil.copytree(tree, root, dirs_exist_ok=True, ignore=lambda directory,names: [n for n in names if Path(directory)==tree and n in nested_sites])
                (root / 'wp-config.php').write_bytes(config)
                web_access(root)
            try:
                replace_files(extracted)
                wp('db', 'import', str(Path(scratch) / 'database.sql'))
            except Exception as original:
                try:
                    with tempfile.TemporaryDirectory(dir=store) as rollback:
                        with tarfile.open(store / safety['name']) as previous:
                            previous.extractall(rollback, filter='data')
                        replace_files(Path(rollback) / 'files')
                        wp('db', 'import', str(Path(rollback) / 'database.sql'))
                except Exception:
                    raise RuntimeError(f"Restore failed. Recovery backup: {safety['name']}. Contact your administrator to recover this site.") from original
                raise RuntimeError(f"Restore failed; the original website was recovered. Safety backup: {safety['name']}") from original
        return {'message': 'Backup restored', 'safety_backup': safety['name']}
    if action == 'clone':
        target = Path(c['target']).resolve()
        if not target.is_relative_to(home) or target == home or target == root or root.is_relative_to(target) or (target.is_relative_to(root) and target.parent != root):
            raise RuntimeError('Choose a separate, empty site directory')
        target.mkdir(parents=True, exist_ok=True)
        if any(p.name != '.well-known' and not (p.name == 'error_pages' and p.is_dir() and not any(p.iterdir())) for p in target.iterdir()): raise RuntimeError('The destination must be empty')
        old_url = wp('option', 'get', 'home')
        prefix = wp('config', 'get', 'table_prefix', '--type=variable')
        if not re.fullmatch(r'[A-Za-z0-9_]+', prefix): raise RuntimeError('Unsupported database table prefix')
        try:
            with tempfile.TemporaryDirectory(dir=store) as scratch:
                sql = Path(scratch) / 'clone.sql'
                wp('db', 'export', str(sql), '--single-transaction')
                # Symlinks are not copied into a new executable website.
                def ignore(directory, names):
                    return [n for n in names if Path(directory, n).is_symlink() or n.startswith('boron-login-') or n in ('.well-known', 'error_pages') or Path(directory,n).resolve() == target or (Path(directory)==root and n in nested_sites)]
                shutil.copytree(root, target, dirs_exist_ok=True, ignore=ignore)
                def quote(v): return "'" + str(v).replace('\\', '\\\\').replace("'", "\\'") + "'"
                conf = '<?php\n'
                for k, v in {'DB_NAME': c['database']['db_name'], 'DB_USER': c['database']['db_user'], 'DB_PASSWORD': c['database']['password'], 'DB_HOST': c.get('db_host', 'localhost'), 'DB_CHARSET': 'utf8mb4', 'DB_COLLATE': ''}.items():
                    conf += f'define({quote(k)}, {quote(v)});\n'
                for k in ['AUTH_KEY','SECURE_AUTH_KEY','LOGGED_IN_KEY','NONCE_KEY','AUTH_SALT','SECURE_AUTH_SALT','LOGGED_IN_SALT','NONCE_SALT']:
                    conf += f'define({quote(k)}, {quote(secrets.token_urlsafe(48))});\n'
                conf += f'$table_prefix = {quote(prefix)};\n' + "if (!defined('ABSPATH')) define('ABSPATH', __DIR__ . '/');\nrequire_once ABSPATH . 'wp-settings.php';\n"
                (target / 'wp-config.php').write_text(conf)
                (target / 'wp-config.php').chmod(0o600)
                wp('db', 'import', str(sql), at=target)
                wp('search-replace', old_url, c['url'], '--all-tables-with-prefix', '--skip-columns=guid', at=target)
                wp('option', 'update', 'home', c['url'], at=target)
                wp('option', 'update', 'siteurl', c['url'], at=target)
                wp('option', 'update', 'blog_public', '0', at=target)
                web_access(target)
        except Exception:
            # Only the account worker removes its newly copied destination.
            for f in target.iterdir():
                if f.name in ('.well-known','error_pages'): continue
                if f.is_dir() and not f.is_symlink(): shutil.rmtree(f)
                else: f.unlink()
            raise
        return {'message': 'Clone ready. Search engine indexing is disabled.', 'url': c['url']}
    raise RuntimeError('Unknown operation')

if __name__ == '__main__':
    try:
        print(json.dumps(main(json.load(sys.stdin))))
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
