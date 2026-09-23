import pytest

from daemon import snapshot_mail_guard_config as config
from shared.config import settings
from shared.validation import ValidationError
from tests.test_mail_restore_gate import gate


@pytest.fixture
def configured(gate, tmp_path, monkeypatch):
    directory, lookup = gate
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(directory))
    binary = str(directory.parent / 'gate')
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
    import grp, os
    path, binary, directory, normal = configured
    other = tmp_path / 'other'
    other.mkdir(mode=0o710)
    other.chmod(0o710)
    os.chown(other, 0, grp.getgrnam('dovecot').gr_gid)
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


def test_guard_preflight_rejects_inaccessible_ancestor(configured):
    from pathlib import Path
    path, binary, directory, normal = configured
    parent = Path(binary).parent
    parent.chmod(0o700)
    try:
        with pytest.raises(ValidationError, match='cannot traverse'):
            config.verify(binary=binary, config=path)
    finally:
        parent.chmod(0o755)


def test_configuration_installer_preserves_original_and_is_idempotent(configured, tmp_path):
    from pathlib import Path
    path, binary, directory, normal = configured
    path.write_text('ssl = no\n' + normal)
    original = path.read_bytes()
    result = config.install_configuration(tmp_path / 'backups', config=path, binary=binary)
    assert (Path(result['backup']) / path.name).read_bytes() == original
    assert Path(result['backup']).stat().st_mode & 0o777 == 0o700
    assert config.verify(binary=binary, config=path) == {'guard': 'ready'}
    current = path.read_bytes()
    config.install_configuration(tmp_path / 'backups', config=path, binary=binary)
    assert path.read_bytes() == current
    assert current.count(b'!include ') == 1


def test_configuration_validation_failure_restores_original(configured, tmp_path):
    path, binary, directory, normal = configured
    path.write_text('ssl = no\nauth_cache_size = 1 M\n' + normal)
    original = path.read_bytes()
    with pytest.raises(ValidationError, match='caching'):
        config.install_configuration(tmp_path / 'backups', config=path, binary=binary)
    assert path.read_bytes() == original
    assert not path.with_name('boron-restore-guard.conf.ext').exists()


def test_configuration_installer_refuses_unmanaged_fragment(configured, tmp_path):
    path, binary, directory, normal = configured
    fragment = path.with_name('boron-restore-guard.conf.ext')
    fragment.write_text('# Custom administrator configuration\n')
    original = path.read_bytes()
    with pytest.raises(ValidationError, match='unmanaged'):
        config.install_configuration(tmp_path / 'backups', config=path, binary=binary)
    assert path.read_bytes() == original
    assert fragment.read_text() == '# Custom administrator configuration\n'


def test_failed_post_reload_health_check_rolls_back(configured, tmp_path, monkeypatch):
    from types import SimpleNamespace
    path, binary, directory, normal = configured
    path.write_text('ssl = no\n' + normal)
    before = path.read_bytes()
    original = config.run
    reloads = []
    def run(args, **kwargs):
        if args[0] == '/usr/bin/doveadm':
            reloads.append(args)
            return SimpleNamespace(ok=True)
        return original(args, **kwargs)
    monkeypatch.setattr(config, 'run', run)
    def unhealthy():
        raise ValidationError('health probe failed')
    with pytest.raises(ValidationError, match='health probe'):
        config.install_configuration(tmp_path / 'backups', config=path, binary=binary,
                                     reload=True, health_check=unhealthy)
    assert len(reloads) == 2
    assert path.read_bytes() == before
    assert not path.with_name('boron-restore-guard.conf.ext').exists()


def test_post_reload_health_waits_for_running_state(monkeypatch):
    states = iter([
        {'ActiveState': 'reloading', 'SubState': 'reload'},
        {'ActiveState': 'active', 'SubState': 'reload'},
        {'ActiveState': 'active', 'SubState': 'running'},
    ])
    sleeps = []
    monkeypatch.setattr(config.time, 'sleep', lambda seconds: sleeps.append(seconds))
    config._require_mail_running(lambda name: next(states), wait_for_reload=True)
    assert sleeps == [0.25, 0.25]


def test_initial_health_does_not_wait_for_reload_state(monkeypatch):
    monkeypatch.setattr(config.time, 'sleep', lambda seconds: pytest.fail('unexpected wait'))
    with pytest.raises(ValidationError, match='must be running'):
        config._require_mail_running(
            lambda name: {'ActiveState': 'reloading', 'SubState': 'reload'})


@pytest.mark.parametrize('running', [True, False])
def test_update_entrypoint_requires_running_mail_and_checks_after_reload(monkeypatch, tmp_path, running):
    import sys
    from daemon import snapshot_mail_service as service
    calls = []
    monkeypatch.setattr(sys, 'argv', ['guard', '--backup-dir', str(tmp_path), '--reload'])
    def status(name):
        calls.append('health')
        return {'ActiveState': 'active' if running else 'inactive', 'SubState': 'running' if running else 'dead'}
    monkeypatch.setattr(service, 'service_status', status)
    monkeypatch.setattr(config, 'install_binary', lambda: calls.append('binary'))
    def install(path, **kwargs):
        assert path == str(tmp_path)
        assert kwargs['reload'] is True
        calls.append('configuration')
        kwargs['health_check']()
    monkeypatch.setattr(config, 'install_configuration', install)
    if running:
        config.main()
        assert calls == ['health', 'binary', 'configuration', 'health']
    else:
        with pytest.raises(SystemExit) as error:
            config.main()
        assert error.value.code == 1
        assert calls == ['health']
