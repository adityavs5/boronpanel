import pytest

from daemon import imapsync as im
from daemon.procutil import ProcResult
from shared.validation import ValidationError


@pytest.fixture()
def mailbox(isolated_db):
    from shared.db import write_session
    from shared.models import Account, Domain, MailDomain, MailUser

    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot="/home/demo1/public_html"))
        mail_domain = MailDomain(account_id=account.id, domain="demo1.example")
        session.add(mail_domain)
        session.flush()
        session.add(MailUser(mail_domain_id=mail_domain.id, local_part="sales", domain="demo1.example"))
    return "sales", "demo1.example"


@pytest.fixture(autouse=True)
def _installed(monkeypatch):
    monkeypatch.setattr(im, "ensure_installed", lambda: None)


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    """validate_imap_source_host resolves the hostname for real (a
    deliberate SSRF guard, shared/validation.py) -- unit tests must not
    depend on live DNS/network the way ARCHITECTURE.md SS12 requires for
    the whole suite, so every hostname used in this file that isn't
    already a literal IP is faked to resolve to one genuinely public
    address (NOT an RFC 5737 documentation-range IP like 203.0.113.0/24 --
    Python's ipaddress module classifies those as is_private=True, which
    would make every non-IP-literal host in this file fail the same
    "non-public address" check the internal-IP tests exist to exercise).
    The function does `import socket as _socket` locally, but that binds
    to the same singleton module object already in sys.modules -- patching
    the real socket module's getaddrinfo here reaches it identically.
    Literal IPs like 192.168.1.50/127.0.0.1 skip getaddrinfo entirely
    (already valid ip_address literals), so this doesn't affect those
    assertions."""
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))])


def test_start_migration_rejects_internal_ip_source(mailbox):
    local_part, domain = mailbox
    with pytest.raises(ValidationError, match="non-public"):
        im.start_migration({
            "domain": domain, "local_part": local_part,
            "source_host": "192.168.1.50", "source_port": 993,
            "source_email": "old@old-provider.example",
            "source_password": "s3cret-source-pw", "dest_password": "s3cret-dest-pw",
        })


def test_start_migration_rejects_loopback_source(mailbox):
    local_part, domain = mailbox
    with pytest.raises(ValidationError, match="non-public"):
        im.start_migration({
            "domain": domain, "local_part": local_part,
            "source_host": "127.0.0.1", "source_port": 993,
            "source_email": "old@old-provider.example",
            "source_password": "x", "dest_password": "y",
        })


def test_start_migration_rejects_unknown_mailbox(isolated_db):
    with pytest.raises(RuntimeError, match="does not exist"):
        im.start_migration({
            "domain": "never-provisioned.example", "local_part": "nobody",
            "source_host": "imap.gmail.com", "source_port": 993,
            "source_email": "old@gmail.com", "source_password": "x", "dest_password": "y",
        })


def test_start_migration_creates_pending_job(mailbox, monkeypatch):
    local_part, domain = mailbox
    monkeypatch.setattr(im.IMAPSYNC_EXECUTOR, "submit", lambda fn, *a: None)  # don't actually run the worker thread
    result = im.start_migration({
        "domain": domain, "local_part": local_part,
        "source_host": "imap.example.net", "source_port": 993,
        "source_email": "old@example.net", "source_password": "s3cret-source-pw", "dest_password": "s3cret-dest-pw",
    })
    assert result["status"] == "pending"
    assert result["mailbox"] == "sales@demo1.example"
    assert "password" not in str(result)  # no password field anywhere in the returned dict


def test_start_migration_rejects_second_concurrent_job_for_same_account(mailbox, monkeypatch):
    # Audit 3 A3-3: IMAPSYNC_EXECUTOR is a small pool shared by every
    # account; without this guard a single account could queue unlimited
    # jobs and starve every other tenant's migrations.
    local_part, domain = mailbox
    monkeypatch.setattr(im.IMAPSYNC_EXECUTOR, "submit", lambda fn, *a: None)
    im.start_migration({
        "domain": domain, "local_part": local_part,
        "source_host": "imap.example.net", "source_port": 993,
        "source_email": "old@example.net", "source_password": "s3cret-source-pw", "dest_password": "s3cret-dest-pw",
    })
    with pytest.raises(im.ImapSyncError, match="already in progress"):
        im.start_migration({
            "domain": domain, "local_part": local_part,
            "source_host": "imap.example.net", "source_port": 993,
            "source_email": "old@example.net", "source_password": "s3cret-source-pw", "dest_password": "s3cret-dest-pw",
        })


def test_start_migration_allows_new_job_after_previous_completed(mailbox, monkeypatch):
    from shared.db import write_session
    from shared.models import ImapMigrationJob

    local_part, domain = mailbox
    monkeypatch.setattr(im.IMAPSYNC_EXECUTOR, "submit", lambda fn, *a: None)
    first = im.start_migration({
        "domain": domain, "local_part": local_part,
        "source_host": "imap.example.net", "source_port": 993,
        "source_email": "old@example.net", "source_password": "s3cret-source-pw", "dest_password": "s3cret-dest-pw",
    })
    with write_session() as session:
        job = session.get(ImapMigrationJob, first["id"])
        job.status = "completed"
    result = im.start_migration({
        "domain": domain, "local_part": local_part,
        "source_host": "imap.example.net", "source_port": 993,
        "source_email": "old@example.net", "source_password": "s3cret-source-pw", "dest_password": "s3cret-dest-pw",
    })
    assert result["status"] == "pending"


def test_start_migration_job_row_never_has_password_column():
    from shared.models import ImapMigrationJob

    columns = {c.name for c in ImapMigrationJob.__table__.columns}
    assert not any("password" in c for c in columns)


def test_get_status_unknown_job_raises(isolated_db):
    with pytest.raises(RuntimeError):
        im.get_status({"id": 999999, "username": "nouser"})


def test_get_status_cross_account_raises(mailbox, monkeypatch):
    """Cross-account IDOR guard: a real job id belonging to a different
    account must be rejected identically to a missing id."""
    local_part, domain = mailbox
    monkeypatch.setattr(im.IMAPSYNC_EXECUTOR, "submit", lambda fn, *a: None)
    job = im.start_migration({
        "domain": domain, "local_part": local_part,
        "source_host": "imap.example.net", "source_port": 993,
        "source_email": "old@example.net", "source_password": "x", "dest_password": "y",
    })
    from shared.db import write_session
    from shared.models import Account

    with write_session() as session:
        session.add(Account(username="otheracct", uid=5002, gid=5002, status="active"))
    with pytest.raises(RuntimeError):
        im.get_status({"id": job["id"], "username": "otheracct"})


def test_list_jobs_scoped_to_account(mailbox, monkeypatch):
    local_part, domain = mailbox
    monkeypatch.setattr(im.IMAPSYNC_EXECUTOR, "submit", lambda fn, *a: None)
    im.start_migration({
        "domain": domain, "local_part": local_part,
        "source_host": "imap.example.net", "source_port": 993,
        "source_email": "old@example.net", "source_password": "x", "dest_password": "y",
    })
    result = im.list_jobs({"username": "demo1"})
    assert len(result["jobs"]) == 1


def test_cancel_migration_marks_cancelled(mailbox, monkeypatch):
    local_part, domain = mailbox
    monkeypatch.setattr(im.IMAPSYNC_EXECUTOR, "submit", lambda fn, *a: None)
    job = im.start_migration({
        "domain": domain, "local_part": local_part,
        "source_host": "imap.example.net", "source_port": 993,
        "source_email": "old@example.net", "source_password": "x", "dest_password": "y",
    })
    result = im.cancel_migration({"id": job["id"], "username": "demo1"})
    assert result["status"] == "cancelled"


def test_cancel_completed_job_is_noop(mailbox, monkeypatch):
    local_part, domain = mailbox
    monkeypatch.setattr(im.IMAPSYNC_EXECUTOR, "submit", lambda fn, *a: None)
    job = im.start_migration({
        "domain": domain, "local_part": local_part,
        "source_host": "imap.example.net", "source_port": 993,
        "source_email": "old@example.net", "source_password": "x", "dest_password": "y",
    })
    from shared.db import write_session
    from shared.models import ImapMigrationJob

    with write_session() as session:
        row = session.get(ImapMigrationJob, job["id"])
        row.status = "completed"
    result = im.cancel_migration({"id": job["id"], "username": "demo1"})
    assert result["status"] == "completed"


def test_list_active_admin_only_shows_active_statuses(mailbox, monkeypatch):
    local_part, domain = mailbox
    monkeypatch.setattr(im.IMAPSYNC_EXECUTOR, "submit", lambda fn, *a: None)
    job = im.start_migration({
        "domain": domain, "local_part": local_part,
        "source_host": "imap.example.net", "source_port": 993,
        "source_email": "old@example.net", "source_password": "x", "dest_password": "y",
    })
    from shared.db import write_session
    from shared.models import ImapMigrationJob

    result = im.list_active_admin({})
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["username"] == "demo1"

    with write_session() as session:
        row = session.get(ImapMigrationJob, job["id"])
        row.status = "completed"
    result = im.list_active_admin({})
    assert result["jobs"] == []


def test_list_source_folders_parses_output(monkeypatch):
    # Real imapsync 2.229 output shape (confirmed live against a real
    # Dovecot mailbox during this feature's own verification) -- bare
    # "[FolderName]" lines under a "Host1: folders list" header, followed
    # by an identical "Host2: folders list" section (since
    # list_source_folders points host2 at the same source server/creds as
    # host1 -- imapsync's own --help documents "host1 alone implies
    # --justconnect", so a real single-host folder listing isn't possible
    # without this). Only the Host1 section must be parsed.
    fake_stdout = (
        "Host1: folders list (first the raw imap format then the [X] = [Y]):\n"
        "* LIST (\\HasNoChildren) \".\" INBOX\n"
        "* LIST (\\HasNoChildren) \".\" Sent\n"
        "* LIST (\\HasNoChildren) \".\" Archive/2024\n"
        "1 OK List completed\n\n"
        "[INBOX]\n[Sent]\n[Archive/2024]\n\n"
        "Host2: folders list (first the raw imap format then the [X] = [Y]):\n"
        "* LIST (\\HasNoChildren) \".\" INBOX\n"
        "1 OK List completed\n\n[INBOX]\n"
    )
    captured = {}
    def fake_run(args, timeout=None, redact=None):
        captured["args"] = args
        return ProcResult(args=args, returncode=0, stdout=fake_stdout, stderr="")
    monkeypatch.setattr(im, "run", fake_run)
    folders = im.list_source_folders("imap.example.net", 993, "old@example.net", "pw")
    assert folders == ["INBOX", "Sent", "Archive/2024"]
    # host2 must be supplied (pointed at the same source) -- without it
    # imapsync silently no-ops instead of listing folders (the real bug
    # this test's fake_stdout format change fixes coverage for).
    assert "--host2" in captured["args"]
    # Audit 3 A3-5: --nolog, or imapsync writes a world-readable transcript
    # (source/dest email, host, login success) to LOG_imapsync/ relative to
    # the daemon's cwd.
    assert "--nolog" in captured["args"]


def test_list_source_folders_raises_on_failure(monkeypatch):
    monkeypatch.setattr(im, "run", lambda args, timeout=None, redact=None: ProcResult(args=args, returncode=1, stdout="", stderr="Login failed"))
    with pytest.raises(im.ImapSyncError, match="Login failed"):
        im.list_source_folders("imap.example.net", 993, "old@example.net", "wrongpw")


def test_run_job_revalidates_source_host_before_connecting(mailbox, monkeypatch, tmp_path):
    # Audit 3 A3-4: source_host must be re-validated immediately before the
    # real connection (shrinking the DNS-rebinding TOCTOU window), not only
    # once at start_migration time -- confirm a rebind detected at
    # connect-time fails the job cleanly and imapsync is never invoked.
    local_part, domain = mailbox
    monkeypatch.setattr(im, "IMAPSYNC_RUN_DIR", str(tmp_path))

    with im.write_session() as session:
        from shared.models import ImapMigrationJob

        job = ImapMigrationJob(account_id=1, mailbox=f"{local_part}@{domain}", source_host="imap.example.net",
                                source_port=993, source_email="old@example.net", status="pending")
        session.add(job)
        session.flush()
        job_id = job.id

    revalidate_calls = []

    def fake_validate(host):
        revalidate_calls.append(host)
        raise ValidationError(f"'{host}' resolves to a non-public address (simulated DNS rebind)")

    monkeypatch.setattr(im, "validate_imap_source_host", fake_validate)
    run_calls = []
    monkeypatch.setattr(im, "run", lambda *a, **kw: run_calls.append(a))

    im._run_job(job_id, "s3cret-source-pw", "s3cret-dest-pw", True)

    status = im.get_status({"id": job_id, "username": "demo1"})
    assert status["status"] == "failed"
    assert "non-public" in status["error"]
    assert revalidate_calls == ["imap.example.net"]
    assert run_calls == [], "imapsync must never be invoked once re-validation fails"


def test_run_job_never_logs_or_stores_passwords(mailbox, monkeypatch, tmp_path, caplog):
    local_part, domain = mailbox
    monkeypatch.setattr(im, "IMAPSYNC_RUN_DIR", str(tmp_path))

    captured_args = []

    def fake_run(args, timeout=None, redact=None):
        captured_args.append(args)
        if "--justfolders" in args:
            return ProcResult(
                args=args, returncode=0,
                stdout="Host1: folders list (...)\n[INBOX]\n\nHost2: folders list (...)\n[INBOX]\n", stderr="",
            )
        # Real imapsync 2.229 Statistics-block line shape, confirmed live.
        return ProcResult(args=args, returncode=0, stdout="Messages transferred                    : 5 \n", stderr="")

    monkeypatch.setattr(im, "run", fake_run)

    with im.write_session() as session:
        from shared.models import ImapMigrationJob

        job = ImapMigrationJob(account_id=1, mailbox=f"{local_part}@{domain}", source_host="imap.example.net",
                                source_port=993, source_email="old@example.net", status="pending")
        session.add(job)
        session.flush()
        job_id = job.id

    im._run_job(job_id, "s3cret-source-pw", "s3cret-dest-pw", True)

    status = im.get_status({"id": job_id, "username": "demo1"})
    assert status["status"] == "completed"
    assert status["messages_done"] == 5
    assert "s3cret-source-pw" not in str(status)
    assert "s3cret-dest-pw" not in str(status)
    # passwords never appear as literal argv values -- only passfile paths do
    for args in captured_args:
        assert "s3cret-source-pw" not in args
        assert "s3cret-dest-pw" not in args
    # the per-job passfile directory is cleaned up after the run
    assert not (tmp_path / str(job_id)).exists()
    # Audit 3 A3-5: every real imapsync invocation (folder-list + per-folder
    # sync) must suppress imapsync's own transcript logging.
    for args in captured_args:
        assert "--nolog" in args
