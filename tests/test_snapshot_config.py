from pathlib import Path
import shutil

import pytest
from sqlalchemy import delete, select

from daemon import snapshot_config, snapshot_jobs as jobs
from shared.config import settings
from shared.db import write_session
from shared.models import Account, SnapshotDestination, SnapshotPolicy, SnapshotRun


@pytest.fixture
def environment(isolated_db,tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'snapshot_private_dir',str(tmp_path/'private'))
    monkeypatch.setattr(settings,'home_base',str(tmp_path/'home'))
    monkeypatch.setattr(jobs._executor,'submit',lambda *_:None)
    home=tmp_path/'home'/'alpha';home.mkdir(parents=True);(home/'site.txt').write_text('site')
    with write_session() as session:session.add(Account(username='alpha',status='active',uid=2000,gid=2000))
    return tmp_path,[]


def test_encrypted_configuration_preview_and_recovery(environment):
    root,_=environment
    destination=jobs.create_destination({'name':'Recoverable local','path':str(root/'repository')})
    jobs.initialize_destination({'id':destination['id']})
    policy=jobs.save_policy({'name':'Recovery job','destination_id':destination['id'],
        'accounts':['alpha'],'components':['files'],'mode':'incremental'})
    ident=jobs.queue_policy({'id':policy['id']})['run_ids'][0];jobs.execute_run(ident)
    password=jobs.recovery_key({'id':destination['id']})['password']
    exported=snapshot_config.export_configuration({})
    assert exported['summary']=={'destinations':1,'policies':1,'catalog_entries':1}
    assert password not in exported['payload']
    with pytest.raises(Exception,match='incorrect|corrupt'):
        snapshot_config.import_configuration({'payload':exported['payload'],'recovery_key':'wrong-key-value-long-enough'})
    preview=snapshot_config.import_configuration({'payload':exported['payload'],'recovery_key':exported['recovery_key']})
    assert preview['status']=='preview' and preview['conflicts']['destinations']==['Recoverable local']

    namespace=destination['namespace']
    with write_session() as session:
        session.execute(delete(SnapshotRun));session.execute(delete(SnapshotPolicy));session.execute(delete(SnapshotDestination))
    shutil.rmtree(Path(settings.snapshot_private_dir)/'repositories'/namespace)
    imported=snapshot_config.import_configuration({'payload':exported['payload'],
        'recovery_key':exported['recovery_key'],'apply':True})
    assert imported['status']=='imported' and imported['catalog_imported']==1
    with write_session() as session:
        restored=session.scalar(select(SnapshotDestination));restored_policy=session.scalar(select(SnapshotPolicy))
        assert restored.enabled is False and restored.status=='draft'
        assert restored_policy.enabled is False
    assert jobs.recovery_key({'id':restored.id})['password']==password
