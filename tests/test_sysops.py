from daemon import sysops


def test_recycle_php_workers_does_not_raise_for_nonexistent_user():
    """No test-environment Linux user exists to actually recycle -- pkill
    finding zero matches is the expected, harmless outcome here; the
    real assertion is that this never raises (set_php_ini/reset_php_ini
    call it unconditionally after every change, never inside a try/except,
    see daemon/handlers_php_ini.py)."""
    sysops.recycle_php_workers("no-such-forgehost-test-user")


def test_recycle_php_workers_scopes_to_username_and_lsphp(monkeypatch):
    calls = []
    monkeypatch.setattr(sysops, "run", lambda args, **kw: calls.append(args))
    sysops.recycle_php_workers("demo1")
    assert calls == [["pkill", "-u", "demo1", "-f", "lsphp"]]
