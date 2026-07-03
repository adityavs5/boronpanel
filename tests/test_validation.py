import pytest

from shared.validation import (
    ValidationError,
    validate_db_identifier,
    validate_domain,
    validate_git_repo_name,
    validate_hotlink_allowed_domains,
    validate_htpasswd_username,
    validate_ip_or_cidr,
    validate_mailbox_local_part,
    validate_password_strength,
    validate_php_error_reporting,
    validate_php_max_execution_time,
    validate_php_memory_limit,
    validate_php_size,
    validate_php_version,
    validate_protected_dir_relative_path,
    validate_record_type,
    validate_redirect_path,
    validate_redirect_status_code,
    validate_redirect_target,
    validate_ssh_key_text,
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


@pytest.mark.parametrize(
    "good", ["MyStr0ngPass!", "C0rrectHorse!", "Tr0ub4dor&3!!", "Aa1!Aa1!Aa1!"]
)
def test_valid_password_strength(good):
    assert validate_password_strength(good) == good


def test_password_strength_rejects_too_short():
    # Phase 4: minimum raised from 8 to 12 characters -- 11 chars, would
    # otherwise satisfy every composition rule.
    with pytest.raises(ValidationError):
        validate_password_strength("Sh0rtPass!!")


def test_password_strength_rejects_too_long():
    with pytest.raises(ValidationError):
        validate_password_strength("Aa1!" * 100)


def test_password_strength_rejects_common_passwords():
    with pytest.raises(ValidationError):
        validate_password_strength("password123!")  # 12 chars, still blocklisted
    with pytest.raises(ValidationError):
        validate_password_strength("Password123!")  # case-insensitive match


def test_password_strength_rejects_repeated_character():
    with pytest.raises(ValidationError):
        validate_password_strength("aaaaaaaaaaaa")


def test_password_strength_rejects_purely_numeric():
    with pytest.raises(ValidationError):
        validate_password_strength("123456789012")


def test_password_strength_accepts_exactly_minimum_length():
    assert validate_password_strength("Aa1!Aa1!Aa1!") == "Aa1!Aa1!Aa1!"  # exactly 12


# Phase 4: composition rules (12+ chars, upper+lower+number+special all
# required) -- explicit goal instruction, superseding Phase 3's
# NIST-800-63B-aligned length-only policy (see shared/validation.py's
# comment for the full reasoning on why this changed).
def test_password_strength_rejects_missing_uppercase():
    with pytest.raises(ValidationError):
        validate_password_strength("lowercase123!!")


def test_password_strength_rejects_missing_lowercase():
    with pytest.raises(ValidationError):
        validate_password_strength("UPPERCASE123!!")


def test_password_strength_rejects_missing_number():
    with pytest.raises(ValidationError):
        validate_password_strength("NoNumbersHere!!")


def test_password_strength_rejects_missing_special_char():
    with pytest.raises(ValidationError):
        validate_password_strength("NoSpecialChars123")


def test_validate_hotlink_allowed_domains_normalizes_and_dedupes():
    assert validate_hotlink_allowed_domains(["CDN.Example", "cdn.example"]) == ["cdn.example"]


def test_validate_hotlink_allowed_domains_rejects_non_list():
    with pytest.raises(ValidationError):
        validate_hotlink_allowed_domains("cdn.example")


def test_validate_hotlink_allowed_domains_rejects_too_many():
    with pytest.raises(ValidationError):
        validate_hotlink_allowed_domains([f"d{i}.example" for i in range(25)])


def test_validate_hotlink_allowed_domains_rejects_invalid_domain():
    with pytest.raises(ValidationError):
        validate_hotlink_allowed_domains(["not a domain"])


@pytest.mark.parametrize(
    "good,expected",
    [
        ("203.0.113.7", "203.0.113.7"),
        ("203.0.113.0/24", "203.0.113.0/24"),
        ("2001:db8::1", "2001:db8::1"),
        ("2001:db8::/32", "2001:db8::/32"),
        ("203.0.113.0/255.255.255.0", "203.0.113.0/24"),  # normalized
    ],
)
def test_valid_ip_or_cidr(good, expected):
    assert validate_ip_or_cidr(good) == expected


@pytest.mark.parametrize(
    "bad",
    ["", "not-an-ip", "999.999.999.999", "203.0.113.7/99", "203.0.113.7/-1", "  ", None, "203.0.113.1; rm -rf /"],
)
def test_invalid_ip_or_cidr(bad):
    with pytest.raises(ValidationError):
        validate_ip_or_cidr(bad)


@pytest.mark.parametrize("good", ["alice", "bob.smith", "user-1", "a_b_c", "x" * 64])
def test_valid_htpasswd_username(good):
    assert validate_htpasswd_username(good) == good


@pytest.mark.parametrize("bad", ["", "user:name", "user name", "user\n", "x" * 65, None, "üser"])
def test_invalid_htpasswd_username(bad):
    with pytest.raises(ValidationError):
        validate_htpasswd_username(bad)


def test_validate_protected_dir_relative_path_strips_slashes():
    assert validate_protected_dir_relative_path("/public_html/members/") == "public_html/members"


def test_validate_protected_dir_relative_path_rejects_empty():
    with pytest.raises(ValidationError):
        validate_protected_dir_relative_path("")
    with pytest.raises(ValidationError):
        validate_protected_dir_relative_path("   ")


def test_validate_protected_dir_relative_path_rejects_nul_byte():
    with pytest.raises(ValidationError):
        validate_protected_dir_relative_path("public_html/\x00evil")


@pytest.mark.parametrize(
    "bad",
    [
        'public_html/x"; touch /tmp/pwned; echo "',
        "public_html/$(touch /tmp/pwned)",
        "public_html/`touch /tmp/pwned`",
        "public_html/x\nrealm evil {",
        "public_html/x|evil",
        "public_html/x;evil",
        "public_html/x&evil",
        "public_html/x'evil",
    ],
)
def test_validate_protected_dir_relative_path_rejects_shell_metacharacters(bad):
    """Security audit F5: this value is later interpolated unescaped into
    a bash post-receive hook (daemon/gitrepo.py) and an OLS vhost realm
    block (daemon/fileauth.py) -- the charset restriction is the actual
    injection defense for both, not just cosmetic."""
    with pytest.raises(ValidationError):
        validate_protected_dir_relative_path(bad)


@pytest.mark.parametrize("good", ["a", "my-site", "site2", "x" * 63])
def test_valid_git_repo_name(good):
    assert validate_git_repo_name(good) == good


@pytest.mark.parametrize("bad", ["", "My-Site", "-leading", "1abc", "a_b", "a.b", "a/b", "x" * 64, None])
def test_invalid_git_repo_name(bad):
    with pytest.raises(ValidationError):
        validate_git_repo_name(bad)


def test_validate_ssh_key_text_strips_and_accepts_single_line():
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDlOBGLYLQypX7LRM27WBG8IVgVQKXCG2NxOgYcxaaOh user@host"
    assert validate_ssh_key_text(f"  {key}  ") == key


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_validate_ssh_key_text_rejects_empty(bad):
    with pytest.raises(ValidationError):
        validate_ssh_key_text(bad)


def test_validate_ssh_key_text_rejects_multiline():
    with pytest.raises(ValidationError):
        validate_ssh_key_text("ssh-ed25519 AAAA...\nssh-ed25519 BBBB...")


def test_validate_ssh_key_text_rejects_nul_byte():
    with pytest.raises(ValidationError):
        validate_ssh_key_text("ssh-ed25519 AAAA\x00...")


def test_validate_ssh_key_text_rejects_over_length():
    with pytest.raises(ValidationError):
        validate_ssh_key_text("ssh-ed25519 " + "A" * 9000)


def test_validate_cron_mailto_empty_is_allowed():
    from shared.validation import validate_cron_mailto

    assert validate_cron_mailto("") == ""
    assert validate_cron_mailto(None) == ""
    assert validate_cron_mailto("   ") == ""


def test_validate_cron_mailto_valid_email_passes_through():
    from shared.validation import validate_cron_mailto

    assert validate_cron_mailto("alerts@example.com") == "alerts@example.com"


def test_validate_cron_mailto_rejects_root_variants():
    from shared.validation import validate_cron_mailto

    for bad in ("root", "root@localhost", "ROOT@example.com", "root@anything.tld"):
        with pytest.raises(ValidationError):
            validate_cron_mailto(bad)


def test_validate_cron_mailto_rejects_bare_username():
    from shared.validation import validate_cron_mailto

    with pytest.raises(ValidationError):
        validate_cron_mailto("someuser")  # no @domain -- not a syntactically valid email


def test_validate_cron_mailto_rejects_malformed():
    from shared.validation import validate_cron_mailto

    with pytest.raises(ValidationError):
        validate_cron_mailto("not an email")


# --- generate_strong_password (Phase 4 feature 12: codebase-wide audit) ----


def test_generate_strong_password_passes_its_own_validator():
    from shared.validation import generate_strong_password, validate_password_strength

    for _ in range(50):
        pw = generate_strong_password()
        assert validate_password_strength(pw) == pw  # must never raise


def test_generate_strong_password_respects_length():
    from shared.validation import generate_strong_password

    pw = generate_strong_password(30)
    assert len(pw) == 30


def test_generate_strong_password_rejects_too_short():
    from shared.validation import generate_strong_password

    with pytest.raises(ValueError):
        generate_strong_password(8)


def test_generate_strong_password_is_random():
    from shared.validation import generate_strong_password

    assert len({generate_strong_password() for _ in range(20)}) == 20
