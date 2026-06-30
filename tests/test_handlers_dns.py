import pytest

from daemon import handlers_dns as hdns
from shared.validation import ValidationError


def test_validate_ipv4_accepts_good():
    assert hdns._validate_ipv4("192.0.2.10") == "192.0.2.10"


def test_validate_ipv4_rejects_bad():
    with pytest.raises(ValidationError):
        hdns._validate_ipv4("not-an-ip")
    with pytest.raises(ValidationError):
        hdns._validate_ipv4("999.999.999.999")


def test_validate_ipv6_accepts_good():
    assert hdns._validate_ipv6("2001:db8::1") == "2001:db8::1"


def test_validate_ipv6_rejects_ipv4():
    with pytest.raises(ValidationError):
        hdns._validate_ipv6("192.0.2.10")


def test_validate_mx_format():
    assert hdns._validate_mx("10 mail.example.com") == "10 mail.example.com."


def test_validate_mx_rejects_missing_priority():
    with pytest.raises(ValidationError):
        hdns._validate_mx("mail.example.com")


def test_validate_mx_rejects_non_numeric_priority():
    with pytest.raises(ValidationError):
        hdns._validate_mx("high mail.example.com")


def test_validate_txt_quotes_unquoted_value():
    assert hdns._validate_txt("v=spf1 -all") == '"v=spf1 -all"'


def test_validate_txt_leaves_already_quoted_value():
    assert hdns._validate_txt('"already quoted"') == '"already quoted"'


def test_validate_txt_escapes_embedded_quotes():
    assert hdns._validate_txt('say "hi"') == '"say \\"hi\\""'


def test_validate_txt_rejects_too_long():
    with pytest.raises(ValidationError):
        hdns._validate_txt("x" * 3000)


def test_cname_validator_normalizes_trailing_dot():
    assert hdns.RECORD_VALUE_VALIDATORS["CNAME"]("target.example.com") == "target.example.com."
    assert hdns.RECORD_VALUE_VALIDATORS["CNAME"]("target.example.com.") == "target.example.com."
