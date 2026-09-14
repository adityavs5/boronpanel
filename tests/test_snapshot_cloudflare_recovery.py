from copy import deepcopy

import pytest

from daemon import cloudflare, snapshot_cloudflare_recovery as recovery
from daemon.snapshot_cloudflare_plan import state
from shared.validation import ValidationError

ZONE='a'*32


def record(ident, value='192.0.2.1', **extra):
    return {'id':f'{ident:032x}','name':'www.alpha.test','type':'A','content':value,'ttl':300,**extra}


@pytest.fixture
def provider(monkeypatch):
    data={'records':[record(1)],'calls':[],'reads':0,'sequence':1000,'fail_after_commit':False,'fail_before_commit':False}
    def read(zone,zone_id=None):
        assert zone=='alpha.test' and zone_id==ZONE
        data['reads']+=1
        return deepcopy(data['records'])
    def apply(zone,batch,*,zone_id):
        assert zone=='alpha.test' and zone_id==ZONE
        data['calls'].append(deepcopy(batch))
        if data['fail_before_commit']: raise cloudflare.CloudflareError(503,'fixture failure')
        records={row['id']:row for row in data['records']}
        for row in batch.get('deletes',[]): records.pop(row['id'])
        for row in batch.get('puts',[]): records[row['id']]=deepcopy(row)
        for row in batch.get('posts',[]):
            data['sequence']+=1; ident=f"{data['sequence']:032x}"
            records[ident]={**deepcopy(row),'id':ident}
        data['records']=list(records.values())
        if data['fail_after_commit']: raise cloudflare.CloudflareError(502,'fixture timeout')
        return {}
    monkeypatch.setattr(cloudflare,'export_record_documents',read)
    monkeypatch.setattr(cloudflare,'apply_record_batch',apply)
    return data


def test_restore_preserves_authority_and_managed_records(provider):
    protected=[{'id':'b'*32,'name':'alpha.test','type':'NS','content':'ns.example','ttl':300},
               record(2,locked=True)]
    protected[1]['name']='managed.alpha.test'
    provider['records']+=protected
    before=deepcopy(provider['records']);desired=[record(99,'192.0.2.3')]
    checkpoints=[];bindings=[]
    result=recovery.apply('alpha.test',ZONE,desired,recovery.validate('alpha.test',before),lambda:bindings.append(True),lambda *args:checkpoints.append(args))
    assert result['updates']==1 and checkpoints==[(1,1)] and len(bindings)>=3
    writable,guarded=recovery.partition('alpha.test',provider['records'])
    assert state('alpha.test',writable)==state('alpha.test',desired)
    assert guarded==protected


def test_committed_timeout_is_verified_without_replay(provider):
    provider['fail_after_commit']=True
    desired=[{**record(99),'name':'new.alpha.test'}]
    recovery.apply('alpha.test',ZONE,desired,provider['records'],lambda:None)
    assert len(provider['calls'])==1
    assert state('alpha.test',provider['records'])==state('alpha.test',desired)


def test_uncommitted_error_stops_without_retry(provider):
    provider['fail_before_commit']=True;before=deepcopy(provider['records'])
    with pytest.raises(ValidationError,match='could not be confirmed'):
        recovery.apply('alpha.test',ZONE,[record(99,'192.0.2.4')],before,lambda:None)
    assert provider['records']==before and len(provider['calls'])==1


def test_large_restore_verifies_each_batch_with_fresh_created_ids(provider):
    desired=[{**record(i+10),'name':f'host{i}.alpha.test'} for i in range(405)]
    checkpoints=[]
    recovery.apply('alpha.test',ZONE,desired,provider['records'],lambda:None,lambda *args:checkpoints.append(args))
    assert len(provider['calls'])==3 and checkpoints==[(1,3),(2,3),(3,3)]
    assert state('alpha.test',provider['records'])==state('alpha.test',desired)


def test_record_id_value_reassociation_is_not_overwritten(provider):
    provider['records'].append(record(2,'192.0.2.2'));before=deepcopy(provider['records']);checks=[]
    def binding():
        checks.append(True)
        if len(checks)==2:
            provider['records'][0]['id'],provider['records'][1]['id']=provider['records'][1]['id'],provider['records'][0]['id']
    with pytest.raises(ValidationError,match='identities changed'):
        recovery.apply('alpha.test',ZONE,[record(99,'192.0.2.4')],before,binding)
    assert not provider['calls']


def test_provider_binding_change_stops_before_write(provider):
    def changed(): raise ValidationError('provider changed')
    with pytest.raises(ValidationError,match='provider changed'):
        recovery.apply('alpha.test',ZONE,[],provider['records'],changed)
    assert not provider['calls'] and provider['reads']==0


def test_managed_owner_conflict_is_rejected(provider):
    provider['records'][0]['locked']=True
    with pytest.raises(ValidationError,match='overlap provider-managed'):
        recovery.apply('alpha.test',ZONE,[record(99,'192.0.2.4')],[],lambda:None)
    assert not provider['calls']


def test_failure_on_later_batch_keeps_verified_partial_state_and_stops(provider):
    desired=[{**record(i+10),'name':f'host{i}.alpha.test'} for i in range(405)]
    checkpoints=[]
    def checkpoint(done,total):
        checkpoints.append((done,total));provider['fail_before_commit']=True
    with pytest.raises(ValidationError,match='could not be confirmed'):
        recovery.apply('alpha.test',ZONE,desired,provider['records'],lambda:None,checkpoint)
    assert checkpoints==[(1,3)] and len(provider['calls'])==2
    assert len(provider['records'])==199
    assert all(row['name'].startswith('host') for row in provider['records'])
