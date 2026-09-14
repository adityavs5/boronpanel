from copy import deepcopy

import pytest

from daemon.snapshot_cloudflare_records import normalize
from shared.validation import ValidationError


EXAMPLES = [
    ('CAA','0 issue "ca.example"'),
    ('SRV','10 5 443 target.example'),
    ('HTTPS','1 target.example alpn="h2" port=443'),
    ('SVCB','0 target.example'),
    ('TLSA','3 1 1 '+'ab'*32),
    ('SMIMEA','3 1 1 '+'ab'*32),
    ('SSHFP','1 2 '+'ab'*32),
    ('DS','12345 13 2 '+'ab'*32),
    ('CERT','1 12345 8 YWJjZA=='),
    ('URI','10 5 "https://example.test/path?q=one"'),
    ('NAPTR','10 20 "s" "SIP+D2U" "" _sip._udp.example.test'),
    ('LOC','37 47 0 N 122 23 0 W 10m 1m 10000m 10m'),
]


@pytest.mark.parametrize('rtype,content',EXAMPLES)
def test_content_only_and_native_data_have_same_round_trip(rtype,content):
    old={'name':'service.alpha.test','type':rtype,'ttl':300,'content':content}
    normalized=normalize('alpha.test',old)
    assert normalized['data'] and 'content' not in normalized
    assert normalize('alpha.test',normalized)==normalized
    assert normalize('alpha.test',{**normalized,'content':content})==normalized
    assert old['content']==content


@pytest.mark.parametrize('rtype,content',EXAMPLES)
def test_inconsistent_native_and_legacy_text_is_not_silently_accepted(rtype,content):
    normalized=normalize('alpha.test',{'name':'service.alpha.test','type':rtype,'ttl':300,'content':content})
    with pytest.raises(ValidationError): normalize('alpha.test',{**normalized,'content':'invalid record content'})


@pytest.mark.parametrize('rtype,data', [
    ('CERT',{'type':1,'key_tag':2,'algorithm':8,'certificate':'%%%'}),
    ('URI',{'target':'https://example.test','weight':True}),
    ('NAPTR',{'order':1,'preference':2,'flags':0,'service':'','regex':'','replacement':'.'}),
    ('SSHFP',{'algorithm':1,'type':2,'fingerprint':'123'}),
])
def test_malformed_additional_native_data_rejected(rtype,data):
    with pytest.raises(ValidationError): normalize('alpha.test',{'name':'alpha.test','type':rtype,'ttl':300,'data':data,'priority':1})


@pytest.mark.parametrize('field,value',[('lat_seconds',float('nan')),('altitude',float('inf')),('lat_degrees',91),('lat_degrees',90),('long_degrees',180),('long_minutes',True),('lat_direction','X')])
def test_invalid_loc_measurements_rejected(field,value):
    normalized=normalize('alpha.test',{'name':'alpha.test','type':'LOC','ttl':300,'content':EXAMPLES[-1][1]})
    malformed=deepcopy(normalized);malformed['data'][field]=value
    with pytest.raises(ValidationError): normalize('alpha.test',malformed)
