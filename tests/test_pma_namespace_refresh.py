"""phpMyAdmin's PHP must see the token directory after the namespace policy changes.

OLS persists a native namespace per UID and reuses it. One built before the
token binding was added hid the token directory, so every sign-on was 403.
"""
import os
from types import SimpleNamespace

from daemon import ols


def _layout(tmp_path, monkeypatch, generation_age, policy_age=100):
    monkeypatch.setattr(ols.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=33))
    policy = tmp_path / "boron-nsconf.conf"
    policy.write_text("/var/lib/boron-pma-tokens,bind-try\n")
    generation = tmp_path / "lsns" / "33" / "1"
    generation.mkdir(parents=True)
    (tmp_path / "lsns" / "33" / "persist").write_text("1")
    now = 1_000_000
    os.utime(policy, (now - policy_age, now - policy_age))
    os.utime(generation, (now - generation_age, now - generation_age))
    calls = []
    monkeypatch.setattr(ols, "run", lambda args, **kw: calls.append(args) or SimpleNamespace(ok=True, stdout="", stderr=""))
    return policy, calls


def test_a_namespace_built_before_the_policy_is_rebuilt(tmp_path, monkeypatch):
    policy, calls = _layout(tmp_path, monkeypatch, generation_age=1000)
    assert ols._retire_stale_namespace("www-data", policy, str(tmp_path / "lsns")) is True
    assert calls == [[f"{ols.OLS_SERVER_BASE}/lsns/bin/unmount_ns", "-u", "33"],
                     ["pkill", "-TERM", "-u", "www-data", "-x", "lsphp"]]


def test_a_current_namespace_is_left_alone(tmp_path, monkeypatch):
    policy, calls = _layout(tmp_path, monkeypatch, generation_age=10)
    assert ols._retire_stale_namespace("www-data", policy, str(tmp_path / "lsns")) is False
    assert calls == []


def test_no_persisted_namespace_needs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(ols.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=33))
    policy = tmp_path / "boron-nsconf.conf"
    policy.write_text("")
    monkeypatch.setattr(ols, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    assert ols._retire_stale_namespace("www-data", policy, str(tmp_path / "lsns")) is False
