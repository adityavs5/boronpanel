"""Compile the actual Dovecot guard and exercise its fd-3 protocol."""
import os
import hashlib
from pathlib import Path
import shutil
import smtplib
import socket
import subprocess
import time

import pytest


@pytest.fixture
def gate(tmp_path):
    if os.geteuid() != 0 or not shutil.which('cc'):
        pytest.skip('Root and a C compiler required for the service-owned gate')
    directory = tmp_path / 'gates'
    directory.mkdir(mode=0o700)
    binary = tmp_path / 'gate'
    source = Path(__file__).parents[1] / 'daemon/mail_restore_gate.c'
    compiled = subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                               f'-DBORON_MAIL_RESTORE_GATES="{directory}"', str(source), '-o', str(binary), '-lcrypto'],
                              capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stderr

    def lookup(user=b'inbox@example.test', authorized='1'):
        payload = tmp_path / 'request'
        payload.write_bytes(user + b'\0synthetic-unused-password\0')
        environment = {'PATH': '/usr/bin:/bin', 'AUTHORIZED': authorized}
        result = subprocess.run(['/bin/sh', '-c', 'exec 3<"$1"; exec "$2"',
                                 'gate-test', str(payload), str(binary)],
                                env=environment, capture_output=True, timeout=5)
        assert result.stdout == b'' and result.stderr == b''
        return result.returncode
    return directory, lookup


def marker(directory, user='inbox@example.test'):
    return directory / hashlib.sha256(user.lower().encode()).hexdigest()


def test_unblocked_lookup_delegates_to_real_userdb(gate):
    directory, lookup = gate
    assert lookup() == 3


def test_long_supported_address_uses_fixed_length_marker(gate):
    directory, lookup = gate
    user = 'a' * 64 + '@' + '.'.join(['b' * 60] * 4) + '.test'
    assert len(user) > 255
    assert lookup(user.encode()) == 3
    marker(directory, user).touch()
    assert lookup(user.encode()) == 111


def test_block_is_temporary_and_scoped_to_exact_mailbox(gate):
    directory, lookup = gate
    marker(directory).touch()
    assert lookup() == 111
    assert lookup(b'INBOX@EXAMPLE.TEST') == 111
    assert lookup(b'other@example.test') == 3
    assert lookup(b'inbox@other.test') == 3
    marker(directory).unlink()
    assert lookup() == 3


@pytest.mark.parametrize('user', [b'../etc/passwd', b'@example.test', b'one@two@three',
                                 b'inbox@', b'line\nbreak@example.test', b'a'*320+b'@example.test'])
def test_malformed_names_temporarily_fail(gate, user):
    assert gate[1](user) == 111


def test_helper_cannot_authenticate_passwords(gate):
    assert gate[1](authorized='') == 111
    assert gate[1](authorized='2') == 111


@pytest.mark.parametrize('kind', ['missing', 'writable', 'symlink'])
def test_invalid_gate_directory_fails_closed(gate, kind, tmp_path):
    directory, lookup = gate
    if kind == 'missing':
        directory.rmdir()
    elif kind == 'writable':
        directory.chmod(0o777)
    else:
        directory.rmdir()
        directory.symlink_to(tmp_path, target_is_directory=True)
    assert lookup() == 111


def test_symlink_marker_blocks_without_following_target(gate):
    directory, lookup = gate
    marker(directory).symlink_to('/does/not/exist')
    assert lookup() == 111


def test_real_dovecot_userdb_delegation_and_temporary_failure(gate, tmp_path, monkeypatch):
    if not shutil.which('dovecot'):
        pytest.skip('Dovecot required')
    directory, lookup = gate
    from daemon import snapshot_mail_guard as guard
    from shared.config import settings
    monkeypatch.setattr(settings, 'mail_restore_guard_dir', str(directory))
    root = tmp_path
    root.chmod(0o755)
    for name in ('run', 'state', 'home'):
        (root / name).mkdir()
    config = root / 'dovecot.conf'
    config.write_text(
        f'base_dir = {root}/run\nstate_dir = {root}/state\n'
        f'log_path = {root}/dovecot.log\nprotocols = lmtp\nssl = no\n'
        f'mail_location = maildir:{root}/home/Maildir\n'
        'service lmtp {\n'
        ' unix_listener lmtp {\n mode = 0600\n user = root\n }\n}\n'
        'first_valid_uid = 1\nauth_cache_size = 0\nlmtp_user_concurrency_limit = 10\n'
        'service auth {\n user = root\n}\n'
        'service auth-worker {\n user = root\n}\n'
        'userdb {\n driver = checkpassword\n'
        f' args = {root}/gate\n result_failure = continue\n'
        ' result_internalfail = return-fail\n}\n'
        'userdb {\n driver = static\n'
        f' args = uid=65534 gid=65534 home={root}/home allow_all_users=yes\n}}\n'
        'passdb {\n driver = static\n args = password=synthetic-test-only\n}\n'
    )
    checked = subprocess.run(['doveconf', '-c', str(config), '-n'],
                             capture_output=True, text=True, timeout=10)
    assert checked.returncode == 0, checked.stderr
    process = subprocess.Popen(['dovecot', '-F', '-c', str(config)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail((root / 'dovecot.log').read_text())
            if (root / 'run/auth-userdb').exists():
                break
            time.sleep(.05)
        else:
            pytest.fail('Isolated Dovecot auth socket did not appear')

        def user(address):
            return subprocess.run(['doveadm', '-c', str(config), 'user', address],
                                  capture_output=True, text=True, timeout=10)
        assert user('inbox@example.test').returncode == 0
        def deliveries():
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(5)
                connection.connect(str(root / 'run/anvil'))
                connection.sendall(b'VERSION\tanvil\t1\t0\nLOOKUP\tlmtp/inbox@example.test\n')
                with connection.makefile('rb') as stream:
                    return int(stream.readline().strip())

        # A recipient accepted before the guard remains tracked until RSET.
        # Merely creating the marker is therefore insufficient for switching.
        with smtplib.LMTP(str(root / 'run/lmtp'), timeout=10) as existing:
            assert existing.ehlo()[0] == 250
            assert existing.mail('sender@example.test')[0] == 250
            assert existing.rcpt('inbox@example.test')[0] == 250
            token = guard.block('example.test', 'inbox', 1)
            blocked = user('inbox@example.test')
            assert blocked.returncode != 0
            assert 'auth user lookup failed' in blocked.stderr.lower(), blocked.stderr
            assert deliveries() == 1
            # Only recipient negotiation: no DATA command or message is sent.
            with smtplib.LMTP(str(root / 'run/lmtp'), timeout=10) as client:
                assert client.ehlo()[0] == 250
                assert client.mail('sender@example.test')[0] == 250
                code, response = client.rcpt('inbox@example.test')
                assert code == 451, (code, response)
                assert client.rcpt('other@example.test')[0] == 250
            assert existing.rset()[0] == 250
            for _ in range(50):
                if deliveries() == 0:
                    break
                time.sleep(.02)
            assert deliveries() == 0
        assert user('other@example.test').returncode == 0
        guard.release('example.test', 'inbox', 1, token)
        assert user('inbox@example.test').returncode == 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
