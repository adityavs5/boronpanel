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
    from daemon.snapshot_configuration import load_dns
    assert load_dns(repo,account,run.snapshot_id)['zones'][0]['records']==records


@pytest.fixture
def local_recovery(dns_account, monkeypatch):
    account,foreign=dns_account
    records=[dict(name='alpha.test.',type='NS',ttl=3600,records=[dict(content='ns.alpha.test.',disabled=False)]),
             dict(name='*.alpha.test.',type='A',ttl=600,records=[dict(content='192.0.2.1',disabled=True)],comments=[]),
             dict(name='_acme-challenge.alpha.test.',type='TXT',ttl=60,records=[dict(content='"saved token"',disabled=False)])]
    monkeypatch.setattr(powerdns,'get_zone',lambda zone:dict(name=zone+'.',rrsets=records))
    return account, snapshot_dns.capture(account)


def test_local_recovery_validates_native_records_and_preserves_current_nameservers(local_recovery):
    account,payload=local_recovery
    result=snapshot_dns.validate_for_restore(account,payload)
    records=result['zones'][0]['records']
    assert len(records)==2 and all(row['type']!='NS' for row in records)
    assert records[0]['records'][0]['disabled'] is True
    assert records[1]['name']=='_acme-challenge.alpha.test.'
    assert len(payload['zones'][0]['records'])==3


@pytest.mark.parametrize('field,value', [('name','evil.test.'),('ttl',True),('ttl',-1),
    ('records',[{'content':'not an IP','disabled':False}]),('records',[{'content':'192.0.2.1','disabled':1}]),
    ('type','AXFR')])
def test_local_recovery_rejects_invalid_records(local_recovery, field, value):
    account,payload=local_recovery
    payload['zones'][0]['records'][1][field]=value
    with pytest.raises(ValidationError):snapshot_dns.validate_for_restore(account,payload)


def test_dns_recovery_rejects_changed_binding_and_duplicate_selection(local_recovery):
    account,payload=local_recovery
    with pytest.raises(ValidationError,match='selection'):
        snapshot_dns.validate_for_restore(account,payload,['alpha.test','alpha.test'])
    payload['zones'][0]['binding']['zone_id']=True
    with pytest.raises(ValidationError,match='registration'):
        snapshot_dns.validate_for_restore(account,payload)


def test_dns_recovery_allows_selecting_owned_subset(local_recovery):
    account,payload=local_recovery
    payload['zones'].append(dict(zone='deleted.test',provider='local',binding={},records=[]))
    assert len(snapshot_dns.validate_for_restore(account,payload,['alpha.test'])['zones'])==1
    with pytest.raises(ValidationError,match='registration'):
        snapshot_dns.validate_for_restore(account,payload)


def test_dns_recovery_rejects_apex_cname(local_recovery):
    account,payload=local_recovery
    payload['zones'][0]['records'][1]=dict(name='alpha.test.',type='CNAME',ttl=60,
                                         records=[dict(content='target.test.',disabled=False)])
    with pytest.raises(ValidationError,match='apex'):
        snapshot_dns.validate_for_restore(account,payload)


@pytest.fixture
def mutable_dns(local_recovery, monkeypatch):
    from copy import deepcopy
    account,saved=local_recovery
    current=deepcopy(saved['zones'][0]['records'])
    calls=[]
    monkeypatch.setattr(powerdns,'get_zone',lambda name:dict(name=name+'.',rrsets=deepcopy(current)))
    def patch(name, changes):
        calls.append(deepcopy(changes))
        for change in changes:
            current[:]=[row for row in current if (row['name'],row['type'])!=(change['name'],change['type'])]
            if change['changetype']=='REPLACE':
                current.append({key:deepcopy(value) for key,value in change.items() if key!='changetype'})
    monkeypatch.setattr(powerdns,'apply_rrset_changes',patch)
    return account,saved,current,calls


def test_dns_apply_saves_before_mutation_deletes_new_records_and_undoes(mutable_dns):
    from copy import deepcopy
    account,saved,current,calls=mutable_dns
    current[1]['records'][0]['content']='192.0.2.88'
    current.append(dict(name='new.alpha.test.',type='A',ttl=60,records=[dict(content='192.0.2.99',disabled=False)]))
    before=deepcopy(current)
    copies=[]
    def save(previous):
        assert current==before and not calls
        copies.append(previous)
    assert snapshot_dns.apply_configuration(account,saved,save)==['alpha.test']
    assert len(calls)==1
    assert any(row['changetype']=='DELETE' and row['name']=='new.alpha.test.' for row in calls[0])
    assert not any(row['type']=='NS' for row in calls[0])
    assert next(row for row in current if row['type']=='NS')==before[0]
    snapshot_dns.apply_configuration(account,copies[0],lambda previous:None)
    actual=snapshot_dns.validate_for_restore(account,snapshot_dns.capture(account))
    assert snapshot_dns._record_state(actual['zones'][0]['records'])==snapshot_dns._record_state(copies[0]['zones'][0]['records'])


def test_dns_apply_does_not_write_when_safety_capture_fails(mutable_dns):
    account,saved,current,calls=mutable_dns
    def fail(previous):raise RuntimeError('backup unavailable')
    with pytest.raises(RuntimeError,match='backup unavailable'):
        snapshot_dns.apply_configuration(account,saved,fail)
    assert not calls


def test_dns_apply_rejects_record_edit_during_safety_capture(mutable_dns):
    account,saved,current,calls=mutable_dns
    def edit(previous):current[1]['ttl']=900
    with pytest.raises(ValidationError,match='changed while preparing'):
        snapshot_dns.apply_configuration(account,saved,edit)
    assert not calls and current[1]['ttl']==900


def test_dns_apply_reports_unconfirmed_provider_result_without_leaking_details(mutable_dns,monkeypatch):
    account,saved,current,calls=mutable_dns
    captured=[]
    def fail(name,changes):raise RuntimeError('private provider detail')
    monkeypatch.setattr(powerdns,'apply_rrset_changes',fail)
    with pytest.raises(ValidationError,match='retained for undo') as error:
        snapshot_dns.apply_configuration(account,saved,captured.append)
    assert captured and 'private provider detail' not in str(error.value)


def test_powerdns_recovery_uses_one_patch(monkeypatch):
    import httpx
    from shared.config import settings
    changes=[dict(name='old.alpha.test.',type='A',changetype='DELETE'),
             dict(name='new.alpha.test.',type='A',ttl=60,changetype='REPLACE',records=[dict(content='192.0.2.1',disabled=True)],comments=[])]
    requests=[]
    def transport(request):
        import json
        requests.append((request.method,request.url.path,json.loads(request.content)))
        return httpx.Response(204)
    monkeypatch.setattr(powerdns,'_client',lambda:httpx.Client(base_url='http://powerdns.test',transport=httpx.MockTransport(transport)))
    powerdns.apply_rrset_changes('alpha.test',changes)
    assert requests==[('PATCH',f'/servers/{settings.powerdns_server_id}/zones/alpha.test.',{'rrsets':changes})]


def test_dns_worker_encrypts_previous_records_and_supports_undo(environment,monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace
    from daemon import cron,snapshot_jobs as jobs,snapshot_configuration as config
    from shared.models import SnapshotRun,SnapshotDestination
    root,_=environment
    with write_session() as session:
        account=session.scalar(select(Account).where(Account.username=='alpha'))
        session.add(DnsZone(account_id=account.id,zone='alpha.test'))
    original=[dict(name='www.alpha.test.',type='A',ttl=600,records=[dict(content='192.0.2.1',disabled=True)],comments=[])]
    live=deepcopy(original)
    monkeypatch.setattr(powerdns,'get_zone',lambda name:dict(name=name+'.',rrsets=deepcopy(live)))
    monkeypatch.setattr(cron,'_read_raw',lambda username:[])
    dest=make_destination(root)
    policy=jobs.save_policy(dict(name='DNS restore',destination_id=dest['id'],accounts=['alpha'],components=['config'],frequency='manual'))
    ident=jobs.queue_policy({'id':policy['id']})['run_ids'][0];jobs.execute_run(ident)
    run=jobs._row(SnapshotRun,ident);assert run.status=='completed',run.error
    repo=jobs.repository(jobs._row(SnapshotDestination,dest['id']))
    live[0]['records'][0]['content']='192.0.2.55'
    before=deepcopy(live)
    updates={}
    def update(ident,**values):updates.update(values)
    def patch(name,changes):
        assert updates.get('safety_snapshot_id')
        for change in changes:
            live[:]=[row for row in live if (row['name'],row['type'])!=(change['name'],change['type'])]
            if change['changetype']=='REPLACE':live.append({k:deepcopy(v) for k,v in change.items() if k!='changetype'})
    monkeypatch.setattr(powerdns,'apply_rrset_changes',patch)
    work=jobs.private_directory('restores','restore-991')
    config.restore_dns(991,account,SimpleNamespace(selection={'dns_zones':['alpha.test']}),repo,run.snapshot_id,work,update)
    assert updates['status']=='completed' and live==original
    safety=updates['safety_snapshot_id']
    assert config.load_dns(repo,account,safety,source_restore_id=991)['zones'][0]['records']==before
    updates.clear()
    config.restore_dns(992,account,SimpleNamespace(selection={'dns_zones':['alpha.test'],'source_restore_id':991}),repo,safety,
                       jobs.private_directory('restores','restore-992'),update)
    assert updates['status']=='completed' and live==before


def test_dns_readback_detects_provider_success_without_expected_records(mutable_dns,monkeypatch):
    account,saved,current,calls=mutable_dns
    current[1]['records'][0]['content']='192.0.2.77'
    monkeypatch.setattr(powerdns,'apply_rrset_changes',lambda name,changes:None)
    copies=[]
    with pytest.raises(ValidationError,match='could not be confirmed'):
        snapshot_dns.apply_configuration(account,saved,copies.append)
    assert copies and current[1]['records'][0]['content']=='192.0.2.77'
