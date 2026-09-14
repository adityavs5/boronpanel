import json

import httpx
import pytest

from daemon import cloudflare

ZONE = 'a' * 32
RECORD = 'b' * 32


def test_recovery_batch_preserves_native_fields_and_bound_token(monkeypatch):
    operations = {'deletes': [{'id': RECORD}], 'posts': [{
        'type': 'A', 'name': 'www.alpha.test', 'content': '192.0.2.7', 'ttl': 1,
        'proxied': True, 'comment': 'Recovery test', 'tags': ['owner:qa'],
        'settings': {'ipv4_only': True}, 'private_routing': False,
    }]}
    calls = []
    def respond(request):
        calls.append(request)
        assert request.url.path == f'/client/v4/zones/{ZONE}/dns_records/batch'
        assert request.headers['authorization'] == 'Bearer fixture-token'
        assert json.loads(request.content) == operations
        return httpx.Response(200, json={'success': True, 'result': {'posts': [{'id': 'c' * 32}]}})
    monkeypatch.setattr(cloudflare, '_transport', httpx.MockTransport(respond))
    with cloudflare.use_token('fixture-token'):
        result = cloudflare.apply_record_batch('alpha.test', operations, zone_id=ZONE)
    assert len(calls) == 1 and result['posts'][0]['id'] == 'c' * 32
    assert cloudflare._token_var.get() is None


@pytest.mark.parametrize('failure', ['timeout', '429', '500', '503', 'invalid_json', 'false_success', 'bad_result'])
def test_ambiguous_recovery_batch_is_never_retried(monkeypatch, failure):
    calls = []
    def respond(request):
        calls.append(request)
        if failure == 'timeout': raise httpx.ReadTimeout('private transport detail', request=request)
        if failure.isdigit(): return httpx.Response(int(failure), text='private response detail')
        if failure == 'invalid_json': return httpx.Response(200, text='not-json')
        return httpx.Response(200, json={'success': failure != 'false_success', 'result': []})
    monkeypatch.setattr(cloudflare, '_transport', httpx.MockTransport(respond))
    with pytest.raises(cloudflare.CloudflareError, match='inspect current records') as caught:
        cloudflare.apply_record_batch('alpha.test', {'deletes': [{'id': RECORD}]}, zone_id=ZONE)
    assert len(calls) == 1
    assert 'private' not in str(caught.value)


@pytest.mark.parametrize('operations,zone', [
    ({}, ZONE), ({'deletes': []}, ZONE), ({'unknown': [{}]}, ZONE),
    ({'deletes': [{'id': '../other'}]}, ZONE),
    ({'posts': [{'id': RECORD}]}, ZONE),
    ({'posts': [{}] * 201}, ZONE),
    ({'deletes': [{'id': RECORD}]}, '../other'),
])
def test_invalid_recovery_batch_rejected_before_transport(monkeypatch, operations, zone):
    monkeypatch.setattr(cloudflare, '_client', lambda: pytest.fail('No transport permitted'))
    with pytest.raises(cloudflare.CloudflareError):
        cloudflare.apply_record_batch('alpha.test', operations, zone_id=zone)
