"""Real UID boundary; run only in the disposable full-system lab."""
import os
from pathlib import Path
import pwd
import tempfile

import pytest

from daemon import disktree
from daemon.procutil import run


@pytest.mark.skipif(os.geteuid() != 0, reason='requires disposable VM root for UID transition')
def test_disk_probe_cannot_follow_swapped_parent_into_private_directory(monkeypatch):
    try:
        owner = pwd.getpwnam('auditweb')
    except KeyError:
        pytest.skip('disposable lab account auditweb is required')
    with tempfile.TemporaryDirectory(prefix='boron-disk-probe-', dir='/tmp') as temp:
        base = Path(temp)
        base.chmod(0o755)
        own = base / 'own'
        own.mkdir(mode=0o755)
        (own / 'child').mkdir()
        (own / 'child' / 'own-file').write_text('normal')
        private = base / 'private'
        private.mkdir(mode=0o700)
        (private / 'child').mkdir()
        (private / 'child' / 'PRIVATE-CANARY').write_text('protected')
        alias = base / 'alias'
        alias.symlink_to(own, target_is_directory=True)
        stale_path = str(alias / 'child')
        monkeypatch.setattr(disktree, '_resolve_dir', lambda *args: (stale_path, str(base)))
        normal = disktree.get_top_files({'username': owner.pw_name})
        assert len(normal['files']) == 1 and normal['files'][0]['path'].endswith('own-file')
        alias.unlink()
        alias.symlink_to(private, target_is_directory=True)
        old = run(['find', stale_path, '-type', 'f', '-printf', '%f'])
        assert old.ok and 'PRIVATE-CANARY' in old.stdout
        with pytest.raises(disktree.DiskTreeError, match='Permission denied'):
            disktree.get_top_files({'username': owner.pw_name})
