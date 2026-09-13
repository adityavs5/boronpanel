from types import SimpleNamespace
import pytest
from daemon import cmdjobs
from shared.db import write_session
from shared.models import Account, CommandRun


@pytest.mark.parametrize('fails', [False, True])
def test_completion_callback_controls_final_job_status(isolated_db, monkeypatch, fails):
    with write_session() as s:
        a=Account(username='alice');s.add(a);s.flush()
        job=CommandRun(account_id=a.id,kind='wpmanager',target='/tmp',command_display='test')
        s.add(job);s.flush();job_id=job.id
    monkeypatch.setattr(cmdjobs,'run',lambda *args,**kw:SimpleNamespace(returncode=0,stdout='worker result',stderr=''))
    events=[]
    def finish():
        events.append('finish')
        assert cmdjobs._get(job_id).status=='running'
        if fails: raise RuntimeError('database cleanup failed')
    cmdjobs._run_job(job_id,[], '/tmp', [], 5,on_success=finish,on_failure=lambda:events.append('rollback'))
    job=cmdjobs._get(job_id)
    assert job.status==('failed' if fails else 'completed')
    assert events==(['finish','rollback'] if fails else ['finish'])
    if fails: assert job.error=='database cleanup failed'
