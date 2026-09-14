from __future__ import annotations

import datetime as dt
import os
import pwd as real_pwd
import tarfile
import time
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import select

from daemon import cpanel_import as ci
from daemon import handlers_account as ha
from daemon import handlers_domain as hd
from shared.db import write_session
from shared.models import Account, CpanelImportJob, Domain


# --- content root / metadata parsing ----------------------------------------


def test_find_content_root_unwrapped(tmp_path):
    (tmp_path / "homedir").mkdir()
    assert ci._find_content_root(tmp_path) == tmp_path


def test_find_content_root_wrapped(tmp_path):
    inner = tmp_path / "cpmove-olduser"
    inner.mkdir()
    (inner / "mysql").mkdir()
    assert ci._find_content_root(tmp_path) == inner


def test_find_content_root_unknown_shape_falls_back_to_extract_dir(tmp_path):
    (tmp_path / "some_random_dir").mkdir()
    assert ci._find_content_root(tmp_path) == tmp_path


def test_normalize_directadmin_user_backup(tmp_path):
    source = tmp_path / "da-user"
    (source / "backup").mkdir(parents=True)
    (source / "backup" / "user.conf").write_text(
        "username=olduser\ndomain=example.com\nemail=owner@example.com\n"
    )
    (source / "backup" / "domains.list").write_text("example.com\nshop.example.com\n")
    primary = source / "domains" / "example.com" / "public_html"
    addon = source / "domains" / "shop.example.com" / "public_html"
    primary.mkdir(parents=True)
    addon.mkdir(parents=True)
    (primary / "index.php").write_text("primary")
    (addon / "index.php").write_text("shop")
    (source / "backup" / "olduser_store.sql").write_text("CREATE TABLE products (id INT);")
    mailbox = source / "imap" / "example.com" / "sales" / "Maildir"
    for folder in ("cur", "new", "tmp"):
        (mailbox / folder).mkdir(parents=True)
    (mailbox / "new" / "message").write_text("mail")
    (source / "backup" / "cron.conf").write_text("0 3 * * * /usr/bin/php /home/olduser/task.php\n")

    normalized = tmp_path / "normalized"
    normalized.mkdir()
    root = ci._normalize_directadmin_archive(source, normalized)
    info = ci._parse_account_info(root)
    domains = ci._parse_domains(root, info)

    assert info["old_username"] == "olduser"
    assert domains == [
        {"domain": "example.com", "kind": "primary"},
        {"domain": "shop.example.com", "kind": "addon"},
    ]
    assert (root / "homedir" / "public_html" / "index.php").read_text() == "primary"
    assert (root / "homedir" / "domains" / "shop.example.com" / "public_html" / "index.php").read_text() == "shop"
    assert [path.name for path in ci._parse_mysql_dumps(root)] == ["olduser_store.sql"]
    assert ci._parse_mailboxes(root, ["example.com"]) == [("example.com", "sales")]
    assert ci._parse_cron(root, "olduser") == [("0 3 * * *", "/usr/bin/php /home/olduser/task.php")]


def test_directadmin_content_root_rejects_unknown_layout(tmp_path):
    (tmp_path / "random").mkdir()
    with pytest.raises(ci.CpanelImportError, match="DirectAdmin"):
        ci._directadmin_content_root(tmp_path)


def test_external_import_job_reports_panel_and_reveals_password_once(isolated_db):
    with write_session() as session:
        job = CpanelImportJob(
            username="demo1",
            panel="directadmin",
            source="upload",
            status="completed",
            initial_password="temporary-secret",
            results=[],
        )
        session.add(job)
        session.flush()
        job_id = job.id
    first = ci.get_job({"job_id": job_id, "username": "demo1"})
    second = ci.get_job({"job_id": job_id, "username": "demo1"})
    assert first["panel"] == "directadmin"
    assert first["initial_password"] == "temporary-secret"
    assert "initial_password" not in second


def test_load_yaml_lenient_real_yaml(tmp_path):
    path = tmp_path / "main.yaml"
    path.write_text("main_domain: example.com\naddon_domains:\n  shop.example.com: {}\n")
    data = ci._load_yaml_lenient(path)
    assert data["main_domain"] == "example.com"
    assert "shop.example.com" in data["addon_domains"]


def test_load_yaml_lenient_falls_back_to_line_parsing(tmp_path):
    path = tmp_path / "olduser.yaml"
    path.write_text("DOMAIN=example.com\nEMAIL=admin@example.com\n# comment\n")
    data = ci._load_yaml_lenient(path)
    assert data["DOMAIN"] == "example.com"
    assert data["EMAIL"] == "admin@example.com"


# --- domain discovery --------------------------------------------------------


def test_parse_domains_dedupes_and_orders_primary_first(tmp_path):
    info = {
        "main_domain": "example.com",
        "addon_domains": ["shop.example.com"],
        "sub_domains": ["blog.example.com"],
        "parked_domains": ["example.com"],  # duplicate of main_domain, must not appear twice
    }
    domains = ci._parse_domains(tmp_path, info)
    assert domains[0] == {"domain": "example.com", "kind": "primary"}
    kinds = {d["domain"]: d["kind"] for d in domains}
    assert kinds["shop.example.com"] == "addon"
    assert kinds["blog.example.com"] == "subdomain"
    assert len(domains) == 3


def test_parse_domains_skips_invalid_domain_names(tmp_path):
    info = {"main_domain": "example.com", "addon_domains": ["not a domain!"], "sub_domains": [], "parked_domains": []}
    domains = ci._parse_domains(tmp_path, info)
    assert [d["domain"] for d in domains] == ["example.com"]


# --- database dump naming ----------------------------------------------------


def test_db_suffix_from_dump_strips_old_username_prefix(tmp_path):
    dump = tmp_path / "olduser_shop.sql"
    dump.touch()
    assert ci._db_suffix_from_dump(dump, "olduser") == "shop"


def test_db_suffix_from_dump_without_old_username(tmp_path):
    dump = tmp_path / "storefront.sql"
    dump.touch()
    assert ci._db_suffix_from_dump(dump, None) == "storefront"


def test_db_suffix_from_dump_sanitizes_and_truncates(tmp_path):
    dump = tmp_path / ("weird-Name!" + "x" * 60 + ".sql")
    dump.touch()
    suffix = ci._db_suffix_from_dump(dump, None)
    assert len(suffix) <= 40
    assert suffix[0].isalpha() or suffix.startswith("db_")
    assert all(c.islower() or c.isdigit() or c == "_" for c in suffix)


# --- mysql dump import: stripping the dump's own database context ----------


def test_strip_dump_database_context_removes_create_and_use():
    """Regression guard for a real, well-known cPanel/WHM migration gotcha
    (found by adversarial review, not live testing -- no real cPanel
    backup was available to confirm empirically, so this defends against
    a documented real-world risk rather than an observed failure): a
    mysqldump generated with `--databases` prepends its own `CREATE
    DATABASE`/`USE` lines naming the ORIGINAL database, which would
    silently override the caller-specified import target."""
    sql = (
        "-- MySQL dump\n"
        "CREATE DATABASE /*!32312 IF NOT EXISTS*/ `olduser_shop` /*!40100 DEFAULT CHARACTER SET utf8 */;\n"
        "USE `olduser_shop`;\n"
        "CREATE TABLE `products` (id INT);\n"
        "INSERT INTO `products` VALUES (1);\n"
    )
    stripped = ci._strip_dump_database_context(sql)
    assert "CREATE DATABASE" not in stripped
    assert "USE `olduser_shop`" not in stripped
    assert "CREATE TABLE `products`" in stripped
    assert "INSERT INTO `products`" in stripped


def test_strip_dump_database_context_leaves_plain_dump_untouched():
    sql = "CREATE TABLE `t` (id INT);\nINSERT INTO `t` VALUES (1);\n"
    assert ci._strip_dump_database_context(sql).splitlines() == sql.splitlines()


def test_import_mysql_dump_strips_context_before_invoking_mysql(tmp_path, monkeypatch):
    dump_path = tmp_path / "olduser_shop.sql"
    dump_path.write_text("CREATE DATABASE `olduser_shop`;\nUSE `olduser_shop`;\nCREATE TABLE t (id INT);\n")

    captured = {}

    def fake_run(args, input_text=None, timeout=None):
        captured["args"] = args
        captured["input_text"] = input_text
        from daemon.procutil import ProcResult

        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ci, "run", fake_run)
    monkeypatch.setattr(ci, "_write_mysql_defaults_file", lambda: str(tmp_path / "fake.cnf"))
    (tmp_path / "fake.cnf").write_text("[client]\n")

    ci._import_mysql_dump("newuser_shop", dump_path)
    assert "newuser_shop" in captured["args"]
    assert "CREATE DATABASE" not in captured["input_text"]
    assert "USE `olduser_shop`" not in captured["input_text"]


# --- BIND zone file parsing (real dnspython parse, no mocking) --------------


def test_parse_bind_zone_records_real_parse(tmp_path):
    zone_path = tmp_path / "example.com.db"
    zone_path.write_text(
        "$TTL 3600\n"
        "@ IN SOA ns1.example.com. hostmaster.example.com. ( 1 3600 900 604800 3600 )\n"
        "@ IN NS ns1.example.com.\n"
        "@ IN A 203.0.113.10\n"
        "www IN A 203.0.113.10\n"
        "@ IN MX 10 mail.example.com.\n"
        "@ IN TXT \"v=spf1 -all\"\n"
        "alias IN CNAME www.example.com.\n"
    )
    records = ci._parse_bind_zone_records(zone_path, "example.com")
    types = {(r["subdomain"], r["type"]) for r in records}
    assert ("www.example.com.", "A") in types or ("www", "A") in types
    mx = [r for r in records if r["type"] == "MX"]
    assert mx and "mail.example.com." in mx[0]["value"], (
        f"MX exchange must be fully qualified, got {mx[0]['value']!r} "
        "(dnspython relativizes names to the zone origin by default)"
    )
    cname = [r for r in records if r["type"] == "CNAME"]
    assert cname and cname[0]["value"] == "www.example.com.", (
        f"CNAME target must be fully qualified, got {cname[0]['value']!r}"
    )
    assert not any(r["type"] == "SOA" for r in records)
    assert not any(r["type"] == "NS" for r in records)


def test_parse_bind_zone_records_bad_file_raises(tmp_path):
    zone_path = tmp_path / "broken.db"
    zone_path.write_text("this is not a valid zone file {{{\n")
    with pytest.raises(ci.CpanelImportError):
        ci._parse_bind_zone_records(zone_path, "broken.example")


# --- cron parsing -------------------------------------------------------------


def test_parse_cron_extracts_schedule_and_command(tmp_path):
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir()
    (cron_dir / "olduser").write_text(
        "# a comment\n"
        "MAILTO=old@example.com\n"
        "0 3 * * * /usr/bin/php /home/olduser/cron.php\n"
        "\n"
        "*/15 * * * * /usr/bin/backup.sh --quiet\n"
    )
    jobs = ci._parse_cron(tmp_path, "olduser")
    assert jobs == [
        ("0 3 * * *", "/usr/bin/php /home/olduser/cron.php"),
        ("*/15 * * * *", "/usr/bin/backup.sh --quiet"),
    ]


def test_parse_cron_no_cron_dir_returns_empty(tmp_path):
    assert ci._parse_cron(tmp_path, "olduser") == []


# --- FTP account discovery ---------------------------------------------------


def test_parse_ftp_accounts_parses_passwd_lines(tmp_path):
    etc_dir = tmp_path / "homedir" / "etc" / "example.com"
    etc_dir.mkdir(parents=True)
    (etc_dir / "passwd").write_text(
        "ftpuser1:$1$abc$def:5001:5001:ftpuser1:/home/olduser:/bin/false\n"
        "malformed-line-no-colons\n"
    )
    found = ci._parse_ftp_accounts(tmp_path, ["example.com"])
    assert found == [("ftpuser1", "example.com")]


# --- WordPress wp-config rewrite ---------------------------------------------


def test_rewrite_wp_config_replaces_db_constants_and_preserves_rest(tmp_path):
    docroot = tmp_path / "site"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text(
        "<?php\n"
        "define('DB_NAME', 'olduser_wp');\n"
        "define('DB_USER', 'olduser_wp');\n"
        "define('DB_PASSWORD', 'oldpass');\n"
        "define('DB_HOST', 'localhost');\n"
        "$table_prefix = 'wp_custom_';\n"
        "define('AUTH_KEY', 'some-real-salt-value');\n"
    )
    detail = ci._rewrite_wp_config(str(docroot), "newuser_wp", "newuser_wp", "p@ss$word")
    content = (docroot / "wp-config.php").read_text()
    assert "newuser_wp" in content
    assert "p@ss$word" in content
    assert "oldpass" not in content
    # Salts/table prefix must survive untouched -- only DB connection consts change.
    assert "wp_custom_" in content
    assert "some-real-salt-value" in content
    assert "DB_NAME" in detail and "DB_PASSWORD" in detail


def test_rewrite_wp_config_password_with_dollar_sign_not_corrupted(tmp_path):
    """Regression guard for the exact bug class documented in
    wordpress._php_str's docstring (Phase 6b Step 1): a naive string
    substitution would let PHP's own double-quoted-string interpolation
    treat '$LU' in the password as a variable reference."""
    docroot = tmp_path / "site"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text(
        "<?php\n"
        "define('DB_NAME', 'db');\n"
        "define('DB_USER', 'user');\n"
        "define('DB_PASSWORD', 'placeholder');\n"
        "define('DB_HOST', 'localhost');\n"
    )
    ci._rewrite_wp_config(str(docroot), "db", "user", "s3cret$LU!")
    content = (docroot / "wp-config.php").read_text()
    assert "s3cret$LU!" in content
    assert "Undefined variable" not in content
    # The replacement must be single-quoted (php_str), not double-quoted,
    # so PHP never attempts interpolation on it.
    assert "\"s3cret$LU!\"" not in content


def test_rewrite_wp_config_raises_when_a_constant_is_missing(tmp_path):
    """Regression test for a real bug found by adversarial review (this
    codebase's own "validate config before apply" rule, applied to a gap
    the original "at least one matched" check didn't cover): a wp-config
    left with some DB_* constants updated and others not would silently
    produce a site with inconsistent, non-working database credentials."""
    docroot = tmp_path / "site"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text("<?php\ndefine('DB_NAME', 'olduser_wp');\n")  # missing USER/PASSWORD/HOST
    with pytest.raises(ci.CpanelImportError, match="DB_USER"):
        ci._rewrite_wp_config(str(docroot), "newuser_wp", "newuser_wp", "pw")


def test_rewrite_wp_config_skips_when_no_wp_config(tmp_path):
    docroot = tmp_path / "site"
    docroot.mkdir()
    with pytest.raises(ci._Skip):
        ci._rewrite_wp_config(str(docroot), "db", "user", "pw")


def test_rewrite_wp_config_raises_when_no_constants_matched(tmp_path):
    docroot = tmp_path / "site"
    docroot.mkdir()
    (docroot / "wp-config.php").write_text("<?php\n// no defines here\n")
    with pytest.raises(ci.CpanelImportError):
        ci._rewrite_wp_config(str(docroot), "db", "user", "pw")


# --- SSL certificate import (real cryptography-generated certs) -------------


def _make_self_signed_cert(domain: str, *, not_after_days: int = 90, sans: list[str] | None = None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    not_before = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(2, -not_after_days + 1))
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=not_after_days))
    )
    builder = builder.add_extension(
        x509.SubjectAlternativeName([x509.DNSName(n) for n in (sans or [domain])]), critical=False
    )
    cert = builder.sign(key, hashes.SHA256())
    cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
    key_bytes = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
    )
    return cert_bytes, key_bytes, key


@pytest.fixture()
def stub_ols_refresh(monkeypatch):
    monkeypatch.setattr(ci.ols, "refresh_vhost", lambda account: None)


def test_import_ssl_cert_accepts_valid_matching_cert(isolated_db, tmp_path, monkeypatch, stub_ols_refresh):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(Domain(account_id=account.id, domain="example.com", kind="primary", docroot=str(tmp_path)))

    cert_bytes, key_bytes, _ = _make_self_signed_cert("example.com")
    cert_path = tmp_path / "example.com.crt"
    key_path = tmp_path / "example.com.key"
    cert_path.write_bytes(cert_bytes)
    key_path.write_bytes(key_bytes)

    # _import_ssl_cert hardcodes /etc/letsencrypt/live/<domain> (matching
    # daemon/ols.py's own letsencrypt_cert_paths convention) -- this test
    # necessarily writes there for real and cleans up afterward rather than
    # mocking Path, since faking a hardcoded absolute path well would just
    # be testing the mock instead of the real install location every other
    # domain's cert already lives at.
    detail = ci._import_ssl_cert("demo1", "example.com", cert_path, key_path)
    assert "installed" in detail

    with write_session() as session:
        row = session.scalar(select(Domain).where(Domain.domain == "example.com"))
        assert row.ssl_status == "active"

    # cleanup: this test necessarily wrote to the real /etc/letsencrypt path
    # (the function hardcodes it) -- remove what it created.
    import shutil as _shutil

    _shutil.rmtree("/etc/letsencrypt/live/example.com", ignore_errors=True)


def test_import_ssl_cert_rejects_expired(tmp_path):
    cert_bytes, key_bytes, _ = _make_self_signed_cert("expired.example.com", not_after_days=-30)
    cert_path = tmp_path / "c.crt"
    key_path = tmp_path / "c.key"
    cert_path.write_bytes(cert_bytes)
    key_path.write_bytes(key_bytes)
    with pytest.raises(ci._Skip, match="expired"):
        ci._import_ssl_cert("demo1", "expired.example.com", cert_path, key_path)


def test_import_ssl_cert_rejects_domain_not_covered(tmp_path):
    cert_bytes, key_bytes, _ = _make_self_signed_cert("other.example.com")
    cert_path = tmp_path / "c.crt"
    key_path = tmp_path / "c.key"
    cert_path.write_bytes(cert_bytes)
    key_path.write_bytes(key_bytes)
    with pytest.raises(ci._Skip, match="does not cover"):
        ci._import_ssl_cert("demo1", "example.com", cert_path, key_path)


def test_import_ssl_cert_rejects_mismatched_key(tmp_path):
    cert_bytes, _key_bytes, _ = _make_self_signed_cert("example.com")
    _, other_key_bytes, _ = _make_self_signed_cert("example.com")
    cert_path = tmp_path / "c.crt"
    key_path = tmp_path / "c.key"
    cert_path.write_bytes(cert_bytes)
    key_path.write_bytes(other_key_bytes)
    with pytest.raises(ci._Skip, match="do not match"):
        ci._import_ssl_cert("demo1", "example.com", cert_path, key_path)


def test_import_ssl_cert_accepts_wildcard_match(tmp_path):
    cert_bytes, key_bytes, _ = _make_self_signed_cert("wild.example.com", sans=["*.example.com"])
    cert_path = tmp_path / "c.crt"
    key_path = tmp_path / "c.key"
    cert_path.write_bytes(cert_bytes)
    key_path.write_bytes(key_bytes)
    # Only reachable up to the DB lookup (no account/domain row here) --
    # confirms the coverage check itself passes before that later failure.
    with pytest.raises(Exception) as excinfo:
        ci._import_ssl_cert("demo1", "shop.example.com", cert_path, key_path)
    assert "does not cover" not in str(excinfo.value)


# --- the docroot-permission-reassertion fix ---------------------------------


def test_reassert_docroot_perms_restores_mode_after_simulated_widening(isolated_db, tmp_path, monkeypatch):
    """Regression test for the real bug found reviewing this module: a
    shutil.copytree(..., dirs_exist_ok=True) over an existing docroot resets
    its mode to the source's (often world-readable) -- ensure_docroot must
    restore 0750 (+ the ACL grant) afterward."""
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (os.getuid(), os.getgid()))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    from shared.config import settings

    monkeypatch.setattr(settings, "home_base", str(tmp_path))
    fake_pw = real_pwd.struct_passwd(("demo1", "x", os.getuid(), os.getgid(), "", str(tmp_path), "/usr/sbin/nologin"))
    # handlers_domain.ensure_docroot does a local `import pwd` inside the
    # function body -- patching the real pwd module (the same cached object
    # that local import resolves to via sys.modules) is what actually takes
    # effect, since `hd` itself never binds a module-level `pwd` attribute.
    monkeypatch.setattr(real_pwd, "getpwnam", lambda name: fake_pw)

    docroot = tmp_path / "demo1" / "public_html"
    docroot.mkdir(parents=True)
    os.chmod(docroot, 0o750)

    import shutil as _shutil

    src = tmp_path / "cpanel_src"
    src.mkdir()
    os.chmod(src, 0o755)
    (src / "index.php").write_text("<?php echo 'hi';")
    _shutil.copytree(src, docroot, dirs_exist_ok=True)
    assert oct(os.stat(docroot).st_mode & 0o777) == "0o755", "sanity check: the simulated widening actually happened"

    ci._reassert_docroot_perms_step("demo1", str(docroot))

    assert oct(os.stat(docroot).st_mode & 0o777) == "0o750"


# --- trigger_import / get_job guards ----------------------------------------


def test_trigger_import_rejects_existing_account(isolated_db, monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "existing1"})
    with pytest.raises(ci.CpanelImportError):
        ci.trigger_import({"username": "existing1", "source": "upload", "source_ref": "/tmp/whatever.tar.gz"})


def test_trigger_import_requires_source_ref(isolated_db):
    with pytest.raises(Exception):
        ci.trigger_import({"username": "newacct1", "source": "upload"})


def test_trigger_import_rejects_second_concurrent_job(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "_executor", ci._executor)  # no-op, keep real executor but we won't let jobs actually run

    def never_runs(job_id, params):
        # Prevent the background thread from actually processing (no real
        # archive at this fake path) -- this test only checks the guard
        # against a second *trigger* while one is pending/running.
        time.sleep(2)

    monkeypatch.setattr(ci, "_run_import_job", never_runs)
    ci.trigger_import({"username": "newacct2", "source": "upload", "source_ref": str(tmp_path / "x.tar.gz")})
    with pytest.raises(ci.CpanelImportError):
        ci.trigger_import({"username": "newacct2", "source": "upload", "source_ref": str(tmp_path / "y.tar.gz")})


def test_get_job_wrong_username_not_found(isolated_db, tmp_path, monkeypatch):
    def never_runs(job_id, params):
        time.sleep(2)

    monkeypatch.setattr(ci, "_run_import_job", never_runs)
    result = ci.trigger_import({"username": "newacct3", "source": "upload", "source_ref": str(tmp_path / "x.tar.gz")})
    with pytest.raises(ci.CpanelImportError):
        ci.get_job({"job_id": result["id"], "username": "otheruser1"})
    # The real owner can still read it.
    job = ci.get_job({"job_id": result["id"], "username": "newacct3"})
    assert job["id"] == result["id"]


def test_get_job_missing_raises(isolated_db):
    with pytest.raises(ci.CpanelImportError):
        ci.get_job({"job_id": 999999, "username": "ghostuser1"})


# --- archive extraction safety -----------------------------------------------


def test_extract_archive_rejects_path_traversal(tmp_path):
    """tarfile.extractall(..., filter='data') is the same stdlib tar-slip
    defense daemon/backup.py's own restore path relies on for an
    externally-sourced artifact -- confirm it actually rejects a crafted
    '../' member for THIS archive's extraction call, not just assumed."""
    archive_path = tmp_path / "evil.tar"
    with tarfile.open(archive_path, "w") as tf:
        info = tarfile.TarInfo(name="../../../../tmp/boron_cpanel_zipslip.txt")
        data = b"pwned"
        info.size = len(data)
        import io

        tf.addfile(info, io.BytesIO(data))

    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    with pytest.raises(ci.CpanelImportError):
        ci._extract_archive(archive_path, extract_dir)
    assert not os.path.exists("/tmp/boron_cpanel_zipslip.txt")


def test_extract_archive_rejects_decompression_bomb(tmp_path, monkeypatch):
    """Security-audit-2 (Medium): the compressed-size cap does not bound the
    decompressed size, so extraction must refuse an archive whose declared
    member sizes exceed the extraction limit before writing it to the
    root-owned staging disk."""
    import io

    from shared.config import settings

    monkeypatch.setattr(settings, "cpanel_import_max_extracted_bytes", 1024)  # 1KB cap for the test

    archive_path = tmp_path / "bomb.tar"
    with tarfile.open(archive_path, "w") as tf:
        info = tarfile.TarInfo(name="homedir/big.bin")
        payload = b"A" * 4096  # declared 4KB > 1KB cap
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    with pytest.raises(ci.CpanelImportError, match="extraction limit"):
        ci._extract_archive(archive_path, extract_dir)
    # Nothing was extracted.
    assert not (extract_dir / "homedir").exists()


def test_extract_archive_allows_normal_size(tmp_path):
    """A legitimately-sized archive still extracts normally under the cap."""
    import io

    archive_path = tmp_path / "ok.tar"
    with tarfile.open(archive_path, "w") as tf:
        info = tarfile.TarInfo(name="homedir/index.php")
        payload = b"<?php echo 'hi';"
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    ci._extract_archive(archive_path, extract_dir)
    assert (extract_dir / "homedir" / "index.php").read_bytes() == b"<?php echo 'hi';"
