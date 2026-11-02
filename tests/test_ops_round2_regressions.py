"""Round 2 regressions: restore recreation, downloads, destinations, error codes."""
from __future__ import annotations

import asyncio
import gzip
import io
import json
import logging
import os
import tarfile
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import datetime as dt

import pytest
from sqlalchemy import select

from daemon import (
    backup, cron, dnsprovider, firewall, gitrepo, handlers_cron, ipban, portable_archive, powerdns,
    snapshot_dns, snapshot_jobs, wordpress,
)
from daemon.procutil import ProcResult
from daemon.rpc_policy import POLICY_BY_OPERATION
from shared.db import write_session
from shared.models import (
    Account, AccountArchiveImportJob, BackupDestination, BackupJob, DatabaseGrant, DatabaseUser, DkimKey,
    Domain, DnsZone, FirewallTemporaryBan, GitRepo, MailDomain, MailUser, RestoreJob, SnapshotDestination,
    SnapshotPolicy, SnapshotRun, utcnow,
)
from shared.validation import ConflictError, NotFoundError, ValidationError


def _code(exc: BaseException) -> str:
    from daemon.server import rpc_error_code
    return rpc_error_code(exc)


def _account(session, username="demo1", status="active", **extra):
    row = Account(username=username, status=status, uid=extra.pop("uid", 2000), gid=extra.pop("gid", 2000), **extra)
    session.add(row)
    session.flush()
    return row


def _summary():
    return {"initial_password": None, "skipped_domains": [], "not_restored": [],
            "mailboxes_requiring_password_reset": [], "database_users_requiring_password_reset": []}


# --- snapshot DNS: TXT saved before 255-octet splitting (item 1) -------------

LONG_DKIM = "v=DKIM1; k=rsa; p=" + "A" * 380


@pytest.fixture
def dns_alpha(isolated_db):
    with write_session() as session:
        account = Account(username="alpha", status="active")
        session.add(account)
        session.flush()
        session.add(DnsZone(account_id=account.id, zone="alpha.test"))
    return account


def _legacy_rrsets():
    return [
        {"name": "alpha.test.", "type": "NS", "ttl": 3600,
         "records": [{"content": "ns1.alpha.test.", "disabled": False}], "comments": []},
        {"name": "default._domainkey.alpha.test.", "type": "TXT", "ttl": 3600,
         "records": [{"content": f'"{LONG_DKIM}"', "disabled": False}], "comments": []},
    ]


def test_snapshot_dns_recovery_point_with_one_long_txt_string_is_restorable(dns_alpha, monkeypatch):
    rrsets = _legacy_rrsets()
    monkeypatch.setattr(powerdns, "get_zone", lambda zone: {"name": zone + ".", "rrsets": deepcopy(rrsets)})
    payload = snapshot_dns.capture(dns_alpha)
    restored = snapshot_dns.validate_for_restore(dns_alpha, payload)["zones"][0]["records"]
    strings = dnsprovider.txt_strings(restored[0]["records"][0]["content"])
    assert len(strings) == 2 and all(len(item) <= 255 for item in strings)
    assert b"".join(strings).decode() == LONG_DKIM
    # The verification compare reads the saved one-string form as well.
    assert snapshot_dns._record_state(rrsets[1:]) == snapshot_dns._record_state(restored)


def test_snapshot_dns_restore_rewrites_a_legacy_long_txt_split(dns_alpha, monkeypatch):
    live = _legacy_rrsets()
    monkeypatch.setattr(powerdns, "get_zone", lambda zone: {"name": zone + ".", "rrsets": deepcopy(live)})
    saved = snapshot_dns.capture(dns_alpha)
    live.append({"name": "new.alpha.test.", "type": "A", "ttl": 60,
                 "records": [{"content": "192.0.2.9", "disabled": False}], "comments": []})

    def patch(name, changes):
        for change in changes:
            live[:] = [row for row in live if (row["name"], row["type"]) != (change["name"], change["type"])]
            if change["changetype"] == "REPLACE":
                live.append({key: deepcopy(value) for key, value in change.items() if key != "changetype"})

    monkeypatch.setattr(powerdns, "apply_rrset_changes", patch)
    previous = []
    assert snapshot_dns.apply_configuration(dns_alpha, saved, previous.append) == ["alpha.test"]
    assert previous and not any(row["name"] == "new.alpha.test." for row in live)
    txt = next(row for row in live if row["type"] == "TXT")
    assert len(dnsprovider.txt_strings(txt["records"][0]["content"])) == 2


# --- legacy restore: apex NS through the Nameservers feature (item 2) --------


def test_legacy_dns_restore_sets_nameservers_with_glue_and_reports_rejected_records(isolated_db, monkeypatch):
    from daemon import handlers_dns, nameservers
    with write_session() as session:
        account = _account(session)
        session.add(DnsZone(account_id=account.id, zone="demo1.example"))
        session.add(DkimKey(domain="demo1.example", selector="default"))
    delegations, written = [], []
    monkeypatch.setattr(nameservers, "default_delegation", lambda zone: (["ns1.panel.example", "ns2.panel.example"], {}))
    monkeypatch.setattr(nameservers, "set_nameservers", lambda params: delegations.append(params) or {})
    monkeypatch.setattr(handlers_dns, "create_zone", lambda params: pytest.fail("the zone exists already"))

    def set_record(params):
        if params["type"] == "CAA":
            raise ValidationError("Unsupported DNS record type 'CAA'")
        written.append((params["subdomain"], params["type"], params["values"]))

    monkeypatch.setattr(handlers_dns, "set_record", set_record)
    zone = {"zone": "demo1.example", "records": [
        {"name": "demo1.example", "type": "NS", "ttl": 3600, "values": ["ns1.demo1.example.", "ns2.elsewhere.example."]},
        {"name": "ns1.demo1.example", "type": "A", "ttl": 3600, "values": ["192.0.2.53"]},
        {"name": "demo1.example", "type": "A", "ttl": 3600, "values": ["192.0.2.10"]},
        {"name": "default._domainkey.demo1.example", "type": "TXT", "ttl": 3600, "values": ['"v=DKIM1; p=OLDKEY"']},
        {"name": "demo1.example", "type": "CAA", "ttl": 3600, "values": ['0 issue "ca.example"']},
    ]}
    summary = _summary()
    backup._recreate_dns_zone("demo1", zone, True, summary)
    assert delegations == [{"domain": "demo1.example", "nameservers": ["ns1.demo1.example", "ns2.elsewhere.example"],
                            "glue": {"ns1.demo1.example": "192.0.2.53"}}]
    assert ("ns1", "A", ["192.0.2.53"]) in written and ("@", "A", ["192.0.2.10"]) in written
    # Apex NS never goes through the record editor; the panel's current DKIM
    # key is not replaced by the backup's discarded one.
    assert not any(rtype in ("NS", "TXT") for _label, rtype, _values in written)
    assert summary["not_restored"] == [{"item": "DNS record demo1.example CAA",
                                        "reason": "Unsupported DNS record type 'CAA'"}]


def test_legacy_dns_restore_keeps_default_delegation_and_reports_missing_glue(isolated_db, monkeypatch):
    from daemon import handlers_dns, nameservers
    with write_session() as session:
        _account(session)
    monkeypatch.setattr(handlers_dns, "create_zone", lambda params: None)
    monkeypatch.setattr(handlers_dns, "set_record", lambda params: None)
    monkeypatch.setattr(nameservers, "default_delegation", lambda zone: (["ns1.panel.example"], {}))
    calls = []
    monkeypatch.setattr(nameservers, "set_nameservers", lambda params: calls.append(params))
    summary = _summary()
    default_ns = {"zone": "demo1.example", "records": [
        {"name": "demo1.example", "type": "NS", "ttl": 3600, "values": ["ns1.panel.example."]}]}
    backup._recreate_dns_zone("demo1", default_ns, False, summary)
    assert calls == [] and summary["not_restored"] == []

    def refuse(params):
        raise nameservers.NameserverError("'ns1.demo1.example' is a subdomain of 'demo1.example' -- a glue A/AAAA record (its own IP address) is required")

    monkeypatch.setattr(nameservers, "set_nameservers", refuse)
    custom = {"zone": "demo1.example", "records": [
        {"name": "demo1.example", "type": "NS", "ttl": 3600, "values": ["ns1.demo1.example."]}]}
    backup._recreate_dns_zone("demo1", custom, False, summary)
    assert summary["not_restored"][0]["item"] == "nameservers of demo1.example"
    assert "glue" in summary["not_restored"][0]["reason"]


# --- error text: no tar stderr or host paths (item 3) ------------------------


def test_archive_tool_failures_keep_stderr_in_the_log_only(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(backup.settings, "mail_base", str(tmp_path / "vmail"))
    (tmp_path / "vmail" / "demo1.example" / "info").mkdir(parents=True)
    stderr = "tar: /var/vmail/demo1.example/info/cur: Cannot open: Permission denied"
    monkeypatch.setattr(backup, "run", lambda args, **kwargs: ProcResult(args, 2, "", stderr))
    monkeypatch.setattr(backup, "_update_job", lambda *a, **k: None)
    with caplog.at_level(logging.ERROR, logger="borond.backup"):
        with pytest.raises(backup.BackupError) as caught:
            backup._build_mailbox_backup("info@demo1.example", tmp_path, 1)
    message = str(caught.value)
    assert message.startswith("Could not archive the mailbox info@demo1.example (reference: ")
    assert "/var/vmail" not in message and "Permission denied" not in message
    assert "Permission denied" in caplog.text


def test_job_errors_hide_internal_details_and_host_paths(caplog):
    with caplog.at_level(logging.ERROR, logger="borond.backup"):
        internal = backup._job_error(RuntimeError("rclone: token=abc at /var/lib/boron/x"), "backup job 9")
    assert "token" not in internal and "/var/lib" not in internal
    assert internal.startswith("The operation failed because of an internal error (reference: ")
    assert internal[-17:-1] in caplog.text
    shown = backup._job_error(backup.BackupError("'/home/demo1/x' does not exist"), "backup job 9")
    assert shown == "'[server path]' does not exist"


def test_unsafe_archive_component_error_does_not_name_the_extraction_directory(tmp_path):
    evil = tmp_path / "evil.tar"
    with tarfile.open(evil, "w") as archive:
        info = tarfile.TarInfo("../outside.txt")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    target = tmp_path / "extract-here"
    target.mkdir()
    with pytest.raises(backup.BackupError) as caught:
        backup._safe_extract_tar(evil, target)
    assert str(caught.value) == "The backup contains an unsafe or unreadable archive component"


# --- full restore recreates what the account no longer has (items 4, 7) ------


def _full_artifact(tmp_path: Path, manifest: dict, databases=(), mail_domains=()) -> Path:
    content = tmp_path / "artifact-src" / "job-1"
    (content / "databases").mkdir(parents=True)
    (content / "mail").mkdir()
    (content / "manifest.json").write_text(json.dumps(manifest))
    for name in databases:
        with gzip.open(content / "databases" / f"{name}.sql.gz", "wt") as dump:
            dump.write("-- dump\n")
    for domain in mail_domains:
        with tarfile.open(content / "mail" / f"{domain}.tar.gz", "w:gz") as archive:
            info = tarfile.TarInfo(f"{domain}/info/cur/1")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"m"))
    artifact = tmp_path / "full.tar"
    with tarfile.open(artifact, "w") as archive:
        archive.add(content, arcname="job-1")
    return artifact


@pytest.fixture
def restore_handlers(isolated_db, tmp_path, monkeypatch):
    """Record the account-structure handlers a full restore calls."""
    from daemon import handlers_database, handlers_domain, handlers_mail, ols
    calls = {key: [] for key in ("domain", "database", "user", "grant", "mail_domain", "mailbox", "cron",
                                 "docroot", "vhost", "tree", "dump")}
    monkeypatch.setattr(backup.settings, "home_base", str(tmp_path / "home"))
    monkeypatch.setattr(backup.settings, "mail_base", str(tmp_path / "vmail"))
    monkeypatch.setattr(backup, "_pwnam", lambda name: SimpleNamespace(pw_uid=2000, pw_gid=2000))

    def add_domain(params):
        calls["domain"].append(params)
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == params["username"]))
            session.add(Domain(account_id=account.id, domain=params["domain"], kind=params["kind"],
                               docroot=f"{tmp_path}/home/demo1/{params['domain']}/public_html"))

    def create_database(params):
        calls["database"].append(params)
        name = f"{params['username']}_{params['name']}"
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == params["username"]))
            session.add(DatabaseGrant(account_id=account.id, db_name=name, db_user=name))
            session.add(DatabaseUser(account_id=account.id, db_user=name, host="localhost"))
        return {"db_name": name, "db_user": name, "password": "unused"}

    def create_user(params):
        calls["user"].append(params)
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == params["username"]))
            session.add(DatabaseUser(account_id=account.id, db_user=f"{params['username']}_{params['name']}",
                                     host=params.get("host", "localhost")))
        return {}

    def create_mailbox(params):
        calls["mailbox"].append(params)
        with write_session() as session:
            domain = session.scalar(select(MailDomain).where(MailDomain.domain == params["domain"]))
            session.add(MailUser(mail_domain_id=domain.id, local_part=params["local_part"], domain=params["domain"]))

    def create_mail_domain(params):
        calls["mail_domain"].append(params)
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == params["username"]))
            session.add(MailDomain(account_id=account.id, domain=params["domain"]))

    monkeypatch.setattr(handlers_domain, "add_domain", add_domain)
    monkeypatch.setattr(handlers_domain, "ensure_docroot",
                        lambda username, docroot, domain_name=None, **kw: calls["docroot"].append(domain_name))
    monkeypatch.setattr(ols, "provision_vhost", lambda account: calls["vhost"].append(account.username))
    monkeypatch.setattr(handlers_database, "create_database", create_database)
    monkeypatch.setattr(handlers_database, "create_user", create_user)
    monkeypatch.setattr(handlers_database, "grant_user", lambda params: calls["grant"].append(params))
    monkeypatch.setattr(handlers_mail, "create_mail_domain", create_mail_domain)
    monkeypatch.setattr(handlers_mail, "create_mailbox", create_mailbox)
    monkeypatch.setattr(handlers_cron, "add_cron_job", lambda params: calls["cron"].append(params))
    monkeypatch.setattr(backup, "_restore_tree", lambda artifact, destination, owner, prefix=None, select=None:
                        calls["tree"].append(Path(destination).name))
    monkeypatch.setattr(backup, "_restore_database_dump", lambda name, dump: calls["dump"].append(name))
    return calls


def test_full_restore_on_active_account_recreates_deleted_items_and_skips_foreign_names(
        restore_handlers, tmp_path, monkeypatch):
    calls = restore_handlers
    from daemon import handlers_dns
    with write_session() as session:
        account = _account(session)
        other = _account(session, username="other", uid=2001, gid=2001)
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary",
                           docroot=f"{tmp_path}/home/demo1/public_html"))
        session.add(Domain(account_id=other.id, domain="taken.example", kind="addon", docroot="/home/other/x"))
        session.add(DnsZone(account_id=account.id, zone="demo1.example"))
        mail = MailDomain(account_id=account.id, domain="demo1.example")
        session.add(mail)
        session.flush()
        session.add(MailUser(mail_domain_id=mail.id, local_part="info", domain="demo1.example"))
        session.add(DatabaseGrant(account_id=account.id, db_name="demo1_shop", db_user="demo1_shop"))
        session.add(DatabaseUser(account_id=account.id, db_user="demo1_shop", host="localhost"))
    monkeypatch.setattr(cron, "list_jobs", lambda username: [{"schedule": "0 * * * *", "command": "/bin/kept"}])
    monkeypatch.setattr(handlers_dns, "create_zone", lambda params: pytest.fail("the zone still exists"))
    monkeypatch.setattr(handlers_dns, "set_record", lambda params: pytest.fail("current DNS is not rolled back"))
    manifest = {
        "username": "demo1",
        "domains": [
            {"domain": "demo1.example", "kind": "primary", "docroot": f"{tmp_path}/home/demo1/public_html"},
            {"domain": "shop.demo1.example", "kind": "subdomain", "docroot": f"{tmp_path}/home/demo1/public_html/shop"},
            {"domain": "taken.example", "kind": "addon", "docroot": f"{tmp_path}/home/demo1/taken.example/public_html"},
        ],
        "databases": [{"db_name": "demo1_shop", "db_user": "demo1_shop"}, {"db_name": "demo1_wp", "db_user": "demo1_wp"}],
        "database_users": [
            {"db_user": "demo1_shop", "host": "localhost", "grants": [{"db_name": "demo1_shop", "preset": "all", "privileges": []}]},
            {"db_user": "demo1_ro", "host": "localhost", "grants": [{"db_name": "demo1_wp", "preset": "readonly", "privileges": []}]},
        ],
        "mail_domains": ["demo1.example", "taken.example"],
        "mail_users": [{"local_part": "info", "domain": "demo1.example", "quota_mb": 1024},
                       {"local_part": "sales", "domain": "demo1.example", "quota_mb": 2048},
                       {"local_part": "boss", "domain": "taken.example", "quota_mb": 1024}],
        "cron_jobs": [{"schedule": "0 * * * *", "command": "/bin/kept", "label": ""},
                      {"schedule": "5 4 * * *", "command": "/bin/report", "label": "report"}],
        "dns_zone": {"zone": "demo1.example", "records": [{"name": "demo1.example", "type": "A", "ttl": 60, "values": ["192.0.2.1"]}]},
    }
    artifact = _full_artifact(tmp_path, manifest, databases=("demo1_shop", "demo1_wp"),
                              mail_domains=("demo1.example", "taken.example"))
    work = tmp_path / "work"
    work.mkdir()
    summary = backup._restore_full("demo1", "active", str(artifact), str(work), progress=lambda message: None)

    assert [params["domain"] for params in calls["domain"]] == ["shop.demo1.example"]
    assert calls["domain"][0]["document_root_mode"] == "custom"
    assert calls["domain"][0]["document_root"] == "public_html/shop"
    assert [params["name"] for params in calls["database"]] == ["wp"]
    assert [params["name"] for params in calls["user"]] == ["ro"]
    assert [(grant["user"], grant["database"], grant["preset"]) for grant in calls["grant"]] == [
        ("demo1_ro", "demo1_wp", "readonly")]
    assert calls["mail_domain"] == []
    assert [(box["local_part"], box["quota_mb"], box["username"]) for box in calls["mailbox"]] == [("sales", 2048, "demo1")]
    assert [job["command"] for job in calls["cron"]] == ["/bin/report"]
    assert sorted(calls["dump"]) == ["demo1_shop", "demo1_wp"]
    assert calls["tree"] == ["demo1.example"]  # taken.example's mail is not written
    assert calls["docroot"] == ["shop.demo1.example"] and calls["vhost"] == ["demo1"]
    assert summary["skipped_domains"] == ["taken.example"]
    assert summary["mailboxes_requiring_password_reset"] == ["sales@demo1.example"]
    assert summary["database_users_requiring_password_reset"] == ["demo1_wp", "demo1_ro"]
    assert summary["not_restored"] == []
    outcome, details = backup.restore_outcome(summary)
    assert outcome == "completed with warnings"
    assert "taken.example" in details and "sales@demo1.example" in details and "demo1_ro" in details


def test_full_restore_reports_items_it_could_not_recreate(restore_handlers, tmp_path, monkeypatch):
    calls = restore_handlers
    from daemon import handlers_mail
    with write_session() as session:
        _account(session)

    def no_capacity(params):
        raise ValidationError("This plan allows at most 1 mail domain")

    monkeypatch.setattr(handlers_mail, "create_mail_domain", no_capacity)
    monkeypatch.setattr(cron, "list_jobs", lambda username: [])
    manifest = {"username": "demo1", "domains": [], "databases": [{"db_name": "renamed", "db_user": "renamed"}],
                "mail_domains": ["demo1.example"], "mail_users": [{"local_part": "info", "domain": "demo1.example"}],
                "cron_jobs": [], "dns_zone": None}
    artifact = _full_artifact(tmp_path, manifest, databases=("renamed",), mail_domains=("demo1.example",))
    work = tmp_path / "work"
    work.mkdir()
    summary = backup._restore_full("demo1", "active", str(artifact), str(work), progress=lambda message: None)
    items = [entry["item"] for entry in summary["not_restored"]]
    assert items == ["database renamed", "mail domain demo1.example"]
    assert calls["dump"] == [] and calls["tree"] == [] and calls["mailbox"] == []
    assert summary["not_restored"][1]["reason"] == "This plan allows at most 1 mail domain"
    assert backup.restore_outcome(_summary()) == ("completed", None)


# --- terminated account restore through the job worker (item 5) ---------------


def test_account_lock_is_reentrant_only_for_the_thread_holding_it(isolated_db):
    # A holder that did not opt in (an account restore of another kind, or a
    # settings change) still refuses nested settings handlers.
    with snapshot_jobs.lock("account-7"):
        with pytest.raises(BlockingIOError):
            with snapshot_jobs.lock("account-7", blocking=False, reentrant=True):
                pass
    with snapshot_jobs.lock("account-7", allow_nested=True):
        with snapshot_jobs.lock("account-7", blocking=False, reentrant=True):
            pass
        seen = []

        def other_thread():
            try:
                with snapshot_jobs.lock("account-7", blocking=False, reentrant=True):
                    seen.append("acquired")
            except BlockingIOError:
                seen.append("busy")

        worker = threading.Thread(target=other_thread)
        worker.start()
        worker.join()
        assert seen == ["busy"]
        with pytest.raises(BlockingIOError):
            with snapshot_jobs.lock("account-7", blocking=False):
                pass
    with snapshot_jobs.lock("account-7", blocking=False):
        pass


def _terminated_backup(tmp_path, status="terminated"):
    with write_session() as session:
        account = _account(session, status=status)
        destination = BackupDestination(name="d", kind="local", local_path=str(tmp_path / "dest"))
        session.add(destination)
        session.flush()
        job = BackupJob(account_id=account.id, kind="full", destination_id=destination.id, status="completed",
                        artifact_path=str(tmp_path / "dest" / "demo1.boron.tar"))
        session.add(job)
        session.flush()
        return account.id, job.id


def test_terminated_account_full_restore_is_queued_and_item_restores_refused(isolated_db, tmp_path, monkeypatch):
    _account_id, job_id = _terminated_backup(tmp_path)
    submitted = []
    monkeypatch.setattr(backup._executor, "submit", lambda fn, *args: submitted.append(args))
    queued = backup.trigger_restore({"backup_job_id": job_id, "username": "demo1"})
    assert queued["kind"] == "full" and submitted
    with write_session() as session:
        session.get(RestoreJob, queued["id"]).status = "failed"
    with pytest.raises(backup.BackupError, match="terminated"):
        backup.trigger_restore({"backup_job_id": job_id, "username": "demo1", "kind": "database",
                                "item_ref": "demo1_shop"})


def _queue_restore(account_id, job_id):
    with write_session() as session:
        row = RestoreJob(backup_job_id=job_id, account_id=account_id, kind="full", status="pending")
        session.add(row)
        session.flush()
        return row.id


def test_terminated_account_restore_job_recreates_it_inside_the_account_lock(isolated_db, tmp_path, monkeypatch):
    from daemon import account_mutation
    account_id, job_id = _terminated_backup(tmp_path)
    restore_id = _queue_restore(account_id, job_id)
    monkeypatch.setattr(backup.settings, "backup_staging_dir", str(tmp_path / "staging"))
    (tmp_path / "staging").mkdir()
    monkeypatch.setattr(backup, "_fetch_artifact_locally", lambda path, kind, tmp: str(Path(tmp) / "a.tar"))

    @account_mutation.locked
    def settings_change(params):  # reactivate_account and domain.add are guarded like this
        with write_session() as session:
            session.scalar(select(Account).where(Account.username == params["username"])).status = "active"

    def fake_restore(username, status, artifact, tmp_dir, restore_job_id=None, progress=None, reactivation=None):
        assert status == "terminated"
        settings_change({"username": username})
        return {**_summary(), "initial_password": "Generated-Pass-1!"}

    monkeypatch.setattr(backup, "_restore_full", fake_restore)
    backup._run_restore_job(restore_id)
    with write_session() as session:
        row = session.get(RestoreJob, restore_id)
        assert row.status == "completed", row.error
        assert row.progress_message == "completed with warnings"
        assert "set one from the account page" in row.error and "Generated-Pass" not in row.error
        assert session.get(Account, account_id).status == "active"


def test_failed_restore_after_reactivation_terminates_the_account_again(isolated_db, tmp_path, monkeypatch):
    from daemon import handlers_account
    account_id, job_id = _terminated_backup(tmp_path)
    restore_id = _queue_restore(account_id, job_id)
    monkeypatch.setattr(backup.settings, "backup_staging_dir", str(tmp_path / "staging"))
    (tmp_path / "staging").mkdir()
    monkeypatch.setattr(backup, "_fetch_artifact_locally", lambda path, kind, tmp: str(Path(tmp) / "a.tar"))

    def fail_after_reactivation(username, *args, **kwargs):
        with write_session() as session:
            session.scalar(select(Account).where(Account.username == username)).status = "active"
        kwargs["reactivation"]["done"] = True
        raise backup.BackupError("database 'demo1_wp' does not exist")

    terminated = []

    def terminate(params):
        with write_session() as session:
            # The job row is final and the account lock released by now.
            assert session.get(RestoreJob, restore_id).status == "failed"
            with snapshot_jobs.lock(f"account-{account_id}", blocking=False):
                pass
            session.get(Account, account_id).status = "terminated"
        terminated.append(params["username"])

    monkeypatch.setattr(backup, "_restore_full", fail_after_reactivation)
    monkeypatch.setattr(handlers_account, "terminate_account", terminate)
    backup._run_restore_job(restore_id)
    assert terminated == ["demo1"]
    with write_session() as session:
        row = session.get(RestoreJob, restore_id)
        assert row.status == "failed"
        assert row.error == "database 'demo1_wp' does not exist The partly restored account was terminated again."


def test_suspended_account_restore_is_still_refused(isolated_db, tmp_path):
    _account_id, job_id = _terminated_backup(tmp_path, status="suspended")
    with pytest.raises(backup.BackupError, match="suspended"):
        backup.trigger_restore({"backup_job_id": job_id, "username": "demo1"})


def test_only_an_administrator_can_restore_a_terminated_account(isolated_db, tmp_path, monkeypatch):
    # borond sets customer_scope for every non-admin principal: a reseller
    # recreating a terminated account would pass its plan's account limit.
    from daemon.server import CUSTOMER_SCOPE_OPERATIONS
    assert "backup.restore.trigger" in CUSTOMER_SCOPE_OPERATIONS
    _account_id, job_id = _terminated_backup(tmp_path)
    monkeypatch.setattr(backup._executor, "submit", lambda fn, *args: pytest.fail("must not queue"))
    with pytest.raises(backup.BackupError, match="Only an administrator"):
        backup.trigger_restore({"backup_job_id": job_id, "username": "demo1", "customer_scope": True})


def test_failed_restore_that_did_not_reactivate_leaves_the_account_alone(isolated_db, tmp_path, monkeypatch):
    # Another restore or import may have made the account active meanwhile;
    # only the flow that reactivated it terminates it again.
    from daemon import handlers_account
    account_id, job_id = _terminated_backup(tmp_path)
    restore_id = _queue_restore(account_id, job_id)
    monkeypatch.setattr(backup.settings, "backup_staging_dir", str(tmp_path / "staging"))
    (tmp_path / "staging").mkdir()
    monkeypatch.setattr(backup, "_fetch_artifact_locally", lambda path, kind, tmp: str(Path(tmp) / "a.tar"))

    def fail_before_reactivation(username, *args, **kwargs):
        with write_session() as session:
            session.scalar(select(Account).where(Account.username == username)).status = "active"
        raise backup.BackupError("bad manifest")

    monkeypatch.setattr(backup, "_restore_full", fail_before_reactivation)
    monkeypatch.setattr(handlers_account, "terminate_account", lambda params: pytest.fail("not ours to terminate"))
    backup._run_restore_job(restore_id)
    with write_session() as session:
        assert session.get(RestoreJob, restore_id).error == "bad manifest"
        assert session.get(Account, account_id).status == "active"


# --- portable import rollback (item 6) ----------------------------------------


@pytest.fixture
def import_job(isolated_db, tmp_path, monkeypatch):
    staging = tmp_path / "imports"
    staging.mkdir()
    monkeypatch.setattr(portable_archive.settings, "cpanel_import_staging_dir", str(staging))
    monkeypatch.setattr(portable_archive, "validate_archive",
                        lambda archive, extract, username: ({"format_version": 1, "components": []}, extract))
    source = tmp_path / "boron-archive-import-x.boron.tar"
    source.write_bytes(b"archive")
    with write_session() as session:
        row = AccountArchiveImportJob(username="demo1", source_ref=str(source), status="pending")
        session.add(row)
        session.flush()
        return row.id, str(source)


def test_import_failing_after_reactivation_terminates_the_account(import_job, monkeypatch):
    from daemon import handlers_account
    job_id, source = import_job

    def fail_after_reactivation(username, status, *args, **kwargs):
        assert status == "terminated"
        with write_session() as session:
            session.scalar(select(Account).where(Account.username == username)).status = "active"
        kwargs["reactivation"]["done"] = True
        raise backup.BackupError("mysql restore failed for 'demo1_wp'")

    terminated = []

    def terminate(params):
        terminated.append(params["username"])
        with write_session() as session:
            session.scalar(select(Account).where(Account.username == params["username"])).status = "terminated"

    monkeypatch.setattr(backup, "_restore_full", fail_after_reactivation)
    monkeypatch.setattr(handlers_account, "terminate_account", terminate)
    portable_archive._run_import(job_id, "demo1", source)
    assert terminated == ["demo1"]
    with write_session() as session:
        job = session.get(AccountArchiveImportJob, job_id)
        assert job.status == "failed"
        assert job.error.endswith("The partly restored account was terminated again.")
        # Reactivation attached logins and settings to the row: it is kept.
        assert session.scalar(select(Account).where(Account.username == "demo1")).status == "terminated"


def test_import_failing_before_reactivation_still_drops_its_placeholder(import_job, monkeypatch):
    from daemon import handlers_account
    job_id, source = import_job
    monkeypatch.setattr(backup, "_restore_full", lambda *a, **k: (_ for _ in ()).throw(backup.BackupError("bad manifest")))
    monkeypatch.setattr(handlers_account, "terminate_account", lambda params: pytest.fail("nothing to terminate"))
    portable_archive._run_import(job_id, "demo1", source)
    with write_session() as session:
        assert session.get(AccountArchiveImportJob, job_id).error == "bad manifest"
        assert session.scalar(select(Account).where(Account.username == "demo1")) is None


def test_import_reports_restore_warnings(import_job, monkeypatch):
    job_id, source = import_job

    def restore(username, status, *args, **kwargs):
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            account.status = "active"
        return {**_summary(), "initial_password": "Imported-Pass-42!", "skipped_domains": ["taken.example"]}

    monkeypatch.setattr(backup, "_restore_full", restore)
    from daemon import handlers_auth
    monkeypatch.setattr(handlers_auth, "create_panel_user", lambda params: None)
    portable_archive._run_import(job_id, "demo1", source)
    with write_session() as session:
        job = session.get(AccountArchiveImportJob, job_id)
        assert job.status == "completed" and job.progress_message == "restored with warnings"
        assert "taken.example" in job.error


# --- customer download of panel backups (item 10) -----------------------------


@pytest.fixture
def downloads(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(portable_archive.settings, "snapshot_download_dir", str(tmp_path / "downloads"))
    monkeypatch.setattr(portable_archive.pwd, "getpwnam",
                        lambda name: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))

    def fetch(path, kind, tmp):
        target = Path(tmp) / Path(path).name
        target.write_bytes(Path(path).read_bytes())
        return str(target)

    monkeypatch.setattr(backup, "_fetch_artifact_locally", fetch)
    (tmp_path / "dest").mkdir()
    rows = {}
    with write_session() as session:
        destination = BackupDestination(name="d", kind="local", local_path=str(tmp_path / "dest"))
        session.add(destination)
        session.flush()
        for username, uid in (("demo1", 2000), ("demo2", 2001)):
            account = _account(session, username=username, uid=uid, gid=uid)
            artifact = tmp_path / "dest" / f"{username}_20261102-040000_db_{username}_shop.sql.gz"
            artifact.write_bytes(b"dump of " + username.encode())
            job = BackupJob(account_id=account.id, kind="database", item_ref=f"{username}_shop",
                            destination_id=destination.id, status="completed", artifact_path=str(artifact),
                            size_bytes=artifact.stat().st_size)
            session.add(job)
            session.flush()
            rows[username] = (account.id, job.id)
    return rows


def test_customer_can_download_own_backup_once_at_a_time(downloads):
    account_id, job_id = downloads["demo1"]
    prepared = portable_archive.prepare_account_download({"username": "demo1", "job_id": job_id})
    assert Path(prepared["path"]).read_bytes() == b"dump of demo1"
    assert prepared["filename"] == "demo1_20261102-040000_db_demo1_shop.sql.gz"
    assert prepared["cleanup_id"].startswith(f"export-a{account_id}-")
    with pytest.raises(portable_archive.PortableArchiveError, match="already in progress"):
        portable_archive.prepare_account_download({"username": "demo1", "job_id": job_id})
    with pytest.raises(ValidationError, match="cleanup identifier"):
        portable_archive.cleanup_account_download({"username": "demo2", "cleanup_id": prepared["cleanup_id"]})
    assert portable_archive.cleanup_account_download(
        {"username": "demo1", "cleanup_id": prepared["cleanup_id"]}) == {"removed": True}
    again = portable_archive.prepare_account_download({"username": "demo1", "job_id": job_id})
    portable_archive.cleanup_account_download({"username": "demo1", "cleanup_id": again["cleanup_id"]})


def test_customer_cannot_download_another_accounts_backup(downloads):
    _account_id, other_job = downloads["demo2"]
    with pytest.raises(NotFoundError) as caught:
        portable_archive.prepare_account_download({"username": "demo1", "job_id": other_job})
    assert _code(caught.value) == "not_found"


def test_download_name_never_names_the_handoff_directory():
    for name in (".", "..", ".hidden", "a/b", ""):
        assert not portable_archive._ARTIFACT_NAME_RE.fullmatch(name)
    assert portable_archive._ARTIFACT_NAME_RE.fullmatch("demo1_20261102-040000.boron.tar")


def test_abandoned_download_copy_is_swept(downloads):
    _account_id, job_id = downloads["demo1"]
    prepared = portable_archive.prepare_account_download({"username": "demo1", "job_id": job_id})
    old = time.time() - portable_archive.EXPORT_STALE_SECONDS - 60
    os.utime(prepared["path"], (old, old))
    os.utime(prepared["cleanup_dir"], (old, old))
    fresh = portable_archive.prepare_account_download({"username": "demo1", "job_id": job_id})
    assert not Path(prepared["cleanup_dir"]).exists() and Path(fresh["path"]).is_file()


def test_download_needs_free_space(downloads, monkeypatch):
    _account_id, job_id = downloads["demo1"]
    monkeypatch.setattr(portable_archive.shutil, "disk_usage", lambda path: SimpleNamespace(free=1024))
    with pytest.raises(portable_archive.PortableArchiveError, match="free space"):
        portable_archive.prepare_account_download({"username": "demo1", "job_id": job_id})


def test_download_route_streams_and_cleans_up_for_the_same_account(monkeypatch, tmp_path):
    from api.routers import account_backups
    from api.security import Identity
    calls = []
    payload = tmp_path / "x.sql.gz"
    payload.write_bytes(b"x")

    def call_daemon(op, identity, **params):
        calls.append((op, params))
        return {"path": str(payload), "filename": "x.sql.gz", "cleanup_id": "export-a1-abcdef12"}

    monkeypatch.setattr(account_backups, "call_daemon", call_daemon)
    # Account ownership is enforced again by borond's account_username policy.
    identity = Identity(panel_user_id=1, username="admin", role="admin", account_id=None, auth_method="session")
    response = account_backups.download_backup("demo1", 3, identity)
    assert calls == [("backup.job.download", {"username": "demo1", "job_id": 3})]
    assert response.headers["cache-control"] == "private, no-store"
    asyncio.run(response.background())
    assert calls[-1] == ("backup.job.download.cleanup", {"username": "demo1", "cleanup_id": "export-a1-abcdef12"})
    paths = {(route.path, method) for route in account_backups.api_router.routes for method in route.methods}
    assert ("/api/v1/accounts/{username}/backups/{job_id}/download", "GET") in paths


def test_new_download_operations_are_account_scoped_and_registered():
    from daemon import server
    for op in ("backup.job.download", "backup.job.download.cleanup"):
        assert POLICY_BY_OPERATION[op] == "account_username"
        assert op in server.OP_TABLE and op in server.REPORTING_OPS


# --- snapshot destinations and their jobs (item 11) ---------------------------


def _destination(session, name):
    row = SnapshotDestination(name=name, kind="local", path=f"/var/backups/{name}", namespace=name + "ns", status="ready")
    session.add(row)
    session.flush()
    return row


def test_enabled_job_blocks_destination_delete(isolated_db):
    with write_session() as session:
        destination = _destination(session, "snap")
        session.add(SnapshotPolicy(name="nightly", destination_id=destination.id, enabled=True,
                                   options={"destination_ids": [destination.id]}))
        ident = destination.id
    with pytest.raises(ConflictError, match="nightly") as caught:
        snapshot_jobs.delete_destination({"id": ident})
    assert _code(caught.value) == "conflict"


def test_disabled_jobs_are_deleted_with_the_destination_on_request(isolated_db):
    with write_session() as session:
        snap, other = _destination(session, "snap"), _destination(session, "other")
        account = _account(session)
        lone = SnapshotPolicy(name="uw-policy", destination_id=snap.id, enabled=False,
                              options={"destination_ids": [snap.id]})
        mirrored = SnapshotPolicy(name="mirrored", destination_id=snap.id, enabled=False,
                                  options={"destination_ids": [snap.id, other.id]})
        retired = SnapshotPolicy(name="old (deleted job 9)", destination_id=snap.id, enabled=False,
                                 options={"destination_ids": [snap.id, other.id], "deleted_at": "2026-11-01"})
        session.add_all([lone, mirrored, retired])
        session.flush()
        # The retired job's history lives on the other destination only.
        session.add(SnapshotRun(policy_id=retired.id, destination_id=other.id, account_id=account.id, status="completed"))
        ident, mirrored_id, other_id = snap.id, mirrored.id, other.id
    with pytest.raises(ConflictError, match="uw-policy"):
        snapshot_jobs.delete_destination({"id": ident})
    result = snapshot_jobs.delete_destination({"id": ident, "delete_jobs": True})
    assert result["jobs_deleted"] == ["uw-policy"] and result["jobs_updated"] == ["mirrored"]
    with write_session() as session:
        assert session.scalar(select(SnapshotPolicy).where(SnapshotPolicy.name == "uw-policy")) is None
        kept = session.get(SnapshotPolicy, mirrored_id)
        assert kept.destination_id == other_id and kept.options["destination_ids"] == [other_id]
        # Still referenced by the retired job, so kept as a deleted entry.
        assert session.get(SnapshotDestination, ident).status == "deleted"


def test_unknown_destination_and_bad_flag(isolated_db):
    with pytest.raises(NotFoundError):
        snapshot_jobs.delete_destination({"id": 404})
    with write_session() as session:
        ident = _destination(session, "snap").id
    with pytest.raises(ValidationError, match="delete_jobs"):
        snapshot_jobs.delete_destination({"id": ident, "delete_jobs": "yes"})


def test_snapshot_destination_route_passes_delete_jobs(monkeypatch):
    from api.routers import backups as backups_router
    from api.security import Identity
    calls = []
    monkeypatch.setattr(backups_router, "call_daemon", lambda op, identity, **params: calls.append((op, params)) or {})
    admin = Identity(panel_user_id=1, username="admin", role="admin", account_id=None, auth_method="session")
    backups_router.snapshot_delete_destination(4, True, admin)
    assert calls == [("snapshot.destination.delete", {"id": 4, "delete_jobs": True})]


# --- firewall bans (item 8) --------------------------------------------------


def test_temporary_ban_delete_and_rollback_round_trip_with_naive_expiry(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(firewall.settings, "firewall_state_dir", str(tmp_path / "firewall"))
    commands = []

    def fake_run(args, **kwargs):
        commands.append(args)
        return ProcResult(args, 0, "Status: inactive\n" if args[:2] == ["ufw", "status"] else "", "")

    monkeypatch.setattr(firewall, "run", fake_run)
    monkeypatch.setattr(firewall, "_arm_rollback", lambda state: None)
    with write_session() as session:
        session.add(FirewallTemporaryBan(value="203.0.113.20", banned_by="admin", reason="scan",
                                         expires_at=(utcnow() + dt.timedelta(minutes=45)).replace(tzinfo=None)))
    with write_session() as session:
        ban_id = session.scalar(select(FirewallTemporaryBan.id))
    result = firewall.transactional_delete_temporary_ban({"id": ban_id})
    assert result["status"] == "unbanned" and result["pending_change"]
    assert ["ufw", "--force", "delete", "deny", "from", "203.0.113.20"] in commands
    with write_session() as session:
        assert session.scalar(select(FirewallTemporaryBan.id)) is None
    firewall.rollback_change({})
    with write_session() as session:
        restored = session.scalar(select(FirewallTemporaryBan))
        assert restored.value == "203.0.113.20" and restored.reason == "scan"
        remaining = firewall._aware_utc(restored.expires_at) - utcnow()
        assert dt.timedelta(minutes=44) < remaining <= dt.timedelta(minutes=47)


def test_unknown_ban_ids_are_not_found(isolated_db, monkeypatch):
    monkeypatch.setattr(ipban, "run", lambda args, **kwargs: pytest.fail("must not reach ufw"))
    for call in (lambda: ipban.unban_ip({"id": 99}), lambda: firewall.delete_temporary_ban({"id": 99}),
                 lambda: firewall.transactional_delete_temporary_ban({"id": 99})):
        with pytest.raises(NotFoundError) as caught:
            call()
        assert _code(caught.value) == "not_found"
    assert issubclass(ipban.IpBanNotFoundError, ipban.IpBanError)


# --- WordPress lookups (item 9) ----------------------------------------------


def test_wordpress_unknown_account_or_domain_is_not_an_internal_error(isolated_db):
    with write_session() as session:
        _account(session)
        _account(session, username="sleepy", status="suspended", uid=2002, gid=2002)
    for call, code in (
        (lambda: wordpress._account_and_domain("ghost", "demo1.example"), "not_found"),
        (lambda: wordpress._account_and_domain("demo1", "missing.example"), "not_found"),
        (lambda: wordpress._account_and_domain("sleepy", "demo1.example"), "conflict"),
        (lambda: wordpress.get_job({"job_id": 77, "username": "demo1"}), "not_found"),
        (lambda: wordpress.list_installs({"username": "ghost"}), "not_found"),
    ):
        with pytest.raises(wordpress.WordPressError) as caught:
            call()
        assert _code(caught.value) == code


# --- caller mistakes are 4xx, not 502 (item 12) -------------------------------


def test_invalid_cron_input_is_a_bad_request(isolated_db, monkeypatch):
    for call in (lambda: cron._validate_schedule("61 * * * *"), lambda: cron._validate_command("x" * 200_000)):
        with pytest.raises(cron.CronError) as caught:
            call()
        assert _code(caught.value) == "bad_request"
    monkeypatch.setattr(cron, "_read_raw", lambda username: [])
    with pytest.raises(cron.CronError) as caught:
        cron.delete_job("demo1", "00000000-0000-0000-0000-000000000000")
    assert _code(caught.value) == "not_found"
    with pytest.raises(NotFoundError):
        handlers_cron.add_cron_job({"username": "ghost", "schedule": "* * * * *", "command": "/bin/true"})


@pytest.fixture
def git_home(isolated_db, tmp_path, monkeypatch):
    home_base = tmp_path / "home"
    (home_base / "demo1" / "public_html").mkdir(parents=True)
    monkeypatch.setattr(gitrepo.filemanager.settings, "home_base", str(home_base))
    with write_session() as session:
        account = _account(session)
        session.add(GitRepo(account_id=account.id, name="site", deploy_target=None))
    return home_base / "demo1"


@pytest.mark.parametrize("target", ["../../etc", "public_html/../../other", "escape/x"])
def test_git_deploy_target_outside_home_is_a_bad_request(git_home, tmp_path, target):
    outside = tmp_path / "outside"
    outside.mkdir()
    (git_home / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(gitrepo.GitRepoError) as caught:
        gitrepo.set_deploy_target({"username": "demo1", "name": "site", "deploy_target": target})
    assert _code(caught.value) == "bad_request"
    assert str(caught.value) == gitrepo.DEPLOY_TARGET_OUTSIDE_HOME


def test_git_unknown_repo_is_not_found(git_home):
    with pytest.raises(gitrepo.GitRepoError) as caught:
        gitrepo.set_deploy_target({"username": "demo1", "name": "nope", "deploy_target": "public_html"})
    assert _code(caught.value) == "not_found"


def test_backup_of_another_accounts_database_is_a_bad_request(isolated_db):
    with write_session() as session:
        _account(session)
        other = _account(session, username="demo2", uid=2001, gid=2001)
        session.add(DatabaseGrant(account_id=other.id, db_name="demo2_shop", db_user="demo2_shop"))
    with pytest.raises(backup.BackupError, match="owned by this account") as caught:
        backup.trigger_backup({"username": "demo1", "kind": "database", "item_ref": "demo2_shop"})
    assert _code(caught.value) == "bad_request"
