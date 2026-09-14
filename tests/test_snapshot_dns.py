import pytest
from sqlalchemy import select
from daemon import snapshot_dns, cloudflare, cloudflare_accounts, powerdns
from shared.db import write_session
from shared.models import Account, DnsZone, CloudflareZone
from shared.validation import ValidationError


@pytest.fixture
def dns_account(isolated_db):
    with write_session() as session:
        account=Account(username='alpha',status='active')
        foreign=Account(username='bravo',status='active')
        session.add_all([account,foreign]);session.flush()
        session.add_all([DnsZone(account_id=account.id,zone='alpha.test'),DnsZone(account_id=foreign.id,zone='bravo.test')])
    return account,foreign


def test_local_dns_capture_preserves_disabled_records_comments_and_soa(dns_account, monkeypatch):
    account,_=dns_account
    records=[{'name':'alpha.test.','type':'SOA','ttl':3600,'records':[{'content':'ns.alpha.test. host.alpha.test. 1 2 3 4 5','disabled':False}]},
             {'name':'www.alpha.test.','type':'A','ttl':600,'records':[{'content':'192.0.2.1','disabled':True}],
              'comments':[{'content':'temporarily disabled','account':'operator','modified_at':123}]}]
    calls=[]
    def read(zone):
        calls.append(zone);return dict(name=zone+'.',rrsets=records)
    monkeypatch.setattr(powerdns,'get_zone',read)
    result=snapshot_dns.capture(account)
    assert calls==['alpha.test']
    assert result['zones'][0]['records']==records
    records[1]['records'][0]['disabled']=False
    assert result['zones'][0]['records'][1]['records'][0]['disabled'] is True
    assert 'bravo' not in repr(result)
    assert snapshot_dns.legacy_zones(result)==[dict(zone='alpha.test',records=[dict(name='www.alpha.test',type='A',ttl=600,values=['192.0.2.1'])])]


def test_cloudflare_capture_preserves_each_record_and_uses_bound_token(dns_account, monkeypatch):
    account,_=dns_account
    with write_session() as session:
        session.add(CloudflareZone(account_id=account.id,zone='alpha.test',cf_zone_id='zone-alpha',status='active'))
    records=[{'id':'one','name':'www.alpha.test','type':'A','content':'192.0.2.1','ttl':1,'proxied':True,'settings':{'ipv4_only':True}},
             {'id':'two','name':'www.alpha.test','type':'A','content':'192.0.2.2','ttl':600,'proxied':False,'tags':['qa']}]
    monkeypatch.setattr(cloudflare_accounts,'token_for_id',lambda ident:'test-transport-token')
    def export(zone,zone_id=None):
        assert zone=='alpha.test' and zone_id=='zone-alpha'
        assert cloudflare._token_var.get()=='test-transport-token'
        return records
    monkeypatch.setattr(cloudflare,'export_record_documents',export)
    result=snapshot_dns.capture(account)
    assert result['zones'][0]['records']==records
    assert result['zones'][0]['provider']=='cloudflare'
    assert 'test-transport-token' not in repr(result)
    assert cloudflare._token_var.get() is None
    legacy=snapshot_dns.legacy_zones(result)[0]['records']
    assert len(legacy)==1 and legacy[0]['ttl']==cloudflare.DEFAULT_TTL and legacy[0]['proxied'] is True
    assert legacy[0]['values']==['192.0.2.1','192.0.2.2']


@pytest.mark.parametrize('change', ['ownership','provider'])
def test_dns_capture_rejects_scope_changes_during_provider_read(dns_account, monkeypatch, change):
    account,foreign=dns_account
    def read(zone):
        with write_session() as session:
            if change=='ownership':
                session.scalar(select(DnsZone).where(DnsZone.zone==zone)).account_id=foreign.id
            else:
                session.add(CloudflareZone(account_id=account.id,zone=zone,cf_zone_id='new-zone',status='active'))
        return dict(name=zone+'.',rrsets=[])
    monkeypatch.setattr(powerdns,'get_zone',read)
    with pytest.raises(ValidationError,match='changed'):
        snapshot_dns.capture(account)


def test_dns_capture_rejects_foreign_cloudflare_registration(dns_account):
    account,foreign=dns_account
    with write_session() as session:
        session.add(CloudflareZone(account_id=foreign.id,zone='alpha.test',cf_zone_id='foreign',status='active'))
    with pytest.raises(ValidationError,match='another account'):
        snapshot_dns.capture(account)


def test_cloudflare_export_keeps_native_documents(monkeypatch):
    records=[{'id':'record','ttl':1,'proxied':True,'data':{'priority':10}}]
    calls=[]
    def paged(path):
        calls.append(path);yield from records
    monkeypatch.setattr(cloudflare,'_paged',paged)
    assert cloudflare.export_record_documents('alpha.test',zone_id='zone-alpha')==records
    assert calls==['/zones/zone-alpha/dns_records']


from tests.test_snapshot_jobs import environment, make_destination


def test_native_dns_configuration_survives_encrypted_backup(environment, monkeypatch):
    import json
    from daemon import cron, dnsprovider, snapshot_jobs as jobs, snapshot_storage as storage
    from shared.models import SnapshotRun, SnapshotDestination
    root,_=environment
    with write_session() as session:
        account=session.scalar(select(Account).where(Account.username=='alpha'))
        session.add(DnsZone(account_id=account.id,zone='alpha.test'))
    records=[dict(name='www.alpha.test.',type='A',ttl=600,
                  records=[dict(content='192.0.2.15',disabled=True)],comments=[])]
    monkeypatch.setattr(powerdns,'get_zone',lambda zone:dict(name=zone+'.',rrsets=records))
    monkeypatch.setattr(dnsprovider,'list_records',lambda zone:[])
    monkeypatch.setattr(cron,'_read_raw',lambda username:[])
    dest=make_destination(root)
    policy=jobs.save_policy(dict(name='DNS metadata',destination_id=dest['id'],accounts=['alpha'],components=['config'],frequency='manual'))
    ident=jobs.queue_policy({'id':policy['id']})['run_ids'][0]
    jobs.execute_run(ident)
    run=jobs._row(SnapshotRun,ident)
    assert run.status=='completed',run.error
    repo=jobs.repository(jobs._row(SnapshotDestination,dest['id']))
    restored=storage.restore_to(repo,account.id,run.snapshot_id,str(root/'dns-proof'))
    manifest=json.loads(next(restored.rglob('manifest.json')).read_text())
    dns=manifest['dns_configuration']
    assert dns['account_id']==account.id and dns['username']=='alpha'
    assert len(dns['zones'])==1 and dns['zones'][0]['zone']=='alpha.test'
    assert dns['zones'][0]['records']==records
