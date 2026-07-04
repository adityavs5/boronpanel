from api.main import ip_allowed


def test_empty_whitelist_allows_everything():
    assert ip_allowed("203.0.113.5", []) is True
    assert ip_allowed(None, []) is True


def test_exact_ip_match_allowed():
    assert ip_allowed("203.0.113.5", ["203.0.113.5"]) is True


def test_non_matching_ip_rejected():
    assert ip_allowed("203.0.113.99", ["203.0.113.5"]) is False


def test_cidr_match_allowed():
    assert ip_allowed("203.0.113.42", ["203.0.113.0/24"]) is True


def test_cidr_non_match_rejected():
    assert ip_allowed("198.51.100.1", ["203.0.113.0/24"]) is False


def test_no_client_host_rejected_when_whitelist_nonempty():
    assert ip_allowed(None, ["203.0.113.5"]) is False


def test_malformed_client_host_rejected():
    assert ip_allowed("not-an-ip", ["203.0.113.5"]) is False


def test_malformed_whitelist_entry_skipped_not_fatal():
    # A bad entry in the list must not crash the check or accidentally
    # allow everything -- it's just ignored, other entries still apply.
    assert ip_allowed("203.0.113.5", ["garbage", "203.0.113.5"]) is True
    assert ip_allowed("203.0.113.99", ["garbage"]) is False


def test_ipv6_exact_match():
    assert ip_allowed("2001:db8::1", ["2001:db8::1"]) is True


def test_multiple_entries_any_match_allows():
    assert ip_allowed("198.51.100.9", ["203.0.113.5", "198.51.100.9", "192.0.2.0/24"]) is True
