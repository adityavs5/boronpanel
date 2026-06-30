import pytest

from shared.validation import (
    ValidationError,
    validate_db_identifier,
    validate_domain,
    validate_mailbox_local_part,
    validate_php_version,
    validate_record_type,
    validate_username,
)


@pytest.mark.parametrize("good", ["a", "ab12", "x" * 16, "customer1"])
def test_valid_usernames(good):
    assert validate_username(good) == good


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
    for rt in ["A", "AAAA", "CNAME", "MX", "TXT"]:
        assert validate_record_type(rt) == rt
    with pytest.raises(ValidationError):
        validate_record_type("SRV")


def test_mailbox_local_part():
    assert validate_mailbox_local_part("john.doe") == "john.doe"
    with pytest.raises(ValidationError):
        validate_mailbox_local_part("John Doe")
