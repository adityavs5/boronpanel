"""The kernel, not a root file browser's path parser, isolates tenants."""
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest
import yaml

from daemon import filebrowser_accounts as fb
from daemon.procutil import ProcResult
from shared.config import settings
from shared.filebrowser_paths import account_socket


@pytest.fixture
def isolated_instance(tmp_path, monkeypatch):
    home = tmp_path / 'home' / 'demo1'
    home.mkdir(parents=True)
    monkeypatch.setattr(settings, 'home_base', str(home.parent))
    monkeypatch.setattr(settings, 'filebrowser_account_data_dir', str(tmp_path / 'state'))
    monkeypatch.setattr(settings, 'filebrowser_runtime_dir', str(tmp_path / 'run'))
    monkeypatch.setattr(fb, 'SYSTEMD_DIR', tmp_path / 'systemd')
    monkeypatch.setattr(fb.cgroups, 'ensure_slice', lambda username, uid: 'user-test.slice')
    monkeypatch.setattr(fb.cgroups, 'account_slice_name', lambda username, uid=None: 'user-test.slice')
    monkeypatch.setattr(fb.pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
    monkeypatch.setattr(fb.grp, 'getgrnam', lambda _: SimpleNamespace(gr_gid=os.getgid()))
    socket_path = Path(account_socket('demo1'))
    real_lstat = Path.lstat
    def fake_lstat(path):
        if path == socket_path:
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o700, st_uid=os.getuid())
        return real_lstat(path)
    monkeypatch.setattr(Path, 'lstat', fake_lstat)
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ['systemctl', 'start']:
            socket_path.touch()
        return ProcResult(args=args, returncode=0, stdout='', stderr='')
    monkeypatch.setattr(fb, 'run', run)
    yield home, calls


def test_instance_config_uses_own_state_and_unix_socket(isolated_instance):
    home, calls = isolated_instance
    fb.start('demo1', str(home))
    root = Path(settings.filebrowser_account_data_dir) / 'demo1'
    config = yaml.safe_load((root / 'config.yaml').read_text())
    assert config['server']['socket'] == account_socket('demo1')
    assert config['server']['database'] == str(root / 'state/database.db')
    assert config['server']['sources'][0]['path'] == str(home.parent)
    assert (root / 'state').stat().st_mode & 0o777 == 0o700
    assert calls[-1] == ['systemctl', 'start', 'boron-filebrowser@demo1.service']
    assert any(call[:3] == ['setfacl', '-m', 'd:u:boron-api:rwx'] for call in calls)


def test_instance_rejects_moved_or_symlinked_home(isolated_instance):
    home, calls = isolated_instance
    peer = home.parent / 'peer'
    peer.mkdir()
    home.rmdir()
    home.symlink_to(peer)
    with pytest.raises(RuntimeError, match='ownership'):
        fb.start('demo1', str(home))
    assert not calls


def test_unit_confines_home_and_drops_all_privileges():
    unit = fb.unit_content()
    for requirement in ('User=%i\n', 'Group=%i\n', 'CapabilityBoundingSet=\n',
                        'NoNewPrivileges=true\n', 'ProtectHome=tmpfs\n',
                        'ProtectSystem=strict\n', 'RestrictAddressFamilies=AF_UNIX\n'):
        assert requirement in unit
    assert f'BindPaths={settings.home_base}/%i\n' in unit
    assert 'User=root' not in unit
    assert 'ListenStream' not in unit


def test_frontend_uses_separate_system_identity_and_no_tenant_source(isolated_instance, tmp_path, monkeypatch):
    unit_path = tmp_path / 'ui.service'
    monkeypatch.setattr(fb, 'FRONTEND_TEMPLATE_PATH', str(unit_path))
    fb._bootstrap_frontend()
    unit = unit_path.read_text()
    assert 'User=boron-files-ui\n' in unit
    assert 'Group=boron-files-ui\n' in unit
    assert 'BindPaths=' not in unit
    assert 'ProtectHome=tmpfs' in unit and 'CapabilityBoundingSet=\n' in unit
    data = Path(settings.filebrowser_account_data_dir) / '_frontend'
    config = yaml.safe_load((data / 'config.yaml').read_text())
    assert config['server']['sources'][0]['path'] == str(data / 'empty')
    assert 'createUserDir' not in config['server']['sources'][0]['config']
