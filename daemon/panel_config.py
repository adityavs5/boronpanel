"""Transactional panel listener changes, called by the asynchronous admin worker.

Only ports are edited here. The caller must not hold an API request open while
this restarts the API. Existing firewall rules and unrelated TOML stay intact.
"""
from __future__ import annotations

import fcntl
import http.client
import json
import ipaddress
import os
from pathlib import Path
import re
import socket
import ssl
import time
import tomllib

from daemon.configtx import ConfigWriter, StepResult
from daemon.procutil import run
from shared.config import CONFIG_PATH, Settings, settings
from shared.validation import ValidationError
from shared.validation import validate_domain


def validate_ports(admin_port, customer_port):
    for port in (admin_port, customer_port):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValidationError('Panel ports must be integers between 1024 and 65535')
    return admin_port, customer_port


def render_ports(content, admin_port, customer_port):
    """Preserve operator comments and reject ambiguous/non-flat edits."""
    validate_ports(admin_port, customer_port)
    before = tomllib.loads(content)
    expected = dict(before, api_bind_port=admin_port, api_customer_port=customer_port)
    updated = content
    for key, value in (('api_bind_port', admin_port), ('api_customer_port', customer_port)):
        pattern = rf'(?m)^([ \t]*{key}[ \t]*=[ \t]*)[^\n#]*(.*)$'
        updated, count = re.subn(pattern, lambda m: f'{m[1]}{value} {m[2]}'.rstrip(), updated)
        if count == 0:
            # Insert at the beginning, outside any TOML table.
            updated = f'{key} = {value}\n' + updated
    if tomllib.loads(updated) != expected:
        raise ValidationError('Panel configuration is not flat TOML; port settings cannot be safely edited')
    Settings(**expected)  # Reject unknown configuration keys before restarting.
    return updated


def render_hostname(content: str, hostname: str) -> str:
    hostname = validate_domain(hostname)
    before = tomllib.loads(content)
    expected = dict(before, panel_hostname=hostname)
    pattern = r'(?m)^([ \t]*panel_hostname[ \t]*=[ \t]*)[^\n#]*(.*)$'
    updated, count = re.subn(pattern, lambda m: f'{m[1]}"{hostname}" {m[2]}'.rstrip(), content)
    if count == 0:
        updated = f'panel_hostname = "{hostname}"\n' + updated
    if tomllib.loads(updated) != expected:
        raise ValidationError('Panel configuration is not flat TOML; hostname cannot be safely edited')
    Settings(**expected)
    return updated


_SERVER_SETUP_KEYS = {
    "server_public_ip", "letsencrypt_email", "panel_hostname",
    "webmail_hostname", "webmail_url", "pma_hostname",
}


def render_setup_values(content: str, updates: dict[str, str]) -> str:
    """Safely edit the small flat-TOML subset owned by Server Setup."""
    unknown = set(updates) - _SERVER_SETUP_KEYS
    if unknown:
        raise ValidationError("Unsupported server setup setting: " + ", ".join(sorted(unknown)))
    before = tomllib.loads(content)
    expected = dict(before)
    expected.update(updates)
    updated = content
    for key, value in updates.items():
        encoded = json.dumps(value)
        pattern = rf'(?m)^([ \t]*{re.escape(key)}[ \t]*=[ \t]*)[^\n#]*(.*)$'
        updated, count = re.subn(pattern, lambda m, encoded=encoded: f'{m[1]}{encoded} {m[2]}'.rstrip(), updated)
        if count == 0:
            updated = f'{key} = {encoded}\n' + updated
    if tomllib.loads(updated) != expected:
        raise ValidationError("Panel configuration is not flat TOML; server setup values cannot be safely edited")
    Settings(**expected)
    return updated


def apply_setup_values(updates: dict[str, str], *, config_path=CONFIG_PATH, reload_services: bool = False) -> dict:
    """Atomically apply non-secret server identity/service-host settings."""
    clean = dict(updates)
    if "server_public_ip" in clean and clean["server_public_ip"]:
        try:
            ipaddress.ip_address(clean["server_public_ip"])
        except ValueError as exc:
            raise ValidationError("Server public IP is invalid") from exc
    for key in ("panel_hostname", "webmail_hostname", "pma_hostname"):
        if key in clean and clean[key]:
            clean[key] = validate_domain(clean[key])
    if "letsencrypt_email" in clean and clean["letsencrypt_email"]:
        from shared.validation import validate_email_address
        clean["letsencrypt_email"] = validate_email_address(clean["letsencrypt_email"])
    if clean.get("webmail_hostname"):
        clean["webmail_url"] = f'https://{clean["webmail_hostname"]}'
    hostnames = [clean.get(key, getattr(settings, key)) for key in ("panel_hostname", "webmail_hostname", "pma_hostname")]
    present = [item for item in hostnames if item]
    if len(set(present)) != len(present):
        raise ValidationError("Panel, webmail, and phpMyAdmin hostnames must be distinct")
    target = Path(config_path)
    if not target.is_file() or target.is_symlink():
        raise ValidationError("Panel configuration must be an existing regular file")
    original = target.read_text()
    updated = render_setup_values(original, clean)
    metadata = target.stat()

    def validate(path):
        Settings(**tomllib.loads(path.read_text()))
        os.chmod(path, metadata.st_mode & 0o777)
        os.chown(path, metadata.st_uid, metadata.st_gid)
        return StepResult(True)

    def reload():
        try:
            fresh = Settings(**tomllib.loads(target.read_text()))
            for key in clean:
                setattr(settings, key, getattr(fresh, key))
            if reload_services:
                from daemon import ols, panel_tls, pma
                panel_tls.bootstrap_challenge()
                if settings.webmail_hostname:
                    ols.bootstrap_webmail()
                if settings.pma_hostname:
                    pma.bootstrap_pma()
            return StepResult(True)
        except Exception as exc:
            return StepResult(False, f"Service hostname configuration failed: {type(exc).__name__}: {exc}")

    writer = ConfigWriter(
        str(target), validate=validate, reload=reload, verify=lambda: StepResult(True),
        backup_dir=settings.backup_dir, subsystem="server-setup",
    )
    result = writer.apply(updated)
    if not result.ok:
        raise RuntimeError("Server setup configuration failed; previous settings restored. " + result.summary())
    return {"status": "completed", "updated": sorted(clean)}


def apply_hostname(hostname: str, *, config_path=CONFIG_PATH) -> dict:
    """Atomically change the panel hostname and its dedicated ACME route."""
    from daemon import panel_tls

    hostname = validate_domain(hostname)
    target = Path(config_path)
    if not target.is_file() or target.is_symlink():
        raise ValidationError('Panel configuration must be an existing regular file')
    original = target.read_text()
    current = Settings(**tomllib.loads(original)).panel_hostname
    if hostname == current:
        return {'status': 'unchanged', 'hostname': hostname}
    updated = render_hostname(original, hostname)
    metadata = target.stat()

    def validate(path):
        Settings(**tomllib.loads(path.read_text()))
        os.chmod(path, metadata.st_mode & 0o777)
        os.chown(path, metadata.st_uid, metadata.st_gid)
        return StepResult(True)

    def reload_route():
        try:
            settings.panel_hostname = Settings(**tomllib.loads(target.read_text())).panel_hostname
            panel_tls.bootstrap_challenge()
            return StepResult(True)
        except Exception as exc:
            return StepResult(False, f'Panel hostname routing failed: {type(exc).__name__}: {exc}')

    writer = ConfigWriter(
        str(target), validate=validate, reload=reload_route,
        verify=lambda: StepResult(True), backup_dir=settings.backup_dir,
        subsystem='panel-hostname',
    )
    result = writer.apply(updated)
    if not result.ok:
        raise RuntimeError('Panel hostname change failed; previous hostname restored. ' + result.summary())
    return {'status': 'completed', 'hostname': hostname, 'previous_hostname': current}


def check_available(host, old_ports, new_ports):
    sockets = []
    try:
        for port in sorted(set(new_ports) - set(old_ports)):
            listener = socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(listener)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if listener.family == socket.AF_INET6:
                listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            try:
                listener.bind((host, port))
            except OSError as exc:
                raise ValidationError(f'Panel port {port} is unavailable: {exc.strerror}') from exc
    finally:
        for listener in sockets:
            listener.close()


def restart_api():
    try:
        result = run(['systemctl', 'restart', 'boron-api'], timeout=60)
        return StepResult(result.ok, '' if result.ok else 'API restart failed')
    except Exception as exc:
        return StepResult(False, f'API restart failed: {type(exc).__name__}')


def verify_listeners(host, ports, certificate, timeout=30):
    """Pin the already-installed certificate, including before trusted issuance."""
    host = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host)
    try:
        expected = ssl.PEM_cert_to_DER_cert(Path(certificate).read_text().split('-----END CERTIFICATE-----')[0] + '-----END CERTIFICATE-----\n')
    except Exception:
        return StepResult(False, 'Cannot read the installed panel certificate')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Exact certificate pin below replaces CA validation.
    deadline = time.monotonic() + timeout
    remaining = set(ports)
    while remaining:
        for port in tuple(remaining):
            connection = http.client.HTTPSConnection(host, port, context=context, timeout=2)
            try:
                connection.connect()
                if connection.sock.getpeercert(binary_form=True) != expected:
                    continue
                connection.request('GET', '/healthz')
                response = connection.getresponse()
                if response.status == 200 and json.loads(response.read(4096)) == {'status': 'ok'}:
                    remaining.remove(port)
            except (OSError, ValueError, http.client.HTTPException):
                pass
            finally:
                connection.close()
        if not remaining:
            return StepResult(True)
        if time.monotonic() >= deadline:
            return StepResult(False, 'Panel health check failed on port(s): ' + ', '.join(map(str, sorted(remaining))))
        time.sleep(.25)


def apply_ports(admin_port, customer_port, *, config_path=CONFIG_PATH,
                certificate='/etc/boron/ssl/api/panel.crt'):
    """Restart and verify both listeners; preserve original config on failure.

The private directory and flock serialize this writer. New firewall openings are
retained on failure to avoid removing rules another administrator may rely on;
no old rules are removed. Returned diagnostics contain no configuration content.
"""
    new_ports = validate_ports(admin_port, customer_port)
    private = Path(settings.backup_dir) / 'panel-config'
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    private.chmod(0o700)
    with (private / 'change.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValidationError('Another panel configuration change is running') from exc
        target = Path(config_path)
        if not target.is_file() or target.is_symlink():
            raise ValidationError('Panel configuration must be an existing regular file')
        original = target.read_text()
        config = Settings(**tomllib.loads(original))
        old_ports = validate_ports(config.api_bind_port, config.api_customer_port if config.api_customer_port is not None else config.api_bind_port)
        if old_ports == new_ports:
            return {'status': 'unchanged', 'admin_port': admin_port, 'customer_port': customer_port}
        updated = render_ports(original, *new_ports)
        check_available(config.api_bind_host, old_ports, new_ports)
        # Admission happens before the first restart. Do not disable UFW or remove
        # existing rules: the server can have operator-managed access policies.
        for port in sorted(set(new_ports) - set(old_ports)):
            result = run(['ufw', 'insert', '1', 'allow', f'{port}/tcp', 'comment', 'boron-panel'], timeout=20)
            result.raise_if_failed(f'Open panel port {port}')
        metadata = target.stat()

        def validate(path):
            if tomllib.loads(path.read_text()) != tomllib.loads(updated):
                return StepResult(False, 'Configuration validation failed')
            os.chmod(path, metadata.st_mode & 0o777)
            os.chown(path, metadata.st_uid, metadata.st_gid)
            return StepResult(True)

        writer = ConfigWriter(str(target), validate=validate, reload=restart_api,
            verify=lambda: verify_listeners(config.api_bind_host, new_ports, certificate),
            backup_dir=str(private), subsystem='history')
        result = writer.apply(updated)
        if not result.ok:
            recovery = verify_listeners(config.api_bind_host, old_ports, certificate) if result.rolled_back else StepResult(True)
            raise RuntimeError('Panel port change failed. ' + ('Previous listeners restored. ' if recovery.ok else 'Previous listeners need administrator attention. ') + result.summary())
        # The daemon survives the API restart; update its firewall protection and
        # other consumers of this same settings object only after verification.
        settings.api_bind_port, settings.api_customer_port = new_ports
        return {'status': 'completed', 'admin_port': admin_port, 'customer_port': customer_port,
                'previous_admin_port': old_ports[0], 'previous_customer_port': old_ports[1]}
