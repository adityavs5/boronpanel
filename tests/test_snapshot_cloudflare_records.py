from copy import deepcopy

import pytest

from daemon.snapshot_cloudflare_records import normalize
from shared.validation import ValidationError


def record(**changes):
    return dict(name='www.alpha.test', type='A', content='192.0.2.7', ttl=1, **changes)


def test_native_proxy_settings_comments_tags_and_current_ids():
    raw=record(id='a'*32, proxied=True, comment='QA', tags=['owner:qa'],
               settings={'ipv4_only':True}, private_routing=True, created_on='read-only')
    before=deepcopy(raw)
    saved=normalize('alpha.test',raw)
    assert saved['ttl']==1 and saved['proxied'] and saved['private_routing']
    assert saved['settings']=={'ipv4_only':True} and saved['tags']==['owner:qa']
    assert saved['comment']=='QA' and 'id' not in saved and 'created_on' not in saved
    assert normalize('alpha.test',raw,require_id=True)['id']=='a'*32
    assert raw==before


@pytest.mark.parametrize('rtype,content,extra', [
    ('A','192.0.2.1',{}), ('AAAA','2001:db8::1',{}),
    ('CNAME','target.example.',{'settings':{'flatten_cname':True}}),
    ('CNAME','target.example.',{}), ('MX','mail.example.',{'priority':10}),
    ('NS','ns.example.',{}), ('PTR','host.example.',{}),
    ('TXT','"literal quotes" and spaces',{}), ('TXT','',{}),
    ('CAA','0 issue "ca.example"',{'data':{'flags':0,'tag':'issue','value':'ca.example'}}),
    ('SRV','10 5 443 target.example.',{'data':{'priority':10,'weight':5,'port':443,'target':'target.example.'}}),
])
def test_supported_native_record_types(rtype,content,extra):
    raw={'name':'alpha.test' if rtype=='CNAME' else '_service._tcp.alpha.test',
         'type':rtype,'ttl':300,'content':content,**extra}
    result=normalize('alpha.test',raw)
    assert result['type']==rtype and result['ttl']==300
    if rtype=='TXT': assert result['content']==content
    if rtype in ('CAA','SRV'): assert 'data' in result and 'content' not in result


@pytest.mark.parametrize('changes', [
    {'name':'alpha.test.attacker.test'}, {'name':'other.test'},
    {'ttl':True}, {'ttl':2}, {'ttl':86401}, {'proxied':'true'},
    {'content':'not-an-ip'}, {'content':None}, {'comment':False},
    {'settings':{'unknown':True}}, {'settings':{'ipv4_only':1}},
    {'tags':'owner:qa'}, {'tags':[None]}, {'private_routing':1},
    {'locked':True}, {'meta':{'managed_by_apps':True}},
    {'unknown_field':'must not silently discard'},
    {'type':'CNAME','content':'www.alpha.test'},
    {'type':'CAA','data':{'flags':0,'tag':'issue'}},
    {'type':'SRV','data':{'priority':True,'weight':5,'port':443,'target':'target.test'}},
    {'type':'TXT','content':'text','proxied':True},
    {'type':'MX','content':'mail.test','priority':-1},
])
def test_invalid_or_unsupported_saved_records_fail_before_writes(changes):
    raw=record();raw.update(changes)
    with pytest.raises(ValidationError): normalize('alpha.test',raw)


def test_current_ids_are_required_only_for_current_records():
    raw=record(id='saved-legacy-id')
    assert 'id' not in normalize('alpha.test',raw)
    with pytest.raises(ValidationError): normalize('alpha.test',raw,require_id=True)


def test_proxied_record_does_not_silently_rewrite_ttl():
    raw=record(proxied=True);raw['ttl']=300
    with pytest.raises(ValidationError,match='automatic TTL'): normalize('alpha.test',raw)


def test_conflicting_structured_content_is_rejected():
    raw={'name':'alpha.test','type':'CAA','ttl':300,'content':'0 issue "different.example"',
         'data':{'flags':0,'tag':'issue','value':'ca.example'}}
    with pytest.raises(ValidationError,match='invalid for its DNS type'): normalize('alpha.test',raw)


@pytest.mark.parametrize('rtype,data,content', [
    ('HTTPS',{'priority':1,'target':'.','value':'alpn="h2,h3" port=443'},'1 . alpn="h2,h3" port=443'),
    ('SVCB',{'priority':0,'target':'target.example.','value':''},'0 target.example.'),
    ('TLSA',{'usage':3,'selector':1,'matching_type':1,'certificate':'AB'*32},'3 1 1 '+'AB'*32),
    ('DS',{'key_tag':12345,'algorithm':13,'digest_type':2,'digest':'AB'*32},'12345 13 2 '+'AB'*32),
])
def test_additional_structured_types_round_trip(rtype,data,content):
    raw={'name':'_service.alpha.test','type':rtype,'ttl':300,'data':data,'content':content}
    result=normalize('alpha.test',raw)
    assert result['type']==rtype and 'content' not in result
    assert normalize('alpha.test',result)==result


def test_https_parameter_order_has_one_canonical_representation():
    raw={'name':'alpha.test','type':'HTTPS','ttl':300,'data':{'priority':1,'target':'.','value':'port=443 alpn="h2"'}}
    other=deepcopy(raw);other['data']['value']='alpn="h2" port=443'
    assert normalize('alpha.test',raw)==normalize('alpha.test',other)


@pytest.mark.parametrize('rtype,data', [
    ('TLSA',{'usage':True,'selector':1,'matching_type':1,'certificate':'ab'}),
    ('TLSA',{'usage':3,'selector':1,'matching_type':1,'certificate':'zz'}),
    ('DS',{'key_tag':65536,'algorithm':13,'digest_type':2,'digest':'ab'*32}),
    ('HTTPS',{'priority':1,'target':'.','value':'port=not-a-port'}),
])
def test_invalid_additional_structured_data(rtype,data):
    with pytest.raises(ValidationError): normalize('alpha.test',{'name':'alpha.test','type':rtype,'ttl':300,'data':data})
