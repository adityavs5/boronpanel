"""Symlink-safety of daemon.safeio, the shared create+chown primitive that
closes the root-daemon privilege-escalation class (a planted symlink in an
account's home making root chown/chmod/write an arbitrary target)."""
import os

import pytest

from daemon import safeio


def test_secure_mkdirs_happy_path(tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    leaf = safeio.secure_mkdirs(str(root), "a/b/c", os.getuid(), os.getgid(), 0o750)
    assert os.path.isdir(leaf)
    assert leaf == str(root / "a/b/c")
    assert (os.stat(leaf).st_mode & 0o777) == 0o750


def test_secure_mkdirs_refuses_symlinked_component(tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # Account pre-plants a symlink where root is about to makedirs+chown.
    os.symlink(str(outside), str(root / "evil"))
    with pytest.raises(safeio.UnsafePathError):
        safeio.secure_mkdirs(str(root), "evil/sub", os.getuid(), os.getgid(), 0o750)
    # The symlink target must NOT have been chowned/chmod'd/created into.
    assert not (outside / "sub").exists()


def test_secure_mkdirs_rejects_dotdot(tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    with pytest.raises(safeio.UnsafePathError):
        safeio.secure_mkdirs(str(root), "../escape", os.getuid(), os.getgid())


def test_secure_ensure_file_refuses_symlink(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    target = tmp_path / "target"
    target.write_text("secret")
    original_mode = os.stat(target).st_mode & 0o777
    os.symlink(str(target), str(d / "log"))
    with pytest.raises(safeio.UnsafePathError):
        safeio.secure_ensure_file(str(d), "log", os.getuid(), os.getgid(), 0o640)
    # Target untouched, regardless of the server's umask.
    assert (os.stat(target).st_mode & 0o777) == original_mode


def test_secure_ensure_file_creates_and_sets_mode(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    safeio.secure_ensure_file(str(d), "app.log", os.getuid(), os.getgid(), 0o640)
    assert (d / "app.log").is_file()
    assert (os.stat(d / "app.log").st_mode & 0o777) == 0o640


def test_secure_replace_file_replaces_symlink_dest_without_following(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    target = tmp_path / "target"
    target.write_text("original")
    # Destination is a symlink to a sensitive file; replace must overwrite the
    # link entry, never the target's contents.
    os.symlink(str(target), str(d / "authorized_keys"))
    safeio.secure_replace_file(str(d), "authorized_keys", b"new-key\n", os.getuid(), os.getgid(), 0o600)
    assert target.read_text() == "original"  # target untouched
    assert not os.path.islink(d / "authorized_keys")
    assert (d / "authorized_keys").read_text() == "new-key\n"


def test_beneath_write_rejects_ancestor_substitution_after_mkdir(tmp_path, monkeypatch):
    root = tmp_path / 'tenant'; root.mkdir()
    peer = tmp_path / 'peer'; peer.mkdir(); (peer / 'b').mkdir()
    original = safeio.secure_mkdirs
    def substitute(*args, **kwargs):
        result = original(*args, **kwargs)
        (root / 'a').rename(root / 'retained')
        (root / 'a').symlink_to(peer)
        return result
    monkeypatch.setattr(safeio, 'secure_mkdirs', substitute)
    with pytest.raises(safeio.UnsafePathError):
        safeio.secure_write_file_beneath(str(root), 'a/b/probe', b'ROOT_WRITE', os.getuid(), os.getgid())
    assert not (peer / 'b/probe').exists()


def test_replace_handles_short_writes_and_removes_failed_temporary_files(tmp_path, monkeypatch):
    original = os.write
    monkeypatch.setattr(safeio.os, 'write', lambda fd, data: original(fd, data[:2]))
    safeio.secure_replace_file(str(tmp_path), 'config', b'complete content', os.getuid(), os.getgid())
    assert (tmp_path / 'config').read_bytes() == b'complete content'
    def fail(*args):
        raise OSError('disk write failed')
    monkeypatch.setattr(safeio.os, 'write', fail)
    with pytest.raises(OSError):
        safeio.secure_replace_file(str(tmp_path), 'config', b'replacement', os.getuid(), os.getgid())
    assert (tmp_path / 'config').read_bytes() == b'complete content'
    assert list(tmp_path.iterdir()) == [tmp_path / 'config']


def test_all_path_components_reject_symlinks(tmp_path):
    from daemon import safeio
    import os
    protected = tmp_path / 'protected'
    (protected / 'inner').mkdir(parents=True)
    canary = protected / 'inner/canary'
    canary.write_text('private')
    link = tmp_path / 'tenant-link'
    link.symlink_to(protected, target_is_directory=True)
    assert safeio.secure_read_text(str(link / 'inner'), 'canary') is None
    safeio.secure_unlink(str(link / 'inner'), 'canary')
    with pytest.raises(OSError):
        safeio.secure_replace_file(str(link / 'inner'), 'canary', b'changed', os.getuid(), os.getgid())
    assert canary.read_text() == 'private'


def test_text_reader_rejects_fifo_without_blocking(tmp_path):
    from daemon import safeio
    import os
    os.mkfifo(tmp_path / 'pipe')
    assert safeio.secure_read_text(str(tmp_path), 'pipe') is None
