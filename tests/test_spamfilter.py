import pytest

from daemon import spamfilter as sf
from shared.validation import ValidationError, validate_spam_threshold


@pytest.fixture()
def fake_virtual_config_base(tmp_path, monkeypatch):
    base = tmp_path / "spamassassin"
    monkeypatch.setattr(sf, "VIRTUAL_CONFIG_BASE", str(base))
    return base


def test_validate_spam_threshold_accepts_typical_values():
    assert validate_spam_threshold(5) == 5.0
    assert validate_spam_threshold("3.5") == 3.5
    assert validate_spam_threshold(1.0) == 1.0
    assert validate_spam_threshold(20.0) == 20.0


@pytest.mark.parametrize("bad", [0.5, 20.1, "not-a-number", None, -5])
def test_validate_spam_threshold_rejects_out_of_range(bad):
    with pytest.raises(ValidationError):
        validate_spam_threshold(bad)


def test_apply_domain_spam_settings_enabled_with_default_writes_no_file(fake_virtual_config_base):
    sf.apply_domain_spam_settings("demo1.example", enabled=True, threshold=None)
    assert not sf._user_prefs_path("demo1.example").exists()


def test_apply_domain_spam_settings_enabled_with_custom_threshold(fake_virtual_config_base):
    sf.apply_domain_spam_settings("demo1.example", enabled=True, threshold=3.0)
    content = sf._user_prefs_path("demo1.example").read_text()
    assert "required_score 3.0" in content


def test_apply_domain_spam_settings_disabled_writes_high_threshold(fake_virtual_config_base):
    sf.apply_domain_spam_settings("demo1.example", enabled=False, threshold=None)
    content = sf._user_prefs_path("demo1.example").read_text()
    assert f"required_score {sf.DISABLED_THRESHOLD}" in content


def test_apply_domain_spam_settings_disabled_ignores_threshold_value(fake_virtual_config_base):
    """Even if a customer's override threshold is still set, disabling
    must win -- required_score reflects DISABLED_THRESHOLD, not the
    stale override."""
    sf.apply_domain_spam_settings("demo1.example", enabled=False, threshold=3.0)
    content = sf._user_prefs_path("demo1.example").read_text()
    assert f"required_score {sf.DISABLED_THRESHOLD}" in content


def test_apply_domain_spam_settings_switching_back_to_default_removes_file(fake_virtual_config_base):
    sf.apply_domain_spam_settings("demo1.example", enabled=True, threshold=3.0)
    assert sf._user_prefs_path("demo1.example").exists()
    sf.apply_domain_spam_settings("demo1.example", enabled=True, threshold=None)
    assert not sf._user_prefs_path("demo1.example").exists()


def test_remove_domain_spam_settings_is_idempotent(fake_virtual_config_base):
    sf.remove_domain_spam_settings("never-existed.example")  # must not raise
    sf.apply_domain_spam_settings("demo1.example", enabled=True, threshold=3.0)
    sf.remove_domain_spam_settings("demo1.example")
    assert not sf._user_prefs_path("demo1.example").exists()
    sf.remove_domain_spam_settings("demo1.example")  # second call, still no-op


def test_global_sieve_script_is_valid_sieve(isolated_db):
    """Real sievec compile (fast, offline, no root) -- same "compile the
    generated script before trusting it" discipline
    daemon/autoresponder.py's own tests use. Missing-features batch, goal
    feature 5 replaced the old static GLOBAL_SIEVE_SOURCE constant with
    build_global_sieve_source() (per-mailbox blacklist/whitelist blocks
    generated ahead of the same static Junk-filing tail) -- with no
    SpamFilterEntry rows this reduces to just that tail, so this still
    covers the original "the static script is valid Sieve" intent."""
    sf._validate_sieve(sf.build_global_sieve_source())


def test_global_sieve_install_rolls_back_if_compile_fails(monkeypatch, tmp_path):
    from daemon.procutil import ProcResult
    from daemon.configtx import StepResult

    target = tmp_path / "global.sieve"
    target.write_text("old valid script")
    monkeypatch.setattr(sf, "GLOBAL_SIEVE_PATH", str(target))
    monkeypatch.setattr(sf, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(sf, "_validate_sieve", lambda content: None)
    monkeypatch.setattr(sf, "_dovecot_reload", lambda: StepResult(True))
    monkeypatch.setattr(sf, "_dovecot_verify", lambda: StepResult(True))
    calls = []

    def fake_run(args, timeout=None):
        calls.append(args)
        return ProcResult(args=args, returncode=1 if len(calls) == 1 else 0,
                          stdout="", stderr="compile failed")

    monkeypatch.setattr(sf, "run", fake_run)
    with pytest.raises(sf.SpamFilterError, match="global Sieve update failed"):
        sf._install_global_sieve_script("new valid script")
    assert target.read_text() == "old valid script"
    assert len(calls) == 2, "rollback must recompile the restored script"


# --- master.cf rendering (pure string transform, no subprocess) -----------

STOCK_MASTER_CF = """\
smtp      inet  n       -       y       -       -       smtpd
submission inet n       -       y       -       -       smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=encrypt
pickup    unix  n       -       y       60      1       pickup
smtp      unix  -       -       y       -       -       smtp
relay     unix  -       -       y       -       -       smtp
"""


def test_render_master_cf_adds_content_filter_only_to_inet_smtp():
    rendered = sf._render_master_cf(STOCK_MASTER_CF)
    lines = rendered.splitlines()
    smtp_inet_idx = next(i for i, ln in enumerate(lines) if ln.startswith("smtp") and "inet" in ln)
    assert lines[smtp_inet_idx + 1].strip() == "-o content_filter=scan:"
    # the *other* "smtp" line (the outbound relay transport, "smtp unix")
    # must NOT get the override -- confirmed by checking there's exactly
    # one content_filter line in the whole rendered file.
    assert rendered.count("content_filter=scan:") == 1


def test_render_master_cf_does_not_touch_submission():
    rendered = sf._render_master_cf(STOCK_MASTER_CF)
    submission_idx = rendered.splitlines().index("submission inet n       -       y       -       -       smtpd")
    next_line = rendered.splitlines()[submission_idx + 1]
    assert "content_filter" not in next_line


def test_render_master_cf_adds_scan_service_block():
    rendered = sf._render_master_cf(STOCK_MASTER_CF)
    assert "scan      unix  -       n       n       -       10      pipe" in rendered
    assert "argv=/usr/bin/spamc" in rendered


def test_render_master_cf_is_idempotent_second_call_does_not_duplicate():
    once = sf._render_master_cf(STOCK_MASTER_CF)
    twice = sf._render_master_cf(once)
    assert twice.count("content_filter=scan:") == 1
    assert twice.count("argv=/usr/bin/spamc") == 1


def test_render_master_cf_raises_if_no_smtp_inet_line():
    with pytest.raises(sf.SpamFilterError):
        sf._render_master_cf("submission inet n - y - - smtpd\n")


# --- global default threshold (DB-backed) ----------------------------------


def test_set_and_get_global_default_threshold(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(sf, "LOCAL_CF_PATH", str(tmp_path / "local.cf"))
    result = sf.set_global_default_threshold(6.5)
    assert result["default_threshold"] == 6.5
    assert sf.get_global_default_threshold() == 6.5
    content = (tmp_path / "local.cf").read_text()
    assert "required_score 6.5" in content


def test_set_global_default_threshold_rejects_out_of_range(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(sf, "LOCAL_CF_PATH", str(tmp_path / "local.cf"))
    with pytest.raises(ValidationError):
        sf.set_global_default_threshold(500)


def test_get_global_default_threshold_defaults_to_five_when_unset(isolated_db):
    assert sf.get_global_default_threshold() == 5.0
