from pathlib import Path
import pytest
from scripts import reconcile_powerdns_credentials as repair


@pytest.fixture
def config(tmp_path):
    pdns = tmp_path / 'pdns.conf'; pdns.write_text('api=yes\napi-key=old-key\n'); pdns.chmod(0o644)
    conf = tmp_path / 'boron'; (conf / 'ssl').mkdir(parents=True)
    (conf / 'secrets.env').write_text('SESSION_SECRET=unchanged\nPOWERDNS_API_KEY=old-key\n')
    (conf / 'ssl/powerdns-credentials.ini').write_text('dns_powerdns_api_key=old-key\n')
    return pdns, conf


def test_rotation_updates_all_consumers_privately_and_checks_dns(config):
    pdns, conf = config; calls = []; keys = []
    assert repair.reconcile(pdns, conf, gid=0, service=lambda *a: calls.append(a), healthy=keys.append)
    assert len(keys) == 1 and keys[0] != 'old-key'
    assert calls == [('restart', 'pdns'), ('try-restart', 'boron-provisiond')]
    for path in (pdns, conf / 'secrets.env', conf / 'ssl/powerdns-credentials.ini'):
        assert keys[0] in path.read_text() and 'old-key' not in path.read_text()
        assert path.stat().st_mode & 0o007 == 0
    assert 'SESSION_SECRET=unchanged' in (conf / 'secrets.env').read_text()


def test_failed_dns_check_restores_values_but_not_unsafe_permissions(config):
    pdns, conf = config
    def failed(_): raise RuntimeError('API unavailable')
    with pytest.raises(RuntimeError, match='restored'):
        repair.reconcile(pdns, conf, gid=0, service=lambda *a: None, healthy=failed)
    for path in (pdns, conf / 'secrets.env', conf / 'ssl/powerdns-credentials.ini'):
        assert 'old-key' in path.read_text() and path.stat().st_mode & 0o007 == 0


def test_safe_existing_config_does_not_restart_services(config):
    pdns, conf = config
    pdns.chmod(0o640); (conf / 'secrets.env').chmod(0o600); (conf / 'ssl/powerdns-credentials.ini').chmod(0o600)
    assert not repair.reconcile(pdns, conf, gid=0, service=lambda *a: pytest.fail('unneeded restart'))


def test_failed_rotation_is_retried_even_after_permissions_are_repaired(config):
    pdns, conf = config
    def failed(_): raise RuntimeError('transient DNS health failure')
    with pytest.raises(RuntimeError):
        repair.reconcile(pdns, conf, gid=0, service=lambda *a: None, healthy=failed)
    assert pdns.with_name('.boron-key-rotation-pending').exists()
    keys = []
    assert repair.reconcile(pdns, conf, gid=0, service=lambda *a: None, healthy=keys.append)
    assert keys and keys[0] != 'old-key'
    assert not pdns.with_name('.boron-key-rotation-pending').exists()


@pytest.mark.parametrize('existing_credentials', [False, True])
def test_fresh_installer_never_writes_keys_to_world_readable_files(tmp_path, existing_credentials):
    import shlex
    import subprocess
    script = (Path(__file__).resolve().parents[1] / 'scripts/install.sh').read_text()
    function = script[script.index('setup_powerdns() {'):script.index('setup_ssl_bootstrap() {')]
    pdns = tmp_path / 'pdns'; (pdns / 'pdns.d').mkdir(parents=True)
    data = tmp_path / 'data'; data.mkdir(); (data / 'pdns.sqlite3').write_bytes(b'fixture')
    conf = tmp_path / 'conf'; (conf / 'ssl').mkdir(parents=True)
    if existing_credentials:
        credentials = conf / 'ssl/powerdns-credentials.ini'
        credentials.write_text('old fixture'); credentials.chmod(0o644)
    function = function.replace('/etc/powerdns', str(pdns)).replace('/var/lib/powerdns', str(data))
    # Check the destination mode at the moment cat starts, before writing a key.
    harness = '''set -euo pipefail
umask 022
DRY_RUN=false
info() { :; }; ok() { :; }; skip() { :; }
_rand_hex() { printf 'test-only-key'; }
systemctl() { :; }; chown() { :; }
cat() {
    local writer_pid=$BASHPID
    local perms
    perms=$(stat -Lc '%a' "/proc/${writer_pid}/fd/1")
    [[ "$perms" == 640 || "$perms" == 600 ]] || exit 77
    /bin/cat
}
'''
    subprocess.run(['bash', '-c', harness + '\nCONF_DIR=' + shlex.quote(str(conf)) + '\n' + function + '\nsetup_powerdns\n'], check=True, capture_output=True, text=True, timeout=10)
    assert (pdns / 'pdns.d/boron.conf').stat().st_mode & 0o777 == 0o640
    assert (conf / 'ssl/powerdns-credentials.ini').stat().st_mode & 0o777 == 0o600
    assert (conf / 'secrets.env').stat().st_mode & 0o777 == 0o600
