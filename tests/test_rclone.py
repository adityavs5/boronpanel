import pytest

from daemon import rclone
from daemon.procutil import ProcResult


@pytest.fixture()
def fake_run(monkeypatch, tmp_path):
    calls = []

    def _run(args, timeout=30):
        calls.append(args)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rclone, "run", _run)
    monkeypatch.setattr(rclone, "RCLONE_CONFIG_PATH", str(tmp_path / "rclone.conf"))
    return calls


def test_create_remote_keeps_config_values_out_of_argv(fake_run):
    rclone.create_remote("myremote", "s3", {"provider": "AWS", "access_key_id": "AKIA123"})
    args = fake_run[0]
    assert args[:5] == [rclone.settings.rclone_bin, "--config", rclone.RCLONE_CONFIG_PATH, "config", "create"]
    assert "myremote" in args
    assert "s3" in args
    assert "--non-interactive" in args
    assert not any("AWS" in arg or "AKIA123" in arg for arg in args)
    config = open(rclone.RCLONE_CONFIG_PATH).read()
    assert "provider = AWS" in config
    assert "access_key_id = AKIA123" in config


def test_create_remote_raises_on_failure(monkeypatch):
    monkeypatch.setattr(rclone, "run", lambda args, timeout=30: ProcResult(args=args, returncode=1, stdout="", stderr="bad type"))
    with pytest.raises(rclone.RcloneError):
        rclone.create_remote("myremote", "bogus", {})


def test_delete_remote_is_idempotent(fake_run):
    rclone.delete_remote("nonexistent")  # must not raise
    assert any(a[:5] == [rclone.settings.rclone_bin, "--config", rclone.RCLONE_CONFIG_PATH, "config", "delete"] for a in fake_run)


def test_remote_exists_checks_listremotes_output(monkeypatch):
    monkeypatch.setattr(rclone, "run", lambda args, timeout=30: ProcResult(args=args, returncode=0, stdout="myremote:\nother:\n", stderr=""))
    assert rclone.remote_exists("myremote") is True
    assert rclone.remote_exists("missing") is False


def test_copy_raises_on_failure(monkeypatch):
    monkeypatch.setattr(rclone, "run", lambda args, timeout=3600: ProcResult(args=args, returncode=1, stdout="", stderr="network error"))
    with pytest.raises(rclone.RcloneError):
        rclone.copy("/local/file", "remote:path/file")


def test_size_parses_json_output(monkeypatch):
    monkeypatch.setattr(rclone, "run", lambda args, timeout=30: ProcResult(args=args, returncode=0, stdout='{"count": 3, "bytes": 12345}', stderr=""))
    assert rclone.size("remote:path") == 12345


def test_size_returns_zero_on_failure(monkeypatch):
    monkeypatch.setattr(rclone, "run", lambda args, timeout=30: ProcResult(args=args, returncode=1, stdout="", stderr="not found"))
    assert rclone.size("remote:path") == 0
