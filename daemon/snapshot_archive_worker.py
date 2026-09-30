"""Confined portable archive builder. Invoked only by the privileged job runner."""
import fnmatch
import gzip  # Load compression code before filesystem confinement.
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile

# The script directory is service-owned; isolated Python omits tenant paths.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from landlock_exec import _restrict


def path_size(path):
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
        for name in files:
            item = Path(root) / name
            try:
                if not item.is_symlink():
                    total += item.stat().st_size
            except FileNotFoundError:
                continue
    return total


def build(payload):
    work = Path(payload['work'])
    _restrict(payload['roots'], [] if payload.get('measure_only') else [str(work)], [], [])
    # No traversal of an account-controlled source precedes confinement.
    paths = payload['paths']
    options = payload['options']
    estimated = sum(path_size(Path(value)) for value in paths)
    if payload.get('measure_only'):
        print(json.dumps({'estimated_source_bytes': estimated}))
        return
    artifact = Path(payload['artifact'])
    home = Path(payload['home'])
    mail = Path(payload['mail'])
    compressed = options['mode'] == 'compressed'
    free=shutil.disk_usage(work).free
    reserve=max(256*1024*1024,int(estimated*.1))
    if free < estimated+reserve:
        raise ValueError(f'Portable archive needs about {estimated+reserve} bytes of staging space; only {free} bytes are free')
    inventory=[]
    for value in paths:
        source=Path(value)
        if source==home:arcname=Path('account/home')
        elif source.is_relative_to(home):arcname=Path('account/home')/source.relative_to(home)
        elif source.is_relative_to(mail):arcname=Path('account/mail')/source.relative_to(mail)
        else:arcname=Path('account/metadata')
        inventory.append({'source':str(source),'archive_path':arcname.as_posix(),'size_bytes':path_size(source)})
    manifest = {**payload['manifest'], 'inventory': inventory}
    manifest_path=work/'manifest.json';manifest_path.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    patterns=options.get('exclude_patterns',[])
    def archive_filter(info):
        relative=info.name.removeprefix('account/')
        return None if any(fnmatch.fnmatch(relative,pattern) or fnmatch.fnmatch(Path(relative).name,pattern) for pattern in patterns) else info
    try:
        with tarfile.open(artifact,'w:gz' if compressed else 'w',format=tarfile.PAX_FORMAT) as archive:
            archive.add(manifest_path,arcname='account/manifest.json',recursive=False)
            for item,entry in zip(paths,inventory):archive.add(item,arcname=entry['archive_path'],recursive=True,filter=archive_filter)
    except (OSError,tarfile.TarError) as exc:
        raise ValueError(f'Could not create portable account archive: {exc}') from exc


if __name__ == '__main__':
    build(json.load(sys.stdin))
