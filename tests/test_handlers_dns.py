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


def test_ptr_validator_normalizes_trailing_dot():
    assert hdns.RECORD_VALUE_VALIDATORS["PTR"]("host.example.com") == "host.example.com."


def test_validate_srv_accepts_good():
    assert hdns._validate_srv("10 20 5060 sip.example.com") == "10 20 5060 sip.example.com."


def test_validate_srv_rejects_wrong_field_count():
    with pytest.raises(ValidationError):
        hdns._validate_srv("10 20 sip.example.com")


def test_validate_srv_rejects_non_numeric_priority():
    with pytest.raises(ValidationError):
        hdns._validate_srv("high 20 5060 sip.example.com")


def test_validate_srv_rejects_bad_port():
    with pytest.raises(ValidationError):
        hdns._validate_srv("10 20 99999 sip.example.com")


def test_validate_caa_accepts_good():
    assert hdns._validate_caa("0 issue letsencrypt.org") == '0 issue "letsencrypt.org"'


def test_validate_caa_accepts_already_quoted():
    assert hdns._validate_caa('0 issue "letsencrypt.org"') == '0 issue "letsencrypt.org"'


def test_validate_caa_rejects_bad_tag():
    with pytest.raises(ValidationError):
        hdns._validate_caa("0 bogus letsencrypt.org")


def test_validate_caa_rejects_bad_flags():
    with pytest.raises(ValidationError):
        hdns._validate_caa("300 issue letsencrypt.org")


def test_validate_record_type_accepts_new_types():
    from shared.validation import validate_record_type

    for rtype in ("PTR", "SRV", "CAA"):
        assert validate_record_type(rtype) == rtype


def test_create_zone_rejects_unowned_domain_cleanly(isolated_db):
    """Security audit finding F13: DnsZone.account_id is NOT NULL, but
    create_zone previously reached the insert anyway when no account
    could be resolved, raising a raw IntegrityError instead of a clean
    ValidationError."""
    with pytest.raises(ValidationError):
        hdns.create_zone({"domain": "unowned-example.com"})


def test_dns_add_preserves_existing_rrset(monkeypatch):
    monkeypatch.setattr(hdns.dnsprovider, 'list_records', lambda zone: [{'name': 'example.com.', 'type': 'A', 'values': ['192.0.2.1']}])
    calls = []
    monkeypatch.setattr(hdns.dnsprovider, 'upsert_record', lambda *a, **kw: calls.append((a, kw)))
    result = hdns.set_record({'domain': 'example.com', 'type': 'A', 'values': ['192.0.2.2'], 'mode': 'add'})
    assert result['values'] == ['192.0.2.1', '192.0.2.2']
    assert calls[0][0][3] == result['values']
    replaced = hdns.set_record({'domain': 'example.com', 'type': 'A', 'values': ['192.0.2.3'], 'mode': 'replace'})
    assert replaced['values'] == ['192.0.2.3']


@pytest.mark.parametrize('name', ['bad/name', 'bad name', 'outside.test.', 'a..b', '*.*'])
def test_dns_invalid_owner_names_are_actionable(name):
    with pytest.raises(ValidationError):
        hdns.set_record({'domain': 'example.com', 'subdomain': name, 'type': 'A', 'values': ['192.0.2.2']})


@pytest.mark.parametrize('name,existing', [('@', []), ('www', [{'name': 'www.example.com', 'type': 'A', 'values': ['192.0.2.1']}])])
def test_dns_cname_conflicts_rejected_before_provider_write(monkeypatch, name, existing):
    monkeypatch.setattr(hdns.dnsprovider, 'list_records', lambda zone: existing)
    calls = []
    monkeypatch.setattr(hdns.dnsprovider, 'upsert_record', lambda *a, **kw: calls.append(a))
    with pytest.raises(ValidationError, match='CNAME'):
        hdns.set_record({'domain': 'example.com', 'subdomain': name, 'type': 'CNAME', 'values': ['target.example.com']})
    assert calls == []
