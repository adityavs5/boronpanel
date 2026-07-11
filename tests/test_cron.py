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
