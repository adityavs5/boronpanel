"""Filesystem actions for application installers, executed with the account UID."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def execute(action, target, payload=None, archive=None):
    from daemon import appinstaller as ai
    from daemon.safeio import secure_replace_file
    root = Path(target)
    payload = payload or {}
    if payload.get('db_socket'):
        ai.settings.mariadb_socket = payload['db_socket']
    if action == 'prepare':
        root.mkdir(parents=True, exist_ok=True, mode=0o750)
    elif action == 'zip':
        ai._extract_zip(archive, target)
    elif action == 'tar':
        with tempfile.TemporaryDirectory(prefix='boron-app-files-') as temporary:
            work = Path(temporary)
            saved = work / 'release.tar.gz'
            size = 0
            with saved.open('wb') as out:
                while chunk := archive.read(1024 * 1024):
                    size += len(chunk)
                    if size > ai.MAX_APP_DOWNLOAD_BYTES:
                        raise ValueError('archive too large')
                    out.write(chunk)
            source = ai._extract_wrapped_tar(saved, work)
            for entry in source.iterdir():
                shutil.move(str(entry), str(root / entry.name))
    elif action == 'static':
        root.mkdir(parents=True, exist_ok=True, mode=0o750)
        for name in ('index.html', 'style.css'):
            content = (ai.APP_TEMPLATES_DIR / 'static' / name).read_text().replace('{{SITE_TITLE}}', payload['title'])
            secure_replace_file(target, name, content.encode(), os.geteuid(), os.getegid(), 0o640)
    elif action == 'joomla-schema':
        output = []
        for name in ('base.sql', 'extensions.sql', 'supports.sql'):
            with (root / 'installation/sql/mysql' / name).open() as stream:
                value = stream.read(16 * 1024 * 1024 + 1)
            if len(value) > 16 * 1024 * 1024:
                raise ValueError('schema too large')
            output.append(value.replace('#__', payload['prefix']))
        return output
    elif action == 'joomla-config':
        ai._write_joomla_configuration(target, payload['db_name'], payload['db_user'],
                                      payload['db_password'], payload['prefix'], payload['title'])
    elif action == 'remove-installation':
        shutil.rmtree(root / 'installation', ignore_errors=True)
    elif action == 'drupal-config':
        directory = root / 'sites/default'; directory.mkdir(parents=True, exist_ok=True, mode=0o750)
        sample = directory / 'default.settings.php'
        content = sample.read_text() if sample.exists() else '<?php\n'
        secure_replace_file(str(directory), 'settings.php', (content + payload['content']).encode(),
                            os.geteuid(), os.getegid(), 0o640)
        (directory / 'files').mkdir(exist_ok=True, mode=0o750)
    elif action == 'laravel-env':
        import re
        env = root / '.env'
        if env.exists():
            content = env.read_text().replace('DB_CONNECTION=sqlite', 'DB_CONNECTION=mysql')
            for key, value in payload['values'].items():
                # JSON quoting is supported by dotenv and preserves whitespace,
                # quotes and special characters in generated credentials.
                line = key + '=' + json.dumps(value)
                if re.search(r'^'+re.escape(key)+r'=', content, re.MULTILINE):
                    content = re.sub(r'^'+re.escape(key)+r'=.*$', lambda _: line, content, flags=re.MULTILINE)
                else:
                    content += '\n' + line + '\n'
            secure_replace_file(target, '.env', content.encode(), os.geteuid(), os.getegid(), 0o640)
    elif action == 'access':
        subprocess.run(['setfacl', '-R', '-m', 'u:nobody:rX', '-d', '-m', 'u:nobody:rX', target],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    else:
        raise ValueError('unknown action')
    return {'ok': True}


def main():
    if os.geteuid() == 0:
        raise RuntimeError('Application filesystem worker must not run as root')
    for key in ('BORON_CONFIG', 'BORON_SECRETS', 'BORON_API_SECRETS'):
        os.environ[key] = '/nonexistent/boron-app-worker/' + key
    action, target = sys.argv[1:3]
    if action in ('zip', 'tar'):
        result = execute(action, target, archive=sys.stdin.buffer)
    else:
        result = execute(action, target, json.load(sys.stdin))
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Application filesystem operation failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
