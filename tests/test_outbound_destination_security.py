import pytest

from shared import validation as v


@pytest.mark.parametrize('address', ['100.64.0.1', '100.100.100.200', '100.127.255.254'])
def test_shared_space_denied_for_webhook_and_imap(address, monkeypatch):
    with pytest.raises(v.ValidationError):
        v.validate_webhook_url('https://' + address + '/hook')
    with pytest.raises(v.ValidationError):
        v.resolve_public_imap_source(address)
    monkeypatch.setattr(v.socket, 'getaddrinfo', lambda *args: [(0, 0, 0, '', (address, 0))])
    with pytest.raises(v.ValidationError):
        v.resolve_public_imap_source('migration.example')


@pytest.mark.parametrize('url', ['https://user:secret@example.com/hook', 'https://example.com:0/',
    'https://example.com:wrong/', 'https://example.com/\x00', 'https://:/hook'])
def test_invalid_webhook_destination_rejected_without_secret_echo(url):
    with pytest.raises(v.ValidationError) as caught:
        v.validate_webhook_url(url)
    assert "secret" not in str(caught.value)
    assert url not in str(caught.value)
