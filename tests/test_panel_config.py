import socket
import tomllib

import pytest

from daemon import panel_config as panel
from daemon.configtx import StepResult
from daemon.procutil import ProcResult
from shared.config import settings
from shared.validation import ValidationError


def test_render_preserves_comments_and_unrelated_configuration():
    original = '# Site\napi_bind_port = 9443 # old listener\npanel_hostname = "panel.example.com"\n'
    rendered = panel.render_ports(original, 2222, 3333)
    assert '# Site' in rendered and '# old listener' in rendered
    assert tomllib.loads(rendered) == {'api_bind_port': 2222, 'api_customer_port': 3333, 'panel_hostname': 'panel.example.com'}


@pytest.mark.parametrize('port', [22, 65536, True, '2222', None])
def test_reject_invalid_ports(port):
    with pytest.raises(ValidationError):
        panel.render_ports('', 2222, port)


def test_reject_nested_config_instead_of_editing_wrong_key():
    with pytest.raises((ValidationError, TypeError)):
        panel.render_ports('[nested]\napi_bind_port = 9443\n', 2222, 3333)


def test_real_conflict_check_allows_current_ports_only():
    with socket.socket() as blocker:
        blocker.bind(('127.0.0.1', 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        panel.check_available('127.0.0.1', (port,), (port,))
        with pytest.raises(ValidationError, match='unavailable'):
            panel.check_available('127.0.0.1', (), (port,))


@pytest.fixture
def change(tmp_path, monkeypatch):
    path = tmp_path / 'boron.toml'
    original = 'api_bind_port = 9443\n# Preserve me\npanel_hostname = "panel.example.com"\n'
    path.write_text(original)
    path.chmod(0o640)
    monkeypatch.setattr(settings, 'backup_dir', str(tmp_path / 'backup'))
    monkeypatch.setattr(settings, 'api_bind_port', 9443)
    monkeypatch.setattr(settings, 'api_customer_port', None)
    monkeypatch.setattr(panel, 'check_available', lambda *a: None)
    events = []
    def command(args, **kwargs):
        events.append(tuple(args))
        return ProcResult(args, 0, '', '')
    monkeypatch.setattr(panel, 'run', command)
    monkeypatch.setattr(panel, 'verify_listeners', lambda *a: StepResult(True))
    return path, original, events


def test_success_preserves_metadata_and_opens_ports_before_restart(change):
    path, original, events = change
    result = panel.apply_ports(2222, 3333, config_path=path)
    assert result['status'] == 'completed'
    assert events == [
        ('ufw', 'insert', '1', 'allow', '2222/tcp', 'comment', 'boron-panel'),
        ('ufw', 'insert', '1', 'allow', '3333/tcp', 'comment', 'boron-panel'),
        ('systemctl', 'restart', 'boron-api')]
    assert path.stat().st_mode & 0o777 == 0o640
    assert settings.api_bind_port == 2222 and settings.api_customer_port == 3333
    backups = list((path.parent / 'backup/panel-config/history').glob('boron.toml.*'))
    assert len(backups) == 1 and backups[0].read_text() == original
    assert (path.parent / 'backup/panel-config').stat().st_mode & 0o777 == 0o700


def test_failed_health_check_restores_exact_config_and_settings(change, monkeypatch):
    path, original, events = change
    verified = []
    def verify(host, ports, certificate):
        verified.append(ports)
        return StepResult(ports == (9443, 9443), 'test listener failure')
    monkeypatch.setattr(panel, 'verify_listeners', verify)
    with pytest.raises(RuntimeError, match='Previous listeners restored'):
        panel.apply_ports(2222, 3333, config_path=path)
    assert path.read_text() == original
    assert (settings.api_bind_port, settings.api_customer_port) == (9443, None)
    assert events.count(('systemctl', 'restart', 'boron-api')) == 2
    assert verified == [(2222, 3333), (9443, 9443)]


def test_restart_exception_is_rolled_back(change, monkeypatch):
    path, original, events = change
    def command(args, **kwargs):
        if args[0] == 'systemctl':
            raise TimeoutError('test')
        return ProcResult(args, 0, '', '')
    monkeypatch.setattr(panel, 'run', command)
    with pytest.raises(RuntimeError, match='Panel port change failed'):
        panel.apply_ports(2222, 2222, config_path=path)
    assert path.read_text() == original


def test_firewall_failure_does_not_change_config_or_restart(change, monkeypatch):
    path, original, events = change
    monkeypatch.setattr(panel, 'run', lambda args, **kwargs: ProcResult(args, 1, '', 'firewall unavailable'))
    with pytest.raises(RuntimeError, match='Open panel port'):
        panel.apply_ports(2222, 2222, config_path=path)
    assert path.read_text() == original
    assert not events


def test_same_ports_no_restart_or_firewall_change(change):
    path, original, events = change
    assert panel.apply_ports(9443, 9443, config_path=path)['status'] == 'unchanged'
    assert path.read_text() == original
    assert not events


def test_concurrent_change_is_rejected(change):
    import fcntl
    path, original, events = change
    directory = path.parent / 'backup/panel-config'
    directory.mkdir(parents=True)
    with (directory / 'change.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValidationError, match='Another panel'):
            panel.apply_ports(2222, 3333, config_path=path)
    assert path.read_text() == original and not events


@pytest.mark.parametrize('failure', [None, 'certificate', 'health'])
def test_real_tls_health_probe_pins_certificate(tmp_path, failure):
    import datetime as dt
    import http.server
    import ssl
    import threading
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'test.example')])
    now = dt.datetime.now(dt.timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).not_valid_before(now-dt.timedelta(days=1))
        .not_valid_after(now+dt.timedelta(days=1)))
    cert = builder.serial_number(1).sign(key, hashes.SHA256())
    cert_path = tmp_path / 'cert.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path = tmp_path / 'key.pem'
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    expected = tmp_path / 'expected.pem'
    expected.write_bytes((builder.serial_number(2).sign(key, hashes.SHA256()) if failure == 'certificate' else cert).public_bytes(serialization.Encoding.PEM))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == '/healthz'
            self.send_response(503 if failure == 'health' else 200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        result = panel.verify_listeners('0.0.0.0', (server.server_port,), expected, timeout=0)
        assert result.ok == (failure is None)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
