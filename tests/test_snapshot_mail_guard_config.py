import pytest

from daemon import snapshot_mail_guard_config as config
from shared.config import settings
from shared.validation import ValidationError
from tests.test_mail_restore_gate import gate


@pytest.fixture
def configured(gate, tmp_path, monkeypatch):
    directory, lookup = gate
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(directory))
    binary = str(tmp_path / 'gate')
    path = tmp_path / 'dovecot.conf'
    normal = 'userdb {\n driver = static\n args = uid=65534 gid=65534\n}\n'
    path.write_text('ssl = no\n' + config.render(binary) + normal)
    return path, binary, directory, normal


def test_compiled_guard_directory_and_effective_config_verified(configured):
    path, binary, directory, normal = configured
    assert config.verify(binary=binary, config=path) == {'guard': 'ready'}


@pytest.mark.parametrize('bad', ['order', 'cache', 'internalfail', 'skip', 'success'])
def test_unsafe_effective_configuration_is_refused(configured, bad):
    path, binary, directory, normal = configured
    text = path.read_text()
    if bad == 'order': text = 'ssl = no\n' + normal + config.render(binary)
    elif bad == 'cache': text += 'auth_cache_size = 1 M\n'
    elif bad == 'internalfail': text = text.replace('result_internalfail = return-fail', 'result_internalfail = continue')
    elif bad == 'skip': text = text.replace('skip = never', 'skip = found')
    else: text = text.replace('result_success = return-fail', 'result_success = return-ok')
    path.write_text(text)
    with pytest.raises(ValidationError):
        config.verify(binary=binary, config=path)


def test_compiled_directory_must_match_runtime_settings(configured, tmp_path, monkeypatch):
    path, binary, directory, normal = configured
    other = tmp_path / 'other'
    other.mkdir(mode=0o700)
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(other))
    with pytest.raises(ValidationError, match='different storage'):
        config.verify(binary=binary, config=path)


def test_writable_guard_executable_refused(configured):
    from pathlib import Path
    path, binary, directory, normal = configured
    Path(binary).chmod(0o777)
    with pytest.raises(ValidationError, match='controlled by root'):
        config.verify(binary=binary, config=path)


def test_installer_builds_verified_executable_and_can_replace_it(configured):
    from pathlib import Path
    path, binary, directory, normal = configured
    previous = Path(binary).stat().st_ino
    installed = config.install_binary(binary=binary)
    assert installed == {'binary': binary, 'guard_directory': str(directory)}
    assert Path(binary).stat().st_ino != previous
    assert Path(binary).stat().st_mode & 0o777 == 0o755
    assert config.verify(binary=binary, config=path) == {'guard': 'ready'}


def test_compile_failure_retains_existing_guard(configured, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    path, binary, directory, normal = configured
    before = Path(binary).read_bytes()
    original = config.run
    def run(args, **kwargs):
        if args[0] == '/usr/bin/cc':
            return SimpleNamespace(ok=False)
        return original(args, **kwargs)
    monkeypatch.setattr(config, 'run', run)
    with pytest.raises(ValidationError, match='Could not build'):
        config.install_binary(binary=binary)
    assert Path(binary).read_bytes() == before
    assert not list(Path(binary).parent.glob('.boron-mail-guard-*'))


def test_installer_refuses_changing_existing_guard_storage(configured, tmp_path, monkeypatch):
    from pathlib import Path
    path, binary, directory, normal = configured
    before = Path(binary).read_bytes()
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(tmp_path / 'different'))
    with pytest.raises(ValidationError, match='explicit storage-path migration'):
        config.install_binary(binary=binary)
    assert Path(binary).read_bytes() == before


def test_installer_refuses_symlink_executable(configured):
    from pathlib import Path
    path, binary, directory, normal = configured
    target = Path(binary).with_name('retained-binary')
    Path(binary).rename(target)
    Path(binary).symlink_to(target)
    with pytest.raises(ValidationError):
        config.install_binary(binary=binary)
    assert target.is_file() and Path(binary).is_symlink()
