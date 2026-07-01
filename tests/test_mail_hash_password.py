"""Real, offline tests for daemon/mail.py's hash_password -- and,
specifically, a regression test locking in a real security bug found
while building Phase 3 feature 10 (the password manager): the password
must never appear in the argument list daemon/procutil.py's run() logs
(`logger.info("exec: %s", " ".join(args))`), only piped via stdin.
"""
from __future__ import annotations

from daemon import mail


def test_hash_password_produces_argon2id_hash():
    result = mail.hash_password("a-real-test-password-123")
    assert result.startswith("{ARGON2ID}")


def test_hash_password_hash_verifies_against_original(tmp_path):
    from daemon.procutil import run

    result = mail.hash_password("a-real-test-password-123")
    verify = run(["doveadm", "pw", "-t", result, "-p", "a-real-test-password-123"], timeout=15)
    assert verify.ok
    assert "(verified)" in verify.stdout


def test_hash_password_never_puts_password_in_logged_argv(monkeypatch):
    """Regression test: found live that this call originally passed the
    password via `-p <password>`, which daemon/procutil.py's run()
    unconditionally logs in full (`logger.info("exec: %s", " ".join(args))`)
    -- a real violation of "passwords never logged anywhere," fixed by
    piping the password via stdin instead."""
    captured = {}

    def fake_run(args, input_text=None, timeout=30.0, check=False):
        captured["args"] = args
        captured["input_text"] = input_text
        from daemon.procutil import ProcResult

        return ProcResult(args=args, returncode=0, stdout="{ARGON2ID}$fake$", stderr="")

    monkeypatch.setattr(mail, "run", fake_run)
    secret = "supersecretpassword123"

    mail.hash_password(secret)

    assert secret not in captured["args"]
    assert all(secret not in str(arg) for arg in captured["args"])
    assert secret in captured["input_text"]  # only ever reaches doveadm via stdin
