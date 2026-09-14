import pytest

from daemon import cron
from daemon.procutil import ProcResult


@pytest.fixture()
def fake_crontab(monkeypatch):
    """In-memory stand-in for `crontab -u <user> -l` / `-` so tests never
    touch a real system crontab. Keyed by username so multiple accounts in
    one test stay independent."""
    state: dict[str, str] = {}

    def fake_run(args, input_text=None, timeout=30.0, check=False):
        assert args[0] == "crontab"
        assert args[1] == "-u"
        username = args[2]
        if args[3] == "-l":
            content = state.get(username)
            if content is None:
                return ProcResult(args=args, returncode=1, stdout="", stderr=f"no crontab for {username}")
            return ProcResult(args=args, returncode=0, stdout=content, stderr="")
        elif args[3] == "-":
            state[username] = input_text or ""
            return ProcResult(args=args, returncode=0, stdout="", stderr="")
        elif args[3] == "-r":
            if username not in state:
                return ProcResult(args=args, returncode=1, stdout="", stderr=f"no crontab for {username}")
            del state[username]
            return ProcResult(args=args, returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected crontab invocation: {args}")

    monkeypatch.setattr(cron, "run", fake_run)
    return state


def test_list_jobs_empty_crontab(fake_crontab):
    assert cron.list_jobs("demo1") == []


def test_add_job_then_list(fake_crontab):
    result = cron.add_job("demo1", "*/5 * * * *", "/usr/bin/php /home/demo1/cron.php", label="my job")
    assert result["schedule"] == "*/5 * * * *"
    jobs = cron.list_jobs("demo1")
    assert len(jobs) == 1
    assert jobs[0]["id"] == result["id"]
    assert jobs[0]["label"] == "my job"
    assert jobs[0]["command"] == "/usr/bin/php /home/demo1/cron.php"


def test_add_job_rejects_invalid_schedule(fake_crontab):
    with pytest.raises(cron.CronError):
        cron.add_job("demo1", "99 * * * *", "/bin/true")


def test_add_job_rejects_empty_command(fake_crontab):
    with pytest.raises(cron.CronError):
        cron.add_job("demo1", "* * * * *", "   ")


def test_add_job_rejects_newline_in_command(fake_crontab):
    with pytest.raises(cron.CronError):
        cron.add_job("demo1", "* * * * *", "/bin/true\n* * * * * /bin/evil")


def test_add_job_rejects_newline_in_label(fake_crontab):
    with pytest.raises(cron.CronError):
        cron.add_job("demo1", "* * * * *", "/bin/true", label="a\nb")


def test_add_job_rejects_oversized_command(fake_crontab):
    with pytest.raises(cron.CronError):
        cron.add_job("demo1", "* * * * *", "x" * 2000)


def test_multiple_jobs_independent(fake_crontab):
    j1 = cron.add_job("demo1", "0 0 * * *", "/bin/job1")
    j2 = cron.add_job("demo1", "0 12 * * *", "/bin/job2")
    jobs = cron.list_jobs("demo1")
    assert {j["id"] for j in jobs} == {j1["id"], j2["id"]}


def test_update_job_changes_only_that_job(fake_crontab):
    j1 = cron.add_job("demo1", "0 0 * * *", "/bin/job1")
    j2 = cron.add_job("demo1", "0 12 * * *", "/bin/job2")

    cron.update_job("demo1", j1["id"], "30 1 * * *", "/bin/job1-updated", label="renamed")

    jobs = {j["id"]: j for j in cron.list_jobs("demo1")}
    assert jobs[j1["id"]]["schedule"] == "30 1 * * *"
    assert jobs[j1["id"]]["command"] == "/bin/job1-updated"
    assert jobs[j1["id"]]["label"] == "renamed"
    # untouched
    assert jobs[j2["id"]]["schedule"] == "0 12 * * *"
    assert jobs[j2["id"]]["command"] == "/bin/job2"


def test_update_unknown_job_raises(fake_crontab):
    cron.add_job("demo1", "* * * * *", "/bin/true")
    with pytest.raises(cron.CronError):
        cron.update_job("demo1", "nonexistent-id", "* * * * *", "/bin/true")


def test_delete_job_removes_only_that_job(fake_crontab):
    j1 = cron.add_job("demo1", "0 0 * * *", "/bin/job1")
    j2 = cron.add_job("demo1", "0 12 * * *", "/bin/job2")

    cron.delete_job("demo1", j1["id"])

    jobs = cron.list_jobs("demo1")
    assert len(jobs) == 1
    assert jobs[0]["id"] == j2["id"]


def test_delete_unknown_job_raises(fake_crontab):
    with pytest.raises(cron.CronError):
        cron.delete_job("demo1", "nonexistent-id")


def test_unmanaged_lines_are_preserved(fake_crontab):
    """A line the account might already have (or one Boron doesn't
    recognize) must survive add/update/delete of *other* jobs untouched --
    the marker-comment scheme must not accidentally treat arbitrary
    crontab content as its own."""
    fake_crontab["demo1"] = "# a manual comment\n0 3 * * * /bin/manual-job\n"
    j1 = cron.add_job("demo1", "0 0 * * *", "/bin/managed-job")

    raw = fake_crontab["demo1"]
    assert "# a manual comment" in raw
    assert "/bin/manual-job" in raw

    cron.delete_job("demo1", j1["id"])
    raw_after = fake_crontab["demo1"]
    assert "# a manual comment" in raw_after
    assert "/bin/manual-job" in raw_after
    assert "/bin/managed-job" not in raw_after


def test_delete_all_jobs_on_empty_crontab_is_noop(fake_crontab):
    cron.delete_all_jobs("demo1")  # must not raise even with no crontab


def test_delete_all_jobs_removes_crontab(fake_crontab):
    cron.add_job("demo1", "* * * * *", "/bin/true")
    cron.delete_all_jobs("demo1")
    assert cron.list_jobs("demo1") == []


def test_jobs_are_per_user_independent(fake_crontab):
    cron.add_job("demo1", "* * * * *", "/bin/job-for-demo1")
    cron.add_job("demo2", "* * * * *", "/bin/job-for-demo2")
    assert len(cron.list_jobs("demo1")) == 1
    assert len(cron.list_jobs("demo2")) == 1
    assert cron.list_jobs("demo1")[0]["command"] == "/bin/job-for-demo1"


# --- MAILTO (Phase 4 feature 10) -------------------------------------------


def test_get_mailto_defaults_empty_on_fresh_crontab(fake_crontab):
    assert cron.get_mailto("demo1") == ""


def test_set_mailto_happy_path(fake_crontab):
    result = cron.set_mailto("demo1", "alerts@example.com")
    assert result == "alerts@example.com"
    assert cron.get_mailto("demo1") == "alerts@example.com"
    assert "MAILTO=alerts@example.com" in fake_crontab["demo1"]


def test_set_mailto_rejects_root(fake_crontab):
    from shared.validation import ValidationError

    with pytest.raises(ValidationError):
        cron.set_mailto("demo1", "root")
    with pytest.raises(ValidationError):
        cron.set_mailto("demo1", "root@localhost")
    with pytest.raises(ValidationError):
        cron.set_mailto("demo1", "ROOT@example.com")


def test_set_mailto_rejects_malformed_address(fake_crontab):
    from shared.validation import ValidationError

    with pytest.raises(ValidationError):
        cron.set_mailto("demo1", "not-an-email")


def test_set_mailto_empty_removes_line_and_preserves_jobs(fake_crontab):
    cron.add_job("demo1", "* * * * *", "/bin/true")
    cron.set_mailto("demo1", "alerts@example.com")
    assert "MAILTO=" in fake_crontab["demo1"]

    cron.set_mailto("demo1", "")
    assert "MAILTO=" not in fake_crontab["demo1"]
    assert cron.get_mailto("demo1") == ""
    assert len(cron.list_jobs("demo1")) == 1  # job untouched


def test_set_mailto_preserves_existing_jobs_and_manual_lines(fake_crontab):
    fake_crontab["demo1"] = "# a manual comment\n0 3 * * * /bin/manual-job\n"
    job = cron.add_job("demo1", "0 0 * * *", "/bin/managed-job")

    cron.set_mailto("demo1", "alerts@example.com")

    raw = fake_crontab["demo1"]
    assert "MAILTO=alerts@example.com" in raw
    assert "# a manual comment" in raw
    assert "/bin/manual-job" in raw
    assert "/bin/managed-job" in raw
    assert cron.list_jobs("demo1")[0]["id"] == job["id"]


def test_set_mailto_is_idempotent_no_duplicate_lines(fake_crontab):
    cron.set_mailto("demo1", "first@example.com")
    cron.set_mailto("demo1", "second@example.com")
    raw = fake_crontab["demo1"]
    assert raw.count("MAILTO=") == 1
    assert "MAILTO=second@example.com" in raw


def test_mailto_is_per_user_independent(fake_crontab):
    cron.set_mailto("demo1", "one@example.com")
    cron.set_mailto("demo2", "two@example.com")
    assert cron.get_mailto("demo1") == "one@example.com"
    assert cron.get_mailto("demo2") == "two@example.com"


def test_add_job_includes_human_readable_description(fake_crontab):
    job = cron.add_job("demo1", "0 3 * * *", "/bin/nightly")
    assert job["description"] == "Every day at 03:00"
    assert cron.list_jobs("demo1")[0]["description"] == "Every day at 03:00"


class TestDescribeSchedule:
    """Item 6: templates + human-readable description alongside raw cron
    syntax -- every minute, every 5/15/30min, hourly, daily, weekly,
    monthly, custom."""

    def test_every_minute(self):
        assert cron.describe_schedule("* * * * *") == "Every minute"

    @pytest.mark.parametrize("n", [5, 15, 30])
    def test_every_n_minutes(self, n):
        assert cron.describe_schedule(f"*/{n} * * * *") == f"Every {n} minutes"

    def test_step_of_one_reads_as_every_minute(self):
        assert cron.describe_schedule("*/1 * * * *") == "Every minute"

    def test_hourly_on_the_hour(self):
        assert cron.describe_schedule("0 * * * *") == "Every hour, on the hour"

    def test_hourly_at_specific_minute(self):
        assert cron.describe_schedule("30 * * * *") == "Every hour, at minute 30"

    def test_daily(self):
        assert cron.describe_schedule("15 4 * * *") == "Every day at 04:15"

    def test_weekly(self):
        assert cron.describe_schedule("0 9 * * 1") == "Every Monday at 09:00"

    def test_weekly_sunday_zero_and_seven_are_equivalent(self):
        assert cron.describe_schedule("0 9 * * 0") == "Every Sunday at 09:00"
        assert cron.describe_schedule("0 9 * * 7") == "Every Sunday at 09:00"

    def test_monthly(self):
        assert cron.describe_schedule("0 0 1 * *") == "On day 1 of every month at 00:00"

    def test_yearly(self):
        assert cron.describe_schedule("0 0 25 12 *") == "Once a year on December 25 at 00:00"

    def test_custom_fallback_for_list_syntax(self):
        assert cron.describe_schedule("0 9 * * 1,3,5") == "Custom schedule"

    def test_custom_fallback_for_range_syntax(self):
        assert cron.describe_schedule("0 9-17 * * *") == "Custom schedule"

    def test_custom_fallback_for_malformed_input(self):
        assert cron.describe_schedule("not a schedule") == "Custom schedule"

    def test_nicknames(self):
        assert cron.describe_schedule("@daily") == "Every day at midnight"
        assert cron.describe_schedule("@hourly") == "Every hour, on the hour"
        assert cron.describe_schedule("@weekly") == "Once a week, at midnight on Sunday"
        assert cron.describe_schedule("@monthly") == "Once a month, at midnight on the 1st"
        assert cron.describe_schedule("@yearly") == "Once a year, at midnight on January 1st"


def test_configuration_round_trip_preserves_manual_entries_and_environment(fake_crontab):
    original = 'SHELL=/bin/bash\nPATH=/usr/bin:/bin\nMAILTO=""\n# manual task\n@daily /bin/true\n'
    fake_crontab['demo1'] = original
    job = cron.add_job('demo1', '@hourly', '/usr/bin/php /home/demo1/cron.php', 'hourly')
    assert cron.list_jobs('demo1')[0]['schedule'] == '@hourly'
    saved = cron.capture_configuration('demo1')
    before = fake_crontab['demo1']
    fake_crontab['demo1'] = '0 0 * * * /bin/false\n'
    fake_crontab['demo2'] = 'MAILTO=other@example.test\n'
    cron.restore_configuration('demo1', saved)
    assert fake_crontab['demo1'] == before
    assert cron.list_jobs('demo1')[0]['id'] == job['id']
    assert fake_crontab['demo2'] == 'MAILTO=other@example.test\n'


@pytest.mark.parametrize('change', [
    {'username': 'demo2'}, {'format': True}, {'format': 2}, {'lines': ['x\nsecond line']},
    {'lines': ['x\rsecond line']}, {'lines': ['x\0']}, {'lines': 'not a list'},
    {'lines': [None]}, {'lines': ['x'*65537]}, {'lines': ['']*10001},
])
def test_invalid_cron_recovery_never_changes_current_table(fake_crontab, change):
    fake_crontab['demo1'] = '0 0 * * * /bin/true\n'
    saved = dict(format=1, username='demo1', lines=[])
    saved.update(change)
    with pytest.raises(cron.CronError):
        cron.restore_configuration('demo1', saved)
    assert fake_crontab['demo1'] == '0 0 * * * /bin/true\n'


def test_empty_cron_recovery_clears_only_the_bound_table(fake_crontab):
    fake_crontab['demo1'] = '0 0 * * * /bin/true\n'
    fake_crontab['demo2'] = '0 0 * * * /bin/false\n'
    cron.restore_configuration('demo1', dict(format=1, username='demo1', lines=[]))
    assert fake_crontab['demo1'] == ''
    assert fake_crontab['demo2'] == '0 0 * * * /bin/false\n'
