from copy import deepcopy

import pytest

from daemon import dns_editor as editor, cloudflare_accounts, dnsprovider, mail_dns, powerdns
from shared.config import settings
from shared.db import write_session
from shared.models import Account, CloudflareZone, DnsZone, Domain
from shared.validation import ValidationError
from tests.test_snapshot_cloudflare_recovery import provider  # noqa: F401

ZONE = 'alpha.test'


@pytest.fixture
def managed(isolated_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'snapshot_private_dir', str(tmp_path / 'private'))
    with write_session() as session:
        account = Account(username='demo1', uid=5001, gid=5001, status='active')
        session.add(account)
        session.flush()
        session.add(Domain(account_id=account.id, domain=ZONE, kind='primary', docroot='/home/demo1/public_html'))
        session.add(DnsZone(account_id=account.id, zone=ZONE))
    return account.id


@pytest.fixture
def local(managed, monkeypatch):
    data = {'records': [
        {'name': ZONE+'.', 'type': 'SOA', 'ttl': 3600, 'records': [{'content': 'ns.alpha.test. hostmaster.alpha.test. 1 3600 600 86400 300', 'disabled': False}]},
        {'name': ZONE+'.', 'type': 'NS', 'ttl': 3600, 'records': [{'content': 'ns.alpha.test.', 'disabled': False}]},
        {'name': ZONE+'.', 'type': 'A', 'ttl': 3600, 'records': [{'content': '192.0.2.1', 'disabled': False}, {'content': '192.0.2.2', 'disabled': False}]},
        {'name': '_verify.'+ZONE+'.', 'type': 'TXT', 'ttl': 3600, 'records': [{'content': '"'+'x'*400+'"', 'disabled': False}]},
        {'name': 'disabled.'+ZONE+'.', 'type': 'A', 'ttl': 300, 'records': [{'content': '192.0.2.5', 'disabled': True}]},
    ], 'writes': [], 'cluster': [], 'reject': False}
    monkeypatch.setattr(powerdns, 'get_zone', lambda zone: {'rrsets': deepcopy(data['records'])})
    def apply(zone, changes):
        data['writes'].append(deepcopy(changes))
        if data['reject']:
            raise powerdns.PowerDnsError(422, 'fixture rejection')
        current = {(r['name'], r['type']): r for r in deepcopy(data['records'])}
        for row in changes:
            key = (row['name'], row['type'])
            if row['changetype'] == 'DELETE':
                current.pop(key, None)
            else:
                current[key] = {k: v for k, v in row.items() if k != 'changetype'}
        data['records'] = list(current.values())
    monkeypatch.setattr(powerdns, 'apply_rrset_changes', apply)
    monkeypatch.setattr(dnsprovider, '_cluster_notify', lambda zone: data['cluster'].append(zone))
    return data


@pytest.mark.parametrize('text', [
    '$INCLUDE /etc/shadow', '$GENERATE 1-99999 host$ A 192.0.2.1',
    'outside.test. 3600 IN A 192.0.2.1', '$ORIGIN outside.test.\nwww 3600 IN A 192.0.2.1',
    '@ 3600 IN CNAME elsewhere.test.', 'www 30 IN A 192.0.2.1',
    'www IN CNAME target.test.\nwww IN A 192.0.2.1', '@ IN NS changed.test.',
])
def test_unsafe_raw_input_never_writes(local, text):
    before = editor.get({'domain': ZONE})
    with pytest.raises(ValidationError):
        editor.apply({'domain': ZONE, 'text': text, 'fingerprint': before['fingerprint'], 'confirmation': ZONE})
    assert local['writes'] == []


def test_export_roundtrip_preserves_large_txt_and_readonly_records(local):
    before = deepcopy(local['records'])
    view = editor.get({'domain': ZONE})
    assert 'disabled.'+ZONE not in view['text']
    assert ' IN NS ' not in view['text']
    assert editor.preview({'domain': ZONE, **view})['changes'] == []
    assert editor.apply({'domain': ZONE, **view, 'confirmation': ZONE})['changed'] == 0
    assert local['records'] == before and local['writes'] == []


def test_stale_or_unconfirmed_edit_never_writes(local):
    view = editor.get({'domain': ZONE})
    with pytest.raises(ValidationError, match='Type the full'):
        editor.apply({'domain': ZONE, **view, 'confirmation': 'other.test'})
    local['records'][2]['records'].append({'content': '192.0.2.3', 'disabled': False})
    with pytest.raises(ValidationError, match='changed'):
        editor.apply({'domain': ZONE, **view, 'confirmation': ZONE})
    assert local['writes'] == []


def test_atomic_local_edit_keeps_authority_disabled_records_and_prior_state(local, tmp_path):
    view = editor.get({'domain': ZONE})
    text = view['text'].replace('192.0.2.2', '192.0.2.20') + f'_sip._tcp.{ZONE}. 600 IN SRV 0 1 443 {ZONE}.\n'
    preview = editor.preview({'domain': ZONE, 'text': text, 'fingerprint': view['fingerprint']})
    assert len(preview['changes']) == 2
    result = editor.apply({'domain': ZONE, 'text': text, 'fingerprint': view['fingerprint'], 'confirmation': ZONE})
    assert result['changed'] == 2 and len(local['writes']) == 1
    records = {(r['name'], r['type']): r for r in local['records']}
    assert records[(ZONE+'.', 'NS')]['records'][0]['content'] == 'ns.alpha.test.'
    assert records[('disabled.'+ZONE+'.', 'A')]['records'][0]['disabled'] is True
    assert [r['content'] for r in records[(ZONE+'.', 'A')]['records']] == ['192.0.2.1', '192.0.2.20']
    prior = next((tmp_path/'private/dns-editor/account-1').glob('*.json'))
    assert prior.stat().st_mode & 0o777 == 0o600
    assert local['cluster'] == [ZONE]


def test_provider_rejects_whole_local_change_without_partial_state(local):
    before = deepcopy(local['records']);view = editor.get({'domain': ZONE});local['reject'] = True
    with pytest.raises(powerdns.PowerDnsError):
        editor.apply({'domain': ZONE, **view, 'text': view['text'].replace('192.0.2.1', '192.0.2.30'), 'confirmation': ZONE})
    assert local['records'] == before and local['cluster'] == []


def test_hosting_template_preserves_ips_and_verification_txt_while_adding_spf(local, monkeypatch):
    from daemon import ipmanager
    monkeypatch.setattr(ipmanager, 'address_for_account', lambda ident: '192.0.2.99')
    local['records'].append({'name': ZONE+'.', 'type': 'TXT', 'ttl': 300, 'records': [{'content': '"verification-token"', 'disabled': False}]})
    monkeypatch.setattr(mail_dns, 'default_records', lambda zone: [mail_dns.DesiredRecord('spf', '@', 'TXT', ('"v=spf1 mx ~all"',))])
    result = editor.template({'domain': ZONE, 'template': 'hosting'})
    rows = {(r['name'], r['type']): r for r in editor._parse(ZONE, result['text'])}
    assert rows[(ZONE, 'A')]['values'] == ['192.0.2.1', '192.0.2.2']
    assert rows[(ZONE, 'TXT')]['values'] == ['"v=spf1 mx ~all"', '"verification-token"']
    assert result['conflicts'] and local['writes'] == []


@pytest.fixture
def cf(managed, provider, monkeypatch):
    with write_session() as session:
        session.add(CloudflareZone(account_id=managed, zone=ZONE, cf_zone_id='a'*32, status='active', name_servers=[], cf_account_id=None))
    monkeypatch.setattr(cloudflare_accounts, 'token_for_id', lambda ident: 'qa-pooled-token')
    provider['records'][0].update(proxied=True, ttl=1, comment='Keep this comment', tags=['environment:qa'])
    provider['records'].append({'id': f'{99:032x}', 'name': 'managed.'+ZONE, 'type': 'TXT', 'content': 'provider-managed', 'ttl': 300, 'locked': True})
    return provider


@pytest.mark.parametrize('ambiguous_response', [False, True])
def test_cloudflare_edit_preserves_proxy_metadata_and_managed_records(cf, ambiguous_response):
    cf['fail_after_commit'] = ambiguous_response
    view = editor.get({'domain': ZONE})
    assert 'managed.'+ZONE not in view['text']
    result = editor.apply({'domain': ZONE, **view, 'text': view['text'].replace('192.0.2.1', '192.0.2.20'), 'confirmation': ZONE})
    assert result['changed'] == 1 and len(cf['calls']) == 1
    record = next(r for r in cf['records'] if r['type'] == 'A')
    assert record['content'] == '192.0.2.20' and record['proxied'] is True and record['ttl'] == 1
    assert record['comment'] == 'Keep this comment' and record['tags'] == ['environment:qa']
    assert next(r for r in cf['records'] if r['name'].startswith('managed.'))['locked'] is True


def test_cloudflare_raw_edit_refuses_multiple_provider_batches(cf):
    view = editor.get({'domain': ZONE})
    text = view['text'] + ''.join(f'host{i}.{ZONE}. 300 IN A 192.0.2.100\n' for i in range(201))
    with pytest.raises(ValidationError, match='200 changes'):
        editor.apply({'domain': ZONE, **view, 'text': text, 'confirmation': ZONE})
    assert cf['calls'] == []


def test_cloudflare_unedited_auto_ttl_is_preserved_during_other_rrset_edits(cf):
    cf['records'].append({'id':f'{98:032x}', 'name':'verify.'+ZONE, 'type':'TXT',
                          'content':'keep verification', 'ttl':1, 'proxied':False})
    view=editor.get({'domain':ZONE})
    editor.apply({'domain':ZONE,**view,'text':view['text'].replace('192.0.2.1','192.0.2.20'),'confirmation':ZONE})
    record=next(r for r in cf['records'] if r['name']=='verify.'+ZONE)
    assert record['ttl']==1 and record['content']=='keep verification'
    assert len(cf['calls'])==1


def test_customer_raw_editor_does_not_call_provider_for_foreign_zone(managed, monkeypatch):
    from fastapi import HTTPException
    from api.routers import dns as router
    from api.security import Identity
    with write_session() as session:
        other = Account(username='other', status='active')
        session.add(other);session.flush()
        session.add(Domain(account_id=other.id, domain='foreign.test', kind='primary', docroot='/home/other/public_html'))
        session.add(DnsZone(account_id=other.id, zone='foreign.test'))
    calls = []
    monkeypatch.setattr(router, 'call_daemon', lambda *args, **kwargs: calls.append((args, kwargs)))
    identity = Identity(1, 'demo1', 'customer', managed, 'session')
    with pytest.raises(HTTPException) as denied:
        router.advanced_zone('foreign.test', identity)
    assert denied.value.status_code == 403 and calls == []


def test_local_external_edit_between_validation_and_capture_never_writes(local, monkeypatch):
    view = editor.get({'domain': ZONE})
    original = powerdns.get_zone
    calls = 0
    def raced(zone):
        nonlocal calls
        calls += 1
        if calls == 2:
            local['records'][2]['records'].append({'content': '192.0.2.44', 'disabled': False})
        return original(zone)
    monkeypatch.setattr(powerdns, 'get_zone', raced)
    with pytest.raises(ValidationError, match='changed'):
        editor.apply({'domain': ZONE, **view, 'text': view['text'].replace('192.0.2.1', '192.0.2.20'), 'confirmation': ZONE})
    assert calls == 2 and local['writes'] == []
