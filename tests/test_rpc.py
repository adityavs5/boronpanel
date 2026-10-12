import asyncio
import json

import pytest

from api import rpc as api_rpc
from api.security import Identity

from shared.rpc import (
    RPC_PROTOCOL_CAPABILITIES,
    RPC_PROTOCOL_VERSION,
    RpcClient,
    RpcError,
    RpcProtocolError,
    RpcRequest,
    decode_frame_header,
    encode_response,
    read_frame,
    validate_peer_protocol,
)
from shared.request_context import request_context


async def _frame_from_bytes(raw: bytes) -> dict:
    # StreamReader binds to "the" running event loop at construction time in
    # 3.12, so it must be built inside the coroutine asyncio.run() drives,
    # not reused/constructed across separate asyncio.run() calls.
    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    reader.feed_eof()
    return await read_frame(reader)


def test_request_round_trip_via_streamreader():
    req = RpcRequest(op="account.create", params={"username": "demo"},
                     credential={"type": "session", "value": "opaque-session"})
    raw = req.to_bytes()

    frame = asyncio.run(_frame_from_bytes(raw))
    assert frame["op"] == "account.create"
    assert frame["params"] == {"username": "demo"}
    assert frame["credential"] == {"type": "session", "value": "opaque-session"}
    assert frame["request_id"] == req.request_id
    assert frame["protocol"]["version"]
    assert "json-frame-v1" in frame["protocol"]["capabilities"]


def test_request_inherits_correlation_context():
    with request_context("upstream-123"):
        req = RpcRequest(op="health")
    assert req.request_id == "upstream-123"


def test_response_round_trip_ok():
    raw = encode_response(True, result={"status": "active"})
    frame = asyncio.run(_frame_from_bytes(raw))
    assert frame["ok"] is True
    assert frame["result"] == {"status": "active"}
    assert "json-frame-v1" in frame["protocol"]["capabilities"]


def test_response_round_trip_error():
    raw = encode_response(False, error_code="bad_request", error_message="nope")
    frame = asyncio.run(_frame_from_bytes(raw))
    assert frame["ok"] is False
    assert frame["error"]["code"] == "bad_request"


def test_oversized_frame_rejected():
    huge_len = (16 * 1024 * 1024) + 1
    header = huge_len.to_bytes(4, "big")
    with pytest.raises(ValueError):
        decode_frame_header(header)


def test_header_must_be_four_bytes():
    with pytest.raises(ValueError):
        decode_frame_header(b"\x00\x01")


def test_operation_credential_does_not_collide_with_rpc_authentication(monkeypatch):
    captured = {}

    class Client:
        def call(self, op, *, rpc_credential=None, **params):
            captured.update(op=op, rpc_credential=rpc_credential, params=params)
            return {"created": True}

    monkeypatch.setattr(api_rpc, "_client", Client())
    identity = Identity(1, "admin", "admin", None, "session",
                        rpc_credential="opaque-session", ip="192.0.2.10")
    result = api_rpc.call_daemon(
        "dnscluster.peer.create", identity,
        credential="directadmin-login-key", name="dns2",
    )

    assert result == {"created": True}
    assert captured == {
        "op": "dnscluster.peer.create",
        "rpc_credential": {"type": "session", "value": "opaque-session"},
        "params": {
            "_ip": "192.0.2.10",
            "credential": "directadmin-login-key",
            "name": "dns2",
        },
    }


def test_root_daemon_rejects_peer_without_kernel_credentials():
    from daemon.server import handle_client

    class Writer:
        closed = False

        def get_extra_info(self, key, default=None):
            return None

        def close(self):
            self.closed = True

    writer = Writer()
    asyncio.run(handle_client(None, writer))
    assert writer.closed


def test_python_provisioning_has_deadline_above_both_bounded_commands(monkeypatch):
    from api import rpc as api_rpc
    from api.security import Identity
    called = []
    class Client:
        def call(self, op, **params):
            called.append((op, params['rpc_credential']))
            return {'created': True}
    assert api_rpc._python_provision_client.timeout > 180 + 180
    monkeypatch.setattr(api_rpc, '_python_provision_client', Client())
    identity = Identity(1, 'admin', 'admin', None, 'session', rpc_credential='opaque-session')
    assert api_rpc.call_daemon('apps.python.create', identity, username='alpha') == {'created': True}
    assert called == [('apps.python.create', {'type': 'session', 'value': 'opaque-session'})]


def test_protocol_allows_one_minor_and_rejects_larger_mismatch():
    major, minor = map(int, RPC_PROTOCOL_VERSION.split('.'))
    compatible = {
        'version': f'{major}.{minor + 1}',
        'capabilities': list(RPC_PROTOCOL_CAPABILITIES),
    }
    assert validate_peer_protocol(
        compatible, local_role='API', peer_role='helper'
    )['version'] == compatible['version']

    incompatible = dict(compatible, version=f'{major}.{minor + 2}')
    with pytest.raises(RpcProtocolError, match='restart the helper'):
        validate_peer_protocol(incompatible, local_role='API', peer_role='helper')


def test_rpc_client_reports_legacy_helper_as_actionable_mismatch(monkeypatch):
    payload = json.dumps({'ok': True, 'result': {'status': 'ok'}}).encode()
    frame = len(payload).to_bytes(4, 'big') + payload

    class FakeSocket:
        def __init__(self):
            self.data = bytearray(frame)

        def settimeout(self, _timeout):
            pass

        def connect(self, _path):
            pass

        def sendall(self, _data):
            pass

        def recv(self, size):
            chunk = bytes(self.data[:size])
            del self.data[:size]
            return chunk

        def close(self):
            pass

    monkeypatch.setattr('shared.rpc.socket.socket', lambda *_args, **_kwargs: FakeSocket())
    with pytest.raises(RpcError) as raised:
        RpcClient('/unused').call('health')
    assert raised.value.code == 'incompatible_protocol'
    assert 'restart both panel services' in raised.value.message


def test_api_surfaces_protocol_mismatch_as_service_unavailable(monkeypatch):
    from fastapi import HTTPException

    class Client:
        def call(self, *_args, **_kwargs):
            raise RpcError(
                'incompatible_protocol',
                'API v3.2 and helper v3.0 are mixed; restart the helper',
            )

    monkeypatch.setattr(api_rpc, '_client', Client())
    identity = Identity(
        1, 'admin', 'admin', None, 'session', rpc_credential='opaque-session'
    )
    with pytest.raises(HTTPException) as raised:
        api_rpc.call_daemon('account.list', identity)
    assert raised.value.status_code == 503
    assert 'restart the helper' in raised.value.detail
