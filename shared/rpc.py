"""Length-prefixed JSON RPC framing over the forgehostd Unix socket.

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
import uuid
from dataclasses import dataclass, field
from typing import Any

_LEN_STRUCT = struct.Struct(">I")
MAX_FRAME_BYTES = 16 * 1024 * 1024


class RpcError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass
class RpcRequest:
    op: str
    params: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_bytes(self) -> bytes:
        payload = json.dumps(
            {"op": self.op, "params": self.params, "request_id": self.request_id}
        ).encode("utf-8")
        return _LEN_STRUCT.pack(len(payload)) + payload


def encode_response(ok: bool, result: Any = None, error_code: str = "", error_message: str = "") -> bytes:
    if ok:
        body = {"ok": True, "result": result}
    else:
        body = {"ok": False, "error": {"code": error_code, "message": error_message}}
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
    """Synchronous client used by forgehost-api to call forgehostd."""

    def __init__(self, socket_path: str, timeout: float = 30.0):
        self.socket_path = socket_path
        self.timeout = timeout

    def call(self, op: str, **params: Any) -> Any:
        req = RpcRequest(op=op, params=params)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
            sock.sendall(req.to_bytes())
            response = read_frame_sync(sock)
        finally:
            sock.close()

        if response.get("ok"):
            return response.get("result")
        error = response.get("error", {})
        raise RpcError(error.get("code", "unknown"), error.get("message", "unknown error"))
