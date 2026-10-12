"""Length-prefixed JSON RPC framing over the borond Unix socket.

See ARCHITECTURE.md SS2 for why this is a tiny custom protocol rather than
gRPC/HTTP: it's a closed, same-host link between two processes we control on
both ends, and the framing is simple enough to unit test as plain
struct.pack/json.dumps round trips.

Wire format, both directions: 4-byte big-endian length prefix, then that many
UTF-8 JSON bytes.
"""
from __future__ import annotations

import asyncio
import json
import socket
import struct
import re
from dataclasses import dataclass, field
from typing import Any

from shared.request_context import current_request_id, new_request_id, normalize_request_id
from version import BORON_VERSION

_LEN_STRUCT = struct.Struct(">I")
MAX_FRAME_BYTES = 16 * 1024 * 1024
RPC_PROTOCOL_VERSION = ".".join(BORON_VERSION.split(".")[:2])
RPC_PROTOCOL_CAPABILITIES = (
    "json-frame-v1",
    "request-id-v1",
    "error-diagnostic-v1",
    "principal-credential-v1",
)
_PROTOCOL_RE = re.compile(r"^(\d+)\.(\d+)$")


class RpcError(Exception):
    def __init__(self, code: str, message: str, diagnostic: dict | None = None):
        self.code = code
        self.message = message
        self.diagnostic = diagnostic
        super().__init__(f"{code}: {message}")


class RpcProtocolError(ValueError):
    pass


def protocol_envelope() -> dict:
    return {
        "version": RPC_PROTOCOL_VERSION,
        "capabilities": list(RPC_PROTOCOL_CAPABILITIES),
    }


def validate_peer_protocol(
    envelope: object,
    *,
    local_role: str,
    peer_role: str,
) -> dict:
    """Require the same major and allow a one-minor rolling restart window."""
    local_match = _PROTOCOL_RE.fullmatch(RPC_PROTOCOL_VERSION)
    if local_match is None:  # pragma: no cover - version.py is release-validated
        raise RuntimeError("local RPC protocol version is invalid")
    local = tuple(map(int, local_match.groups()))
    if not isinstance(envelope, dict):
        raise RpcProtocolError(
            f"{local_role} v{RPC_PROTOCOL_VERSION} and {peer_role} legacy are mixed; "
            "restart both panel services"
        )
    raw_version = envelope.get("version")
    match = _PROTOCOL_RE.fullmatch(str(raw_version or ""))
    if match is None:
        raise RpcProtocolError(
            f"{local_role} v{RPC_PROTOCOL_VERSION} received an invalid {peer_role} protocol; "
            "restart both panel services"
        )
    peer = tuple(map(int, match.groups()))
    if peer[0] != local[0] or abs(peer[1] - local[1]) > 1:
        raise RpcProtocolError(
            f"{local_role} v{RPC_PROTOCOL_VERSION} and {peer_role} v{raw_version} are mixed; "
            "restart the helper after installing matching panel versions"
        )
    capabilities = envelope.get("capabilities")
    if (
        not isinstance(capabilities, list)
        or any(not isinstance(item, str) for item in capabilities)
        or "json-frame-v1" not in capabilities
    ):
        raise RpcProtocolError(
            f"{peer_role} v{raw_version} lacks the required RPC capability; "
            "restart the helper after installing matching panel versions"
        )
    return {"version": str(raw_version), "capabilities": tuple(capabilities)}


@dataclass
class RpcRequest:
    op: str
    params: dict[str, Any] = field(default_factory=dict)
    credential: dict[str, str] | None = None
    request_id: str = field(default_factory=lambda: current_request_id() or new_request_id())

    def __post_init__(self) -> None:
        self.request_id = normalize_request_id(self.request_id) or new_request_id()

    def to_bytes(self) -> bytes:
        payload = json.dumps(
            {"op": self.op, "params": self.params, "credential": self.credential,
             "request_id": self.request_id, "protocol": protocol_envelope()}
        ).encode("utf-8")
        return _LEN_STRUCT.pack(len(payload)) + payload


def encode_response(ok: bool, result: Any = None, error_code: str = "", error_message: str = "", diagnostic: dict | None = None) -> bytes:
    if ok:
        body = {"ok": True, "result": result, "protocol": protocol_envelope()}
    else:
        body = {
            "ok": False,
            "error": {"code": error_code, "message": error_message},
            "protocol": protocol_envelope(),
        }
        if diagnostic:
            body['error']['diagnostic'] = diagnostic
    payload = json.dumps(body).encode("utf-8")
    return _LEN_STRUCT.pack(len(payload)) + payload


def decode_frame_header(header: bytes) -> int:
    if len(header) != 4:
        raise ValueError("frame header must be exactly 4 bytes")
    (length,) = _LEN_STRUCT.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise ValueError(f"frame of {length} bytes exceeds max {MAX_FRAME_BYTES}")
    return length


async def read_frame(reader: asyncio.StreamReader) -> dict:
    header = await reader.readexactly(4)
    length = decode_frame_header(header)
    payload = await reader.readexactly(length)
    return json.loads(payload.decode("utf-8"))


def read_frame_sync(sock: socket.socket) -> dict:
    header = _recv_exact(sock, 4)
    length = decode_frame_header(header)
    payload = _recv_exact(sock, length)
    return json.loads(payload.decode("utf-8"))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("socket closed while reading frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class RpcClient:
    """Synchronous client used by boron-api to call borond."""

    def __init__(self, socket_path: str, timeout: float = 30.0):
        self.socket_path = socket_path
        self.timeout = timeout

    def call(self, op: str, *, rpc_credential: dict[str, str] | None = None,
             request_id: str | None = None, **params: Any) -> Any:
        # Keep envelope authentication under a name that cannot collide with
        # an operation's own data. DNS-cluster peers, for example, legitimately
        # carry a parameter named ``credential`` for the remote server.
        req = RpcRequest(op=op, params=params, credential=rpc_credential,
                         request_id=request_id or current_request_id() or new_request_id())
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
            sock.sendall(req.to_bytes())
            response = read_frame_sync(sock)
        finally:
            sock.close()

        try:
            validate_peer_protocol(response.get("protocol"), local_role="API", peer_role="helper")
        except RpcProtocolError as exc:
            raise RpcError("incompatible_protocol", str(exc)) from exc

        if response.get("ok"):
            return response.get("result")
        error = response.get("error", {})
        raise RpcError(error.get("code", "unknown"), error.get("message", "unknown error"), error.get('diagnostic'))
