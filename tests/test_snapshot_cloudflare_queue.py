from copy import deepcopy

import pytest
from sqlalchemy import select

from daemon import cloudflare, cloudflare_accounts, cron, snapshot_jobs as jobs, snapshot_restores as restores
from daemon.snapshot_cloudflare_plan import state
from shared.db import write_session
from shared.models import Account, CloudflareZone, DnsZone, SnapshotRun, SnapshotRestore
from tests.test_snapshot_jobs import environment, make_destination
from tests.test_snapshot_cloudflare_recovery import provider, ZONE


def test_real_encrypted_cloudflare_queue_restore_and_undo(environment,provider,monkeypatch):
    root,_=environment
    with write_session() as session:
        account=session.scalar(select(Account).where(Account.username=='alpha'))
        session.add(DnsZone(account_id=account.id,zone='alpha.test'))
        session.add(CloudflareZone(account_id=account.id,zone='alpha.test',cf_zone_id=ZONE,status='active'))
    monkeypatch.setattr(cloudflare_accounts,'token_for_id',lambda ident:'fixture-scoped-token')
    monkeypatch.setattr(cron,'_read_raw',lambda username:[])
    send=cloudflare.apply_record_batch
    def scoped(*args,**kwargs):
        assert cloudflare._token_var.get()=='fixture-scoped-token'
        return send(*args,**kwargs)
    monkeypatch.setattr(cloudflare,'apply_record_batch',scoped)
    dest=make_destination(root)
    policy=jobs.save_policy(dict(name='Cloudflare recovery QA',destination_id=dest['id'],accounts=['alpha'],components=['config'],frequency='manual'))
    ident=jobs.queue_policy({'id':policy['id']})['run_ids'][0];jobs.execute_run(ident)
    assert jobs._row(SnapshotRun,ident).status=='completed'
    original=deepcopy(provider['records'])
    provider['records'][0]['content']='192.0.2.99'
    modified=deepcopy(provider['records'])
    catalog=restores.configuration_options(dict(username='alpha',run_id=ident))
    assert catalog['dns_zones']==[dict(zone='alpha.test',provider='cloudflare',available=True,record_count=1)]
    assert '192.0.2' not in repr(catalog)
    request=restores.trigger(dict(username='alpha',run_id=ident,confirmation='alpha',kind='config',config_sections=['dns'],dns_zones=['alpha.test']))
    restores.execute(request['id']);row=jobs._row(SnapshotRestore,request['id'])
    assert row.status=='completed',row.error
    assert row.safety_snapshot_id and state('alpha.test',provider['records'])==state('alpha.test',original)
    undo=restores.undo(dict(username='alpha',restore_id=row.id,confirmation='alpha'))
    restores.execute(undo['id']);row=jobs._row(SnapshotRestore,undo['id'])
    assert row.status=='completed',row.error
    assert state('alpha.test',provider['records'])==state('alpha.test',modified)
    assert cloudflare._token_var.get() is None
