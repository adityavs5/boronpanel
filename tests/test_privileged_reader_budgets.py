import os
import multiprocessing
from types import SimpleNamespace

import pytest

from daemon import logs, maillog, safeio, sshkeys


@pytest.fixture()
def tenant_logs(tmp_path, monkeypatch):
    directory = tmp_path / "demo1" / "logs"
    directory.mkdir(parents=True)
    monkeypatch.setattr(logs.settings, "home_base", str(tmp_path))
    return directory


def test_inventory_supports_multiple_rotations_per_day_and_keeps_active_first(tenant_logs):
    (tenant_logs / "php-error.log").write_text("active\n")
    for day in range(90):
        for rotation in range(3):
            (tenant_logs / f"php-error.log.{day}-{rotation}").write_text("rotated\n")
    rows = logs._safe_segments("demo1", "php-error.log")
    assert len(rows) == 271 and rows[0]["active"]


def test_inventory_limits_unrelated_entries_lazily_and_closes_iterator(tenant_logs, monkeypatch):
    scanned = []
    closed = []

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            closed.append(True)

        def __iter__(self):
            for i in range(100):
                scanned.append(i)
                yield SimpleNamespace(name=f"unrelated-{i}")

    with monkeypatch.context() as scoped:
        scoped.setattr(logs, "MAX_LOG_DIRECTORY_ENTRIES", 3)
        scoped.setattr(logs.os, "scandir", lambda _: Entries())
        scoped.setattr(
            logs.os,
            "listdir",
            lambda *_: (_ for _ in ()).throw(AssertionError("eager listdir")),
        )
        with pytest.raises(logs.LogsError, match="too large"):
            logs._safe_segments("demo1", "php-error.log")
    assert len(scanned) == 4 and closed == [True]


def test_segment_limit_rejects_incomplete_inventory(tenant_logs, monkeypatch):
    monkeypatch.setattr(logs, "MAX_LOG_SEGMENTS", 2)
    for name in ["php-error.log", "php-error.log.1"]:
        (tenant_logs / name).write_text("log\n")
    assert len(logs._safe_segments("demo1", "php-error.log")) == 2
    (tenant_logs / "php-error.log.2").write_text("log\n")
    with pytest.raises(logs.LogsError, match="too many log segments"):
        logs._safe_segments("demo1", "php-error.log")


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory"])
def test_mail_tail_refuses_nonregular_tenant_files_before_read(tenant_logs, monkeypatch, kind):
    path = tenant_logs / "php-mail.log"
    if kind == "symlink":
        path.symlink_to("/dev/zero")
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()
    monkeypatch.setattr(maillog.os, "pread", lambda *_: (_ for _ in ()).throw(AssertionError("unsafe read")))
    with pytest.raises((OSError, safeio.UnsafePathError)):
        maillog._tail_bytes(str(path), 32, username="demo1")


def test_mail_tail_is_byte_bounded_even_with_a_single_long_line(tenant_logs):
    path = tenant_logs / "php-mail.log"
    path.write_bytes(b'x' * 1000 + b'\nlast\n')
    assert maillog._tail_bytes(str(path), 32, username="demo1") == ["last"]
    path.write_bytes(b'x' * 1000)
    assert maillog._tail_bytes(str(path), 32, username="demo1") == []


def test_mail_tail_rejects_symlinked_parent_directory(tmp_path, monkeypatch):
    home = tmp_path / "demo1"
    home.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "php-mail.log").write_text("other tenant\n")
    (home / "logs").symlink_to(other, target_is_directory=True)
    monkeypatch.setattr(maillog.settings, "home_base", str(tmp_path))
    with pytest.raises((OSError, safeio.UnsafePathError)):
        maillog._tail_bytes(str(home / "logs/php-mail.log"), 32, username="demo1")


def test_authorized_key_inventory_rejects_fanout_and_oversize_without_subprocess(monkeypatch):
    monkeypatch.setattr(sshkeys, "_inspect", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("keygen reached")))
    with pytest.raises(sshkeys.SshKeyError, match="256"):
        sshkeys._bounded_authorized_lines("x\n" * 257)
    with pytest.raises(sshkeys.SshKeyError, match="8192"):
        sshkeys._bounded_authorized_lines("x" * 8193)
    with pytest.raises(sshkeys.SshKeyError, match="1 MiB"):
        sshkeys._bounded_authorized_lines("x" * (sshkeys.MAX_AUTHORIZED_BYTES + 1))


def test_authorized_inventory_keeps_comments_and_unicode_character_budget():
    text = "# managed elsewhere\nssh-ed25519 AAAA " + "ü" * 8000 + "\n"
    lines = sshkeys._bounded_authorized_lines(text)
    assert len(lines) == 2 and lines[0].startswith("#") and lines[1].endswith("ü" * 8000)


def test_inspections_share_one_decreasing_deadline(monkeypatch):
    times = iter([0, 0, 9, 11])
    calls = []
    monkeypatch.setattr(sshkeys.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(sshkeys, "_inspect", lambda line, *, timeout: calls.append(timeout) or {"fingerprint": line})
    with pytest.raises(sshkeys.SshKeyError, match="timed out"):
        list(sshkeys._inspect_lines(["one", "two", "three"]))
    assert calls == [10, 1]


def test_authorized_key_write_refuses_overflow_before_replacing(monkeypatch):
    monkeypatch.setattr(sshkeys.safeio, "secure_replace_file", lambda *_: (_ for _ in ()).throw(AssertionError("rewrite reached")))
    with pytest.raises(sshkeys.SshKeyError, match="256"):
        sshkeys._write_authorized_keys("demo1", ["malformed"] * 257, 5001, 5001)


def _parse_hostile_php_lines():
    assert maillog.parse_php_mail_log(["mail() on [" * 150000], "demo1") == []
    assert maillog.parse_php_mail_log(["mail() on [/home/demo1/site.php:" + "1" * 5000 + "]: To: x@example.com"], "demo1") == []


def test_php_log_parser_avoids_repeated_prefix_backtracking():
    process = multiprocessing.get_context("fork").Process(target=_parse_hostile_php_lines)
    process.start()
    try:
        process.join(timeout=3)
        assert not process.is_alive(), "mail parser exceeded its process deadline"
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
        process.join()


def test_authorized_line_budget_counts_crlf_once():
    assert len(sshkeys._bounded_authorized_lines("# comment\r\n" * 1000)) == 1000


def test_php_log_parser_keeps_unicode_offsets_and_header_separator(tenant_logs):
    script = str(tenant_logs.parent / "site.php")
    rows = maillog.parse_php_mail_log([f"İ prefix MAIL() on [{script}:7]: To: İ@example.com -- Headers: Subject: hello"], "demo1")
    assert rows == [{"username": "demo1", "script": script, "line": 7, "to": "İ@example.com"}]
