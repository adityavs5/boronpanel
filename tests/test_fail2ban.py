import pytest

from daemon import fail2ban
from daemon.procutil import ProcResult
from shared.validation import ValidationError

# Captured live from this server's own real fail2ban-client output
# (docs/CHECKPOINT-phase5-5-fail2ban.md) -- this box has genuine
# internet-facing SSH scanning traffic, so this is a real sample, not a
# hand-crafted guess.
STATUS_LIST_SAMPLE = "Status\n|- Number of jail:\t3\n`- Jail list:\tsshd, postfix, dovecot\n"

SSHD_STATUS_SAMPLE = (
    "Status for the jail: sshd\n"
    "|- Filter\n"
    "|  |- Currently failed:\t4\n"
    "|  |- Total failed:\t63\n"
    "|  `- Journal matches:\t_SYSTEMD_UNIT=sshd.service + _COMM=sshd\n"
    "`- Actions\n"
    "   |- Currently banned:\t2\n"
    "   |- Total banned:\t9\n"
    "   `- Banned IP list:\t45.148.10.157 45.227.254.170\n"
)

EMPTY_JAIL_STATUS_SAMPLE = (
    "Status for the jail: postfix\n"
    "|- Filter\n"
    "|  |- Currently failed:\t0\n"
    "|  |- Total failed:\t0\n"
    "|  `- Journal matches:\t_SYSTEMD_UNIT=postfix@-.service\n"
    "`- Actions\n"
    "   |- Currently banned:\t0\n"
    "   |- Total banned:\t0\n"
    "   `- Banned IP list:\t\n"
)

LOG_SAMPLE = """2026-07-03 17:18:02,043 fail2ban.actions        [249303]: NOTICE  [sshd] Ban 45.148.10.151
2026-07-03 17:23:28,739 fail2ban.actions        [249303]: NOTICE  [sshd] Ban 45.148.10.141
2026-07-03 17:28:01,623 fail2ban.actions        [249303]: NOTICE  [sshd] Unban 45.148.10.151
2026-07-03 17:28:43,957 fail2ban.actions        [249303]: NOTICE  [dovecot] Ban 45.227.254.170
"""


def test_list_jail_names_parses_comma_separated_list(monkeypatch):
    monkeypatch.setattr(fail2ban, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=STATUS_LIST_SAMPLE, stderr=""))
    assert fail2ban._list_jail_names() == ["sshd", "postfix", "dovecot"]


def test_jail_status_parses_real_sshd_sample(monkeypatch):
    monkeypatch.setattr(fail2ban, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=SSHD_STATUS_SAMPLE, stderr=""))
    status = fail2ban._jail_status("sshd")
    assert status["currently_failed"] == 4
    assert status["total_failed"] == 63
    assert status["currently_banned"] == 2
    assert status["total_banned"] == 9
    assert status["banned_ips"] == ["45.148.10.157", "45.227.254.170"]


def test_jail_status_handles_empty_banned_list(monkeypatch):
    monkeypatch.setattr(fail2ban, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=EMPTY_JAIL_STATUS_SAMPLE, stderr=""))
    status = fail2ban._jail_status("postfix")
    assert status["banned_ips"] == []
    assert status["currently_banned"] == 0


def test_list_jails_returns_all_active_jails(monkeypatch):
    def fake_run(args, timeout=15):
        if args == ["fail2ban-client", "status"]:
            return ProcResult(args=args, returncode=0, stdout=STATUS_LIST_SAMPLE, stderr="")
        if args == ["fail2ban-client", "status", "sshd"]:
            return ProcResult(args=args, returncode=0, stdout=SSHD_STATUS_SAMPLE, stderr="")
        return ProcResult(args=args, returncode=0, stdout=EMPTY_JAIL_STATUS_SAMPLE, stderr="")

    monkeypatch.setattr(fail2ban, "run", fake_run)
    result = fail2ban.list_jails({})
    assert {j["jail"] for j in result["jails"]} == {"sshd", "postfix", "dovecot"}


def test_get_jail_rejects_inactive_jail(monkeypatch):
    monkeypatch.setattr(fail2ban, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=STATUS_LIST_SAMPLE, stderr=""))
    with pytest.raises(ValidationError):
        fail2ban.get_jail({"jail": "not-a-real-jail"})


def test_validate_ip_accepts_valid_and_rejects_invalid():
    assert fail2ban._validate_ip("45.148.10.157") == "45.148.10.157"
    with pytest.raises(ValidationError):
        fail2ban._validate_ip("not-an-ip")
    with pytest.raises(ValidationError):
        fail2ban._validate_ip("1.2.3.4; rm -rf /")


def test_unban_ip_rejects_unknown_jail(monkeypatch):
    monkeypatch.setattr(fail2ban, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=STATUS_LIST_SAMPLE, stderr=""))
    with pytest.raises(ValidationError):
        fail2ban.unban_ip({"jail": "nonexistent", "ip": "1.2.3.4"})


def test_unban_ip_calls_fail2ban_client_set_unbanip(monkeypatch):
    calls = []

    def fake_run(args, timeout=15):
        calls.append(args)
        if args == ["fail2ban-client", "status"]:
            return ProcResult(args=args, returncode=0, stdout=STATUS_LIST_SAMPLE, stderr="")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fail2ban, "run", fake_run)
    result = fail2ban.unban_ip({"jail": "sshd", "ip": "45.148.10.157"})
    assert result["status"] == "unbanned"
    assert ["fail2ban-client", "set", "sshd", "unbanip", "45.148.10.157"] in calls


def test_unban_all_in_jail_unbans_every_currently_banned_ip(monkeypatch):
    calls = []

    def fake_run(args, timeout=15):
        calls.append(args)
        if args == ["fail2ban-client", "status"]:
            return ProcResult(args=args, returncode=0, stdout=STATUS_LIST_SAMPLE, stderr="")
        if args == ["fail2ban-client", "status", "sshd"]:
            return ProcResult(args=args, returncode=0, stdout=SSHD_STATUS_SAMPLE, stderr="")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fail2ban, "run", fake_run)
    result = fail2ban.unban_all_in_jail({"jail": "sshd"})
    assert result["count"] == 2
    assert set(result["unbanned"]) == {"45.148.10.157", "45.227.254.170"}


def test_unban_network_removes_only_banned_ips_inside_new_bypass(monkeypatch):
    calls = []

    def fake_run(args, timeout=15):
        calls.append(args)
        if args == ["fail2ban-client", "status"]:
            return ProcResult(args=args, returncode=0, stdout="Status\n`- Jail list:\tsshd\n", stderr="")
        if args == ["fail2ban-client", "status", "sshd"]:
            return ProcResult(args=args, returncode=0, stdout=SSHD_STATUS_SAMPLE, stderr="")
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fail2ban, "run", fake_run)
    removed = fail2ban.unban_network("45.148.10.0/24")
    assert removed == [{"jail": "sshd", "ip": "45.148.10.157"}]
    assert ["fail2ban-client", "set", "sshd", "unbanip", "45.227.254.170"] not in calls


def test_recent_events_parses_ban_and_unban_lines(monkeypatch):
    monkeypatch.setattr(fail2ban, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=LOG_SAMPLE, stderr=""))
    result = fail2ban.recent_events({})
    assert len(result["events"]) == 4
    # most recent first
    assert result["events"][0]["action"] == "Ban"
    assert result["events"][0]["jail"] == "dovecot"
    assert result["events"][0]["ip"] == "45.227.254.170"


def test_recent_events_filters_by_jail(monkeypatch):
    monkeypatch.setattr(fail2ban, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=LOG_SAMPLE, stderr=""))
    result = fail2ban.recent_events({"jail": "dovecot"})
    assert len(result["events"]) == 1
    assert result["events"][0]["jail"] == "dovecot"


def test_recent_events_respects_limit(monkeypatch):
    monkeypatch.setattr(fail2ban, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=LOG_SAMPLE, stderr=""))
    result = fail2ban.recent_events({"limit": 2})
    assert len(result["events"]) == 2


def test_bootstrap_jails_writes_config_and_reloads(monkeypatch, tmp_path):
    written = {}

    def fake_atomic_write(path, content):
        written[path] = content

    monkeypatch.setattr(fail2ban, "_atomic_write", fake_atomic_write)
    calls = []

    def fake_run(args, timeout=30):
        calls.append(args)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fail2ban, "run", fake_run)
    result = fail2ban.bootstrap_jails({})
    assert result["status"] == "ok"
    assert fail2ban.FILTER_PANEL_LOGIN_PATH in written
    assert fail2ban.FILTER_OLS_SCAN_PATH in written
    assert fail2ban.JAIL_D_PATH in written
    assert "postfix@-.service" in written[fail2ban.JAIL_D_PATH]
    assert ["fail2ban-client", "reload"] in calls


def test_bootstrap_jails_raises_on_reload_failure(monkeypatch):
    monkeypatch.setattr(fail2ban, "_atomic_write", lambda path, content: None)
    monkeypatch.setattr(fail2ban, "run", lambda args, timeout=30: ProcResult(args=args, returncode=1, stdout="", stderr="config error"))
    with pytest.raises(RuntimeError):
        fail2ban.bootstrap_jails({})



def test_panel_jail_uses_actual_listener_ports(monkeypatch):
    monkeypatch.setattr(fail2ban.settings, 'api_bind_port', 2222)
    monkeypatch.setattr(fail2ban.settings, 'api_customer_port', 2083)
    config = fail2ban._render_jail_conf([])
    assert 'port = 2083,2222' in config
    assert '9443' not in config
    monkeypatch.setattr(fail2ban.settings, 'api_customer_port', 2222)
    assert 'port = 2222\n' in fail2ban._render_jail_conf([])


def test_panel_failregex_covers_password_and_second_factor():
    import re
    pattern = fail2ban._PANEL_LOGIN_FILTER.split('failregex = ', 1)[1].splitlines()[0]
    pattern = pattern.replace('<HOST>', r'(?P<host>[0-9.]+)')
    for route in ('/login', '/login/2fa'):
        for status in (401, 429):
            assert re.match(pattern, f'INFO:     198.51.100.20:34567 - "POST {route} HTTP/1.1" {status}')
        assert not re.match(pattern, f'INFO:     198.51.100.20:34567 - "POST {route} HTTP/1.1" 200')



def test_reconcile_existing_jail_replaces_stale_port_and_is_idempotent(tmp_path, monkeypatch):
    paths = {'JAIL_D_PATH': 'jail.conf', 'FILTER_PANEL_LOGIN_PATH': 'login.conf', 'FILTER_OLS_SCAN_PATH': 'ols.conf'}
    for attr, name in paths.items():
        monkeypatch.setattr(fail2ban, attr, str(tmp_path / name))
    monkeypatch.setattr(fail2ban, '_cloudflare_ranges', lambda: [])
    calls = []
    monkeypatch.setattr(fail2ban, 'run', lambda args, **kwargs: calls.append(args) or ProcResult(args, 0, '', ''))
    assert fail2ban.reconcile_managed_jails() is False
    (tmp_path / 'jail.conf').write_text('port = 9443')
    assert fail2ban.reconcile_managed_jails() is True
    assert fail2ban.reconcile_managed_jails() is False
    assert calls == [['fail2ban-client', 'reload']]
