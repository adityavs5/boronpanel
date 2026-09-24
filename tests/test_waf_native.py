"""Optional native ModSecurity acceptance against the actual rendered rule chain."""
import ctypes as c
import os
import pytest
from daemon import ols

LIBRARY=os.environ.get('BORON_MODSECURITY_LIBRARY')
pytestmark=pytest.mark.skipif(not LIBRARY, reason='requires an isolated native ModSecurity library')


class Intervention(c.Structure):
    _fields_=[('status',c.c_int),('pause',c.c_int),('url',c.c_void_p),('log',c.c_void_p),('disruptive',c.c_int)]


@pytest.mark.parametrize('host,value,blocked', [('shop.example.com','badbot',True),
    ('SHOP.EXAMPLE.COM','badbot',True), ('shop.example.com:443','badbot',True),
    ('shop.example.com.:80','badbot',True), ('shop.example.com','normal',False),
    ('peer.example.com','badbot',False), ('shop.example.com.attacker.example','badbot',False)])
def test_rendered_custom_rule_enforces_host_and_payload(host,value,blocked):
    lib=c.CDLL(LIBRARY)
    signatures={
        'msc_init':([],c.c_void_p), 'msc_cleanup':([c.c_void_p],None),
        'msc_create_rules_set':([],c.c_void_p), 'msc_rules_cleanup':([c.c_void_p],None),
        'msc_rules_add':([c.c_void_p,c.c_char_p,c.POINTER(c.c_char_p)],c.c_int),
        'msc_new_transaction':([c.c_void_p,c.c_void_p,c.c_void_p],c.c_void_p),
        'msc_transaction_cleanup':([c.c_void_p],None),
        'msc_process_connection':([c.c_void_p,c.c_char_p,c.c_int,c.c_char_p,c.c_int],c.c_int),
        'msc_process_uri':([c.c_void_p,c.c_char_p,c.c_char_p,c.c_char_p],c.c_int),
        'msc_add_request_header':([c.c_void_p,c.c_char_p,c.c_char_p],c.c_int),
        'msc_process_request_headers':([c.c_void_p],c.c_int),
        'msc_process_request_body':([c.c_void_p],c.c_int),
        'msc_intervention':([c.c_void_p,c.POINTER(Intervention)],c.c_int),
    }
    for name,(args,result) in signatures.items():
        getattr(lib,name).argtypes=args;getattr(lib,name).restype=result
    context={'waf_enabled':True,'waf_audit_log':'/tmp/unused','waf_rules_file':'/tmp/unused',
        'waf_domain_overrides':[], 'waf_custom_rules':[{'id':7,'domain':'shop.example.com','target':'ARGS','pattern':'badbot'}]}
    rendered=ols.render_httpd_config([],[],waf=context)
    rules='SecRuleEngine On\nSecAuditEngine Off\n'+ '\n'.join(line for line in rendered.splitlines() if line.startswith('SecRule '))
    engine=lib.msc_init();rule_set=lib.msc_create_rules_set();transaction=None
    intervention=Intervention(status=200)
    try:
        error=c.c_char_p()
        assert lib.msc_rules_add(rule_set,rules.encode(),c.byref(error))>0, error.value
        transaction=lib.msc_new_transaction(engine,rule_set,None)
        lib.msc_process_connection(transaction,b'127.0.0.1',12345,b'127.0.0.1',80)
        lib.msc_process_uri(transaction,('/?q='+value).encode(),b'GET',b'1.1')
        lib.msc_add_request_header(transaction,b'Host',host.encode())
        lib.msc_process_request_headers(transaction)
        lib.msc_process_request_body(transaction)
        lib.msc_intervention(transaction,c.byref(intervention))
        assert (intervention.status==403 and bool(intervention.disruptive)) is blocked
    finally:
        libc=c.CDLL(None);libc.free.argtypes=[c.c_void_p]
        if intervention.url:libc.free(intervention.url)
        if intervention.log:libc.free(intervention.log)
        if transaction:lib.msc_transaction_cleanup(transaction)
        lib.msc_rules_cleanup(rule_set);lib.msc_cleanup(engine)
