import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest
from daemon import pma
from shared.config import settings


def test_real_php_concurrent_tokens_and_replay(tmp_path,monkeypatch):
    php=shutil.which('php')
    if not php:pytest.skip('PHP required')
    root=tmp_path/'pma';root.mkdir()
    tokens=tmp_path/'tokens';tokens.mkdir()
    sessions=tmp_path/'sessions';sessions.mkdir()
    monkeypatch.setattr(settings,'pma_docroot',str(root))
    monkeypatch.setattr(settings,'pma_token_dir',str(tokens))
    pma.bootstrap_pma_files()
    token='A'*40
    tokenfile=tokens/(hashlib.sha256(token.encode()).hexdigest()+'.json')
    tokenfile.write_text(json.dumps({'db_user':'testuser','db_password':'test-only','db_name':'alpha_wp','expires_at':int(time.time())+60}))
    wrapper=tmp_path/'wrapper.php'
    wrapper.write_text('''<?php
$_GET['token']=getenv('TEST_TOKEN');
register_shutdown_function(function(){echo json_encode(['status'=>http_response_code(),'authenticated'=>isset($_SESSION['PMA_single_signon_user'])]);});
while(!file_exists(getenv('TEST_GATE'))) usleep(1000);
require getenv('TEST_SCRIPT');
''')
    gate=tmp_path/'start'
    env=dict(os.environ,TEST_TOKEN=token,TEST_GATE=str(gate),TEST_SCRIPT=str(root/'boron_signon.php'))
    args=[php,'-d','session.save_path='+str(sessions),str(wrapper)]
    processes=[subprocess.Popen(args,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(12)]
    try:
        gate.touch()
        results=[]
        for process in processes:
            out,err=process.communicate(timeout=15)
            assert process.returncode==0,err
            results.append(json.loads(out[out.rfind('{'):]))
        assert sum(r['authenticated'] for r in results)==1
        assert sum(r['status']==403 for r in results)==11
        assert not tokenfile.exists()
        replay=subprocess.run(args,env=env,capture_output=True,text=True,timeout=15)
        assert json.loads(replay.stdout[replay.stdout.rfind('{'):])=={'status':403,'authenticated':False}
    finally:
        for process in processes:
            if process.poll() is None:process.kill();process.wait()
