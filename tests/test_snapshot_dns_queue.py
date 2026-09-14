from copy import deepcopy
import pytest
from sqlalchemy import select
from tests.test_snapshot_jobs import environment, make_destination
from daemon import cron, powerdns, snapshot_jobs as jobs, snapshot_restores as restores, snapshot_storage as storage
from shared.db import write_session
from shared.models import Account, DnsZone, SnapshotRun, SnapshotRestore
from shared.validation import ValidationError


@pytest.fixture
def dns_queue(environment,monkeypatch):
    root,_=environment
    with write_session() as session:
        account=session.scalar(select(Account).where(Account.username=='alpha'))
        session.add(DnsZone(account_id=account.id,zone='alpha.test'))
    original=[dict(name='www.alpha.test.',type='A',ttl=600,records=[dict(content='192.0.2.1',disabled=True)],comments=[])]
    live=deepcopy(original)
    monkeypatch.setattr(powerdns,'get_zone',lambda name:dict(name=name+'.',rrsets=deepcopy(live)))
    monkeypatch.setattr(cron,'_read_raw',lambda username:[])
    def patch(name,changes):
        for change in changes:
            live[:]=[row for row in live if (row['name'],row['type'])!=(change['name'],change['type'])]
            if change['changetype']=='REPLACE':live.append({k:deepcopy(v) for k,v in change.items() if k!='changetype'})
    monkeypatch.setattr(powerdns,'apply_rrset_changes',patch)
    dest=make_destination(root)
    policy=jobs.save_policy(dict(name='Queued DNS',destination_id=dest['id'],accounts=['alpha'],components=['config'],frequency='manual'))
    ident=jobs.queue_policy({'id':policy['id']})['run_ids'][0];jobs.execute_run(ident)
    assert jobs._row(SnapshotRun,ident).status=='completed'
    return ident,live,original


def test_dns_catalog_decrypts_once_and_exposes_only_summary(dns_queue,monkeypatch):
    ident,_,_=dns_queue
    original=storage.restore_to
    calls=[]
    def read(*args,**kwargs):
        calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(storage,'restore_to',read)
    result=restores.configuration_options(dict(username='alpha',run_id=ident))
    assert calls==[1]
    assert result['dns_available'] and result['cron_available'] and result['php_available']
    assert result['dns_zones']==[dict(zone='alpha.test',provider='local',available=True,record_count=1)]
    assert '192.0.2.1' not in repr(result)


def test_queued_dns_restore_undo_redo_preserves_zone_selection(dns_queue):
    ident,live,original=dns_queue
    live[0]['records'][0]['content']='192.0.2.99'
    before=deepcopy(live)
    request=restores.trigger(dict(username='alpha',run_id=ident,confirmation='alpha',kind='config',config_sections=['dns'],dns_zones=['alpha.test']))
    restores.execute(request['id'])
    row=jobs._row(SnapshotRestore,request['id'])
    assert row.status=='completed',row.error
    assert live==original and row.selection['dns_zones']==['alpha.test']
    for expected in (before,original):
        request=restores.undo(dict(username='alpha',restore_id=row.id,confirmation='alpha'))
        restores.execute(request['id'])
        row=jobs._row(SnapshotRestore,request['id'])
        assert row.status=='completed',row.error
        assert live==expected and row.selection['dns_zones']==['alpha.test']


def test_dns_queue_rejects_foreign_missing_and_duplicate_zone_selection(dns_queue):
    ident,live,original=dns_queue
    params=dict(username='alpha',run_id=ident,confirmation='alpha',kind='config',config_sections=['dns'])
    for selection in ([],['bravo.test'],['alpha.test','alpha.test']):
        with pytest.raises(ValidationError):restores.trigger(dict(**params,dns_zones=selection))
    with pytest.raises(ValidationError):
        restores.configuration_options(dict(username='bravo',run_id=ident))
    assert live==original
