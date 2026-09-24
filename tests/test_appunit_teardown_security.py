import pytest

from daemon import appunits
from daemon.procutil import ProcResult


@pytest.mark.parametrize('state', ['active', 'activating', 'deactivating', '', 'unexpected'])
def test_failed_stop_preserves_unit_and_environment(tmp_path, monkeypatch, state):
    monkeypatch.setattr(appunits, 'UNITS_DIR', tmp_path)
    monkeypatch.setattr(appunits.settings, 'app_env_dir', str(tmp_path))
    name = 'boron-node-demo1-1.service'
    unit = appunits.unit_path(name)
    env = appunits.env_file_path(name)
    unit.write_text('unit canary')
    env.write_text('secret canary')
    calls = []
    def run(args, **kwargs):
        calls.append(args[1])
        return ProcResult(args, 1, state if args[1] == 'is-active' else '', '')
    monkeypatch.setattr(appunits, 'run', run)
    with pytest.raises(RuntimeError, match='stopped safely'):
        appunits.remove_unit(name)
    assert unit.read_text() == 'unit canary'
    assert env.read_text() == 'secret canary'
    assert calls == ['stop', 'is-active']


def test_failed_disable_retains_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(appunits, 'UNITS_DIR', tmp_path)
    name = 'boron-node-demo1-1.service'
    appunits.unit_path(name).write_text('canary')
    def run(args, **kwargs):
        return ProcResult(args, int(args[1] == 'disable'), '', '')
    monkeypatch.setattr(appunits, 'run', run)
    with pytest.raises(RuntimeError, match='disabled safely'):
        appunits.remove_unit(name)
    assert appunits.unit_path(name).read_text() == 'canary'


def test_absent_unit_teardown_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(appunits, 'UNITS_DIR', tmp_path)
    monkeypatch.setattr(appunits.settings, 'app_env_dir', str(tmp_path))
    def run(args, **kwargs):
        return ProcResult(args, 0 if args[1] == 'daemon-reload' else 4, 'unknown' if args[1] == 'is-active' else '', '')
    monkeypatch.setattr(appunits, 'run', run)
    appunits.remove_unit('absent.service')
