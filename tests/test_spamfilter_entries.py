import pytest

from daemon import spamfilter as sf
from shared.validation import ValidationError


@pytest.fixture(autouse=True)
def _no_dovecot(monkeypatch):
    """Every entry mutation regenerates + reinstalls the global Sieve script
    and reloads dovecot -- none of that is available in the test sandbox
    (no real sievec/dovecot), so the install/reload/verify steps are
    replaced with no-ops, same "real system call needs a real box" mocking
    convention every other handler test in this project uses. Sieve TEXT
    GENERATION itself (build_global_sieve_source) is still exercised for
    real -- only the filesystem/subprocess side effects are stubbed."""
    monkeypatch.setattr(sf, "_install_global_sieve_script", lambda content=None: None)
    monkeypatch.setattr(sf, "_dovecot_reload", lambda: sf.StepResult(True))
    monkeypatch.setattr(sf, "_dovecot_verify", lambda: sf.StepResult(True))


def test_add_entry_creates_row(isolated_db):
    result = sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "blacklist", "pattern": "spammer@evil.com"})
    assert result["domain"] == "example.com"
    assert result["local_part"] == "sales"
    assert result["kind"] == "blacklist"
    assert result["pattern"] == "spammer@evil.com"
    assert result["id"] is not None


def test_add_entry_normalizes_domain_pattern(isolated_db):
    result = sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "whitelist", "pattern": "Partner.COM"})
    assert result["pattern"] == "partner.com"


def test_add_entry_rejects_invalid_pattern(isolated_db):
    with pytest.raises(ValidationError):
        sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "blacklist", "pattern": "not a valid pattern!"})


def test_add_entry_rejects_bad_kind(isolated_db):
    with pytest.raises(ValidationError):
        sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "graylist", "pattern": "evil.com"})


def test_add_entry_is_idempotent_for_duplicate(isolated_db):
    first = sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "blacklist", "pattern": "evil.com"})
    second = sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "blacklist", "pattern": "evil.com"})
    assert first["id"] == second["id"]


def test_list_entries_scoped_to_mailbox(isolated_db):
    sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "blacklist", "pattern": "evil.com"})
    sf.add_entry({"domain": "example.com", "local_part": "support", "kind": "whitelist", "pattern": "vip.com"})
    result = sf.list_entries({"domain": "example.com", "local_part": "sales"})
    assert len(result["entries"]) == 1
    assert result["entries"][0]["pattern"] == "evil.com"


def test_delete_entry_removes_it(isolated_db):
    added = sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "blacklist", "pattern": "evil.com"})
    sf.delete_entry({"id": added["id"], "domain": "example.com"})
    result = sf.list_entries({"domain": "example.com", "local_part": "sales"})
    assert result["entries"] == []


def test_delete_entry_missing_raises(isolated_db):
    with pytest.raises(RuntimeError):
        sf.delete_entry({"id": 9999, "domain": "example.com"})


def test_delete_entry_cross_domain_raises(isolated_db):
    """Cross-account IDOR guard: an id that's real but belongs to a
    different domain must be rejected identically to a missing id, not
    silently deleted."""
    added = sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "blacklist", "pattern": "evil.com"})
    with pytest.raises(RuntimeError):
        sf.delete_entry({"id": added["id"], "domain": "other-account.example"})
    result = sf.list_entries({"domain": "example.com", "local_part": "sales"})
    assert len(result["entries"]) == 1  # not deleted


def test_import_entries_adds_multiple_and_skips_blank_and_comments(isolated_db):
    text = "evil.com\n# a comment\n\nspammer@bad.net\n"
    result = sf.import_entries({"domain": "example.com", "local_part": "sales", "kind": "blacklist", "text": text})
    assert len(result["added"]) == 2
    assert result["errors"] == []


def test_import_entries_reports_invalid_lines_without_failing_whole_batch(isolated_db):
    text = "evil.com\nnot valid!!\n"
    result = sf.import_entries({"domain": "example.com", "local_part": "sales", "kind": "whitelist", "text": text})
    assert len(result["added"]) == 1
    assert len(result["errors"]) == 1


def test_delete_entries_for_mailbox_cascades(isolated_db):
    sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "blacklist", "pattern": "evil.com"})
    sf.delete_entries_for_mailbox("example.com", "sales")
    result = sf.list_entries({"domain": "example.com", "local_part": "sales"})
    assert result["entries"] == []


def test_build_global_sieve_source_includes_whitelist_and_blacklist_blocks(isolated_db):
    sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "whitelist", "pattern": "vip@partner.com"})
    sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "blacklist", "pattern": "spammer@evil.com"})
    source = sf.build_global_sieve_source()
    assert 'envelope :is "to" "sales@example.com"' in source
    assert 'address :all :is "from" "vip@partner.com"' in source
    assert "keep;" in source
    assert 'reject "' in source
    assert "X-Spam-Flag" in source  # static tail rule still present
    assert '"reject"' in source.splitlines()[0]  # require line includes the extension


def test_build_global_sieve_source_omits_reject_extension_when_no_blacklist(isolated_db):
    sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "whitelist", "pattern": "vip@partner.com"})
    source = sf.build_global_sieve_source()
    assert '"reject"' not in source.splitlines()[0]


def test_build_global_sieve_source_domain_wide_pattern_uses_domain_test(isolated_db):
    sf.add_entry({"domain": "example.com", "local_part": "sales", "kind": "blacklist", "pattern": "evil.com"})
    source = sf.build_global_sieve_source()
    assert 'address :domain :is "from" "evil.com"' in source
