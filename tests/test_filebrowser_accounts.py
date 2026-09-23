"""The kernel, not a root file browser's path parser, isolates tenants."""
import os
from pathlib import Path
import socket
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
    monkeypatch.setattr(fb.pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
    monkeypatch.setattr(fb.grp, 'getgrnam', lambda _: SimpleNamespace(gr_gid=os.getgid()))
    listener = socket.socket(socket.AF_UNIX)
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ['systemctl', 'start']:
            listener.bind(account_socket('demo1'))
        return ProcResult(args=args, returncode=0, stdout='', stderr='')
    monkeypatch.setattr(fb, 'run', run)
    yield home, calls
    listener.close()


def test_instance_config_uses_own_state_and_unix_socket(isolated_instance):
    home, calls = isolated_instance
    fb.start('demo1', str(home))
    root = Path(settings.filebrowser_account_data_dir) / 'demo1'
    config = yaml.safe_load((root / 'config.yaml').read_text())
    assert config['server']['socket'] == account_socket('demo1')
    assert config['server']['database'] == str(root / 'state/database.db')
    assert config['server']['sources'][0]['path'] == str(home.parent)
    assert (root / 'state').stat().st_mode & 0o777 == 0o700
    assert calls == [['systemctl', 'start', 'boron-filebrowser@demo1.service']]


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
