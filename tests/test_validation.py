import pytest

from shared.validation import (
    ValidationError,
    validate_db_identifier,
    validate_domain,
    validate_mailbox_local_part,
    validate_password_strength,
    validate_php_error_reporting,
    validate_php_max_execution_time,
    validate_php_memory_limit,
    validate_php_size,
    validate_php_version,
    validate_record_type,
    validate_redirect_path,
    validate_redirect_status_code,
    validate_redirect_target,
    validate_username,
)


@pytest.mark.parametrize("good", ["a", "ab12", "x" * 16, "customer1"])
def test_valid_usernames(good):
    assert validate_username(good) == good


def test_username_rejects_trailing_newline():
    """Regression test for a real bug found by live testing: Python's `$`
    regex anchor matches either at the true end of a string OR just
    before a single trailing newline, so a bare "^...$"-anchored regex
    incorrectly accepted "root\\n" as a valid username (distinct from,
    but confusable with, the actually-reserved "root"). Applies to every
    \\A/\\Z-anchored validator in shared/validation.py, not just this one --
    caught here, in the module's own oldest validator, after being found
    via a newer validator's own test coverage."""
    with pytest.raises(ValidationError):
        validate_username("root\n")
    with pytest.raises(ValidationError):
        validate_username("customer1\n")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "1abc",  # must start with a letter
        "Abc",  # no uppercase
        "ab_c",  # no underscore
        "x" * 17,  # too long
        "ab c",
        "ab.c",
    ],
)
def test_invalid_usernames(bad):
    with pytest.raises(ValidationError):
        validate_username(bad)


@pytest.mark.parametrize("reserved", ["root", "vmail", "mysql", "forgehost", "admin"])
def test_reserved_usernames_rejected(reserved):
    with pytest.raises(ValidationError):
        validate_username(reserved)


@pytest.mark.parametrize(
    "good",
    ["example.com", "sub.example.com", "xn--mnchen-3ya.de", "a.co"],
)
def test_valid_domains(good):
    assert validate_domain(good)


@pytest.mark.parametrize(
    "bad",
    ["", "-bad.com", "bad-.com", "no_underscores_allowed.com", "toolong" * 50 + ".com", "nodot"],
)
def test_invalid_domains(bad):
    with pytest.raises(ValidationError):
        validate_domain(bad)


def test_domain_case_and_trailing_dot_normalized():
    assert validate_domain("Example.COM.") == "example.com"


def test_php_version_allowed():
    assert validate_php_version("8.3", ("8.1", "8.3")) == "8.3"


def test_php_version_rejected():
    with pytest.raises(ValidationError):
        validate_php_version("7.0", ("8.1", "8.3"))


@pytest.mark.parametrize("bad", ["DROP TABLE", "1abc", "a-b", "a;b", "a" * 70])
def test_db_identifier_rejected(bad):
    with pytest.raises(ValidationError):
        validate_db_identifier(bad)


def test_db_identifier_accepted():
    assert validate_db_identifier("customer1_shop") == "customer1_shop"


def test_record_type_allowed_set():
    # Phase 3 feature 1 widened this from A/AAAA/CNAME/MX/TXT to also
    # include PTR/SRV/CAA (the full zone editor's record-type scope).
    for rt in ["A", "AAAA", "CNAME", "MX", "TXT", "PTR", "SRV", "CAA"]:
        assert validate_record_type(rt) == rt
    with pytest.raises(ValidationError):
        validate_record_type("NS")


def test_mailbox_local_part():
    assert validate_mailbox_local_part("john.doe") == "john.doe"
    with pytest.raises(ValidationError):
        validate_mailbox_local_part("John Doe")


@pytest.mark.parametrize("good", ["256M", "1G", "2048K", "1M"])
def test_valid_php_sizes(good):
    assert validate_php_size(good, "field") == good


@pytest.mark.parametrize("bad", ["-1", "0", "999999M", "abc", "256", "256X"])
def test_invalid_php_sizes(bad):
    with pytest.raises(ValidationError):
        validate_php_size(bad, "field")


def test_php_memory_limit_rejects_unlimited():
    with pytest.raises(ValidationError):
        validate_php_memory_limit("-1")
    assert validate_php_memory_limit("256M") == "256M"


@pytest.mark.parametrize("good", [1, 30, 300])
def test_valid_php_max_execution_time(good):
    assert validate_php_max_execution_time(good) == good


@pytest.mark.parametrize("bad", [0, -1, 301])
def test_invalid_php_max_execution_time(bad):
    with pytest.raises(ValidationError):
        validate_php_max_execution_time(bad)


def test_php_error_reporting_accepts_standard_expressions():
    assert validate_php_error_reporting("E_ALL & ~E_DEPRECATED & ~E_STRICT")
    assert validate_php_error_reporting("E_ALL")


def test_php_error_reporting_rejects_injection_attempt():
    with pytest.raises(ValidationError):
        validate_php_error_reporting('E_ALL"; rm -rf /; "')
    with pytest.raises(ValidationError):
        validate_php_error_reporting("E_ALL\nphp_admin_value evil 1")


@pytest.mark.parametrize("good", ["/", "/old-page", "/blog/post-1.html", "/a/b/c"])
def test_valid_redirect_paths(good):
    assert validate_redirect_path(good) == good


@pytest.mark.parametrize("bad", ["old-page", "/a b", "/a\n", "/a[b]", "/a*", "/a$", "/(a)"])
def test_invalid_redirect_paths(bad):
    with pytest.raises(ValidationError):
        validate_redirect_path(bad)


@pytest.mark.parametrize("good", ["https://example.com/", "http://example.com/new-page", "https://sub.example.com:8080/x"])
def test_valid_redirect_targets(good):
    assert validate_redirect_target(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-url",
        "ftp://example.com/",
        "javascript:alert(1)",
        "https://example.com/ [R=301]",
        "https://example.com/\nSet-Cookie: evil",
    ],
)
def test_invalid_redirect_targets(bad):
    with pytest.raises(ValidationError):
        validate_redirect_target(bad)


def test_redirect_status_code_allows_only_301_302():
    assert validate_redirect_status_code(301) == 301
    assert validate_redirect_status_code(302) == 302
    with pytest.raises(ValidationError):
        validate_redirect_status_code(200)
    with pytest.raises(ValidationError):
        validate_redirect_status_code(307)


@pytest.mark.parametrize("good", ["a-real-password-1", "correcthorsebattery", "8-characters!"])
def test_valid_password_strength(good):
    assert validate_password_strength(good) == good


def test_password_strength_rejects_too_short():
    with pytest.raises(ValidationError):
        validate_password_strength("short12")  # 7 chars, below the 8-char minimum


def test_password_strength_rejects_too_long():
    with pytest.raises(ValidationError):
        validate_password_strength("a" * 300)


def test_password_strength_rejects_common_passwords():
    with pytest.raises(ValidationError):
        validate_password_strength("password123")
    with pytest.raises(ValidationError):
        validate_password_strength("Password123")  # case-insensitive match


def test_password_strength_rejects_repeated_character():
    with pytest.raises(ValidationError):
        validate_password_strength("aaaaaaaaaa")


def test_password_strength_rejects_purely_numeric():
    with pytest.raises(ValidationError):
        validate_password_strength("12345678901234")


def test_password_strength_accepts_exactly_minimum_length():
    assert validate_password_strength("12345abc") == "12345abc"  # exactly 8, not purely numeric
