from copy import deepcopy

import pytest

from daemon.snapshot_cloudflare_plan import plan, state
from shared.validation import ValidationError


def record(ident, content='192.0.2.1', **extra):
    return dict(id=f'{ident:032x}', name='www.alpha.test', type='A', content=content, ttl=300, **extra)


def test_noop_keeps_current_ids_despite_saved_ids_order_and_defaults():
    current=[record(1),record(2,'192.0.2.2',settings={'ipv4_only':False},private_routing=False)]
    desired=[record(99,'192.0.2.2'),record(98)]
    assert plan('alpha.test',current,desired)=={'batches':[],'unchanged':2,'deletes':0,'updates':0,'creates':0}


def test_equivalent_ipv6_spellings_do_not_create_changes():
    current=[{'id':'a'*32,'name':'www.alpha.test','type':'AAAA','content':'2001:0db8:0:0:0:0:0:1','ttl':300}]
    desired=[{**current[0],'content':'2001:db8::1'}]
    assert plan('alpha.test',current,desired)['batches']==[]


def test_secure_child_delegation_allows_ns_and_ds_at_same_name():
    records=[{'name':'child.alpha.test','type':'NS','ttl':300,'content':'ns.external.test'},
             {'name':'child.alpha.test','type':'DS','ttl':300,'data':{'key_tag':12345,'algorithm':13,'digest_type':2,'digest':'ab'*32}}]
    result=plan('alpha.test',[],records)
    assert result['creates']==2


def test_changes_only_target_current_ids_and_preserve_native_options():
    current=[record(1),record(2,'192.0.2.2')]
    desired=[record(999,'192.0.2.3',proxied=False,comment='new',tags=['owner:qa'])]
    before=deepcopy(current);saved=deepcopy(desired)
    result=plan('alpha.test',current,desired)
    assert result['updates']==1 and result['deletes']==1
    batch=result['batches'][0]
    assert batch['deletes']==[{'id':f'{2:032x}'}]
    assert batch['puts'][0]['id']==f'{1:032x}'
    assert batch['puts'][0]['comment']=='new' and batch['puts'][0]['tags']==['owner:qa']
    assert current==before and desired==saved


def test_metadata_changes_match_existing_dns_values_before_pairing():
    current=[record(1,'192.0.2.2',comment='before'),record(2,'192.0.2.1',comment='before')]
    desired=[record(99,'192.0.2.1',comment='after'),record(98,'192.0.2.2',comment='after')]
    puts=plan('alpha.test',current,desired)['batches'][0]['puts']
    assert {r['id']:r['content'] for r in puts}=={f'{1:032x}':'192.0.2.2',f'{2:032x}':'192.0.2.1'}


def test_large_rrset_to_cname_deletes_before_create_across_batches():
    current=[record(i+1,f'192.0.{i//256}.{i%256}') for i in range(405)]
    desired=[{'id':'never-authority','name':'www.alpha.test','type':'CNAME','content':'target.example','ttl':1,'proxied':True}]
    result=plan('alpha.test',current,desired)
    assert result['deletes']==405 and result['creates']==1
    assert [sum(map(len,b.values())) for b in result['batches']]==[200,200,6]
    assert set(result['batches'][0])==set(result['batches'][1])=={'deletes'}
    assert 'id' not in result['batches'][-1]['posts'][0]


def test_plan_applies_to_desired_state_including_duplicate_counts():
    current=[record(1),record(2,'192.0.2.2')]
    desired=[record(99,'192.0.2.2'),record(100,'192.0.2.3'),record(101,'192.0.2.4')]
    result=plan('alpha.test',current,desired)
    actual={r['id']:r for r in current}
    for batch in result['batches']:
        for row in batch.get('deletes',[]): actual.pop(row['id'])
        for row in batch.get('puts',[]): actual[row['id']]=row
        for index,row in enumerate(batch.get('posts',[])): actual[f'new-{index}']=row
    assert state('alpha.test',list(actual.values()))==state('alpha.test',desired)
    assert state('alpha.test',[current[0],current[0]])!=state('alpha.test',[current[0]])


@pytest.mark.parametrize('bad', ['foreign','duplicate_id','cname_conflict','managed'])
def test_invalid_collection_cannot_produce_a_write_plan(bad):
    current=[record(1)];desired=[record(2)]
    if bad=='foreign': desired[0]['name']='other.test'
    elif bad=='duplicate_id': current.append(record(1,'192.0.2.2'))
    elif bad=='managed': current[0]['locked']=True
    else: desired.append({'name':'www.alpha.test','type':'CNAME','content':'target.test','ttl':300})
    with pytest.raises(ValidationError): plan('alpha.test',current,desired)
