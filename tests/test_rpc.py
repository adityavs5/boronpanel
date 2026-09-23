import asyncio

import pytest

from shared.rpc import (
    RpcRequest,
    decode_frame_header,
    encode_response,
    read_frame,
)


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


def test_response_round_trip_ok():
    raw = encode_response(True, result={"status": "active"})
    frame = asyncio.run(_frame_from_bytes(raw))
    assert frame == {"ok": True, "result": {"status": "active"}}


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
