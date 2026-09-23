import sys
import os
from pathlib import Path
import subprocess
import time
import uuid

import pytest

from daemon import snapshot_mail_service as service
from shared.validation import ValidationError
from shared.config import settings


@pytest.fixture
def isolated_service(tmp_path, monkeypatch):
    if not Path(settings.snapshot_private_dir).is_relative_to(tmp_path):
        monkeypatch.setattr(settings, 'snapshot_private_dir', str(tmp_path / 'service-private'))
    if os.geteuid() != 0 or not Path('/run/systemd/system').is_dir():
        pytest.skip('Root and running systemd required')
    name = 'boron-mail-test-' + uuid.uuid4().hex + '.service'
    path = Path('/run/systemd/system') / name
    operations = []
    path.write_text('[Unit]\nDescription=Boron isolated mail switch test\n'
                    '[Service]\nType=simple\nExecStart=/usr/bin/sleep 300\nKillMode=control-group\n')
    path.chmod(0o644)
    def ctl(*args):
        return subprocess.run(['/usr/bin/systemctl', *args], capture_output=True, text=True, timeout=20)
    try:
        assert ctl('daemon-reload').returncode == 0
        assert ctl('start', name).returncode == 0
        yield name, operations
    finally:
        for operation in operations:
            unit = service.switch_unit(name)
            ctl('stop', unit)
            ctl('reset-failed', unit)
        ctl('stop', name)
        path.unlink(missing_ok=True)
        ctl('daemon-reload')
        ctl('reset-failed', name)


@pytest.mark.parametrize('crash', [False, True])
def test_systemd_resumes_service_after_success_or_worker_kill(isolated_service, tmp_path, crash):
    name, operations = isolated_service
    operation = uuid.uuid4().hex
    operations.append(operation)
    proof = tmp_path / 'barrier-proof'
    script = tmp_path / 'switch.py'
    script.write_text('import os, signal, subprocess, sys\nfrom pathlib import Path\n'
                      'r = subprocess.run(["/usr/bin/systemctl", "show", sys.argv[1], '
                      '"-p", "ActiveState", "-p", "MainPID", "-p", "ControlPID"], '
                      'capture_output=True, text=True, check=True)\n'
                      'state = dict(line.split("=", 1) for line in r.stdout.splitlines())\n'
                      'assert state == {"ActiveState":"inactive", "MainPID":"0", "ControlPID":"0"}\n'
                      'Path(sys.argv[2]).write_text("mail service stopped before switch")\n'
                      'if sys.argv[3] == "crash": os.kill(os.getpid(), signal.SIGKILL)\n')
    command = ['/usr/bin/python3', str(script), name, str(proof), 'crash' if crash else 'success']
    before = service.service_status(name)
    if crash:
        with pytest.raises(ValidationError, match='Mail switch failed'):
            service.supervised_command(command, operation, service=name)
    else:
        result = service.supervised_command(command, operation, service=name)
        assert result['mail_service'] == 'running'
    assert proof.read_text() == 'mail service stopped before switch'
    after = service.service_status(name)
    assert after['ActiveState'] == 'active' and after['SubState'] == 'running'
    assert after['MainPID'] != before['MainPID']
    observed = service.inspect_switch(operation, service=name)
    assert observed['state'] in ('terminal', 'missing')
    if crash:
        assert observed['state'] == 'terminal'
        assert observed['result'] == 'signal'
        with pytest.raises(ValidationError, match='another operation'):
            service.inspect_switch(uuid.uuid4().hex, service=name)
        with pytest.raises(ValidationError, match='another operation'):
            service.retire_switch(uuid.uuid4().hex, service=name)


def test_inactive_service_is_not_started_by_a_restore(isolated_service):
    name, operations = isolated_service
    subprocess.run(['/usr/bin/systemctl', 'stop', name], check=True, capture_output=True, timeout=20)
    with pytest.raises(ValidationError, match='must be running'):
        service.supervised_command(['/usr/bin/true'], uuid.uuid4().hex, service=name)
    assert service.service_status(name)['ActiveState'] == 'inactive'


def test_active_service_fails_stopped_barrier(isolated_service):
    with pytest.raises(ValidationError, match='has not stopped completely'):
        service.require_stopped(isolated_service[0])


def test_arbitrary_service_rejected():
    with pytest.raises(ValidationError):
        service.supervised_command(['/usr/bin/true'], uuid.uuid4().hex, service='ssh.service')


def test_switch_survives_loss_of_calling_panel_process(isolated_service, tmp_path):
    name, operations = isolated_service
    operation = uuid.uuid4().hex
    operations.append(operation)
    entered, finish = tmp_path / 'entered', tmp_path / 'finish'
    worker = tmp_path / 'worker.py'
    worker.write_text('import time, sys\nfrom pathlib import Path\n'
                      'Path(sys.argv[1]).touch()\n'
                      'deadline = time.monotonic() + 15\n'
                      'while not Path(sys.argv[2]).exists():\n'
                      ' if time.monotonic() > deadline: raise RuntimeError("test timeout")\n'
                      ' time.sleep(.02)\n')
    command = ['/usr/bin/python3', str(worker), str(entered), str(finish)]
    caller = tmp_path / 'caller.py'
    repo = Path(__file__).parents[1]
    caller.write_text(f'import sys\nsys.path.insert(0, {str(repo)!r})\n'
                      'from shared.config import settings\n'
                      f'settings.snapshot_private_dir = {settings.snapshot_private_dir!r}\n'
                      'from daemon.snapshot_mail_service import supervised_command\n'
                      f'supervised_command({command!r}, {operation!r}, service={name!r})\n')
    process = subprocess.Popen([sys.executable, str(caller)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not entered.exists():
            assert process.poll() is None, 'Caller exited before the supervised worker started'
            assert time.monotonic() < deadline
            time.sleep(.05)
        service.require_stopped(name)
        assert service.inspect_switch(operation, service=name)['state'] == 'running'
        with pytest.raises(BlockingIOError):
            service.retire_switch(operation, service=name)
        process.kill()
        process.wait(timeout=5)
        assert service.inspect_switch(operation, service=name)['state'] == 'running'
        with pytest.raises(ValidationError, match='still running'):
            service.retire_switch(operation, service=name)
        finish.touch()
        deadline = time.monotonic() + 10
        while service.service_status(name)['ActiveState'] != 'active':
            assert time.monotonic() < deadline, 'Supervisor did not resume the test service'
            time.sleep(.05)
    finally:
        finish.touch()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_absent_switch_is_observed_without_creating_a_unit(isolated_service):
    name, operations = isolated_service
    assert service.inspect_switch(uuid.uuid4().hex, service=name) == {
        'unit': service.switch_unit(name), 'state': 'missing', 'result': None}


@pytest.mark.parametrize('change', [
    {'ControlPID': '123'}, {'MainPID': '123'}, {'Job': '123'},
    {'ActiveState': 'deactivating'},
])
def test_cleanup_or_pending_job_is_not_terminal(monkeypatch, change):
    from types import SimpleNamespace
    operation = uuid.uuid4().hex
    fields = {'LoadState': 'loaded', 'ActiveState': 'inactive', 'SubState': 'dead',
              'Description': 'Boron mailbox switch ' + operation,
              'MainPID': '0', 'ControlPID': '0', 'Job': '', 'Result': 'success'}
    fields.update(change)
    monkeypatch.setattr(service, 'run', lambda *a, **kw: SimpleNamespace(
        ok=True, stdout='\n'.join(k+'='+v for k, v in fields.items())))
    assert service.inspect_switch(operation)['state'] == 'running'


def test_observation_error_is_not_a_missing_worker(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(service, 'run', lambda *a, **kw: SimpleNamespace(ok=False, stdout=''))
    with pytest.raises(ValidationError, match='Could not inspect'):
        service.inspect_switch(uuid.uuid4().hex)
