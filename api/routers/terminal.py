"""Phase 8 feature 7: web terminal — the API (unprivileged) side.

WebSocket `/ws/accounts/{u}/terminal`. On connect it authenticates the panel
session + ownership, asks borond to open an ephemeral SSH session (which
injects a one-time public key into the account's authorized_keys and returns the
matching PRIVATE key), then connects to sshd on 127.0.0.1 AS THE ACCOUNT USER
(never root) with that key -- held in memory only, never written to disk -- and
pumps bytes between the browser's xterm.js and the SSH pty. On close/disconnect/
idle-timeout it tells borond to remove the injected key.

Client protocol (JSON text frames):
  {"t":"i","d":"<keystrokes>"}   input
  {"t":"r","c":<cols>,"r":<rows>} resize
Server sends raw terminal output as text frames.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import paramiko
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi import HTTPException

from fastapi import Depends
from api.rpc import call_daemon
from api.security import COOKIE_NAME, Identity, _identity_from_session_cookie, get_identity, require_account_access

logger = logging.getLogger("boron-api.terminal")

router = APIRouter()
http_router = APIRouter(prefix="/api/v1/accounts/{username}/terminal", tags=["terminal"])


@http_router.get("/sessions")
def terminal_sessions(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("terminal.list", identity, username=username)

IDLE_TIMEOUT_SECONDS = 30 * 60  # 30-minute idle timeout (per goal)
_RECV_TIMEOUT = 0.2
MAX_INPUT_CHARS = 16 * 1024
MAX_RESIZE_COLUMNS = 300
MAX_RESIZE_ROWS = 120
# Terminal I/O blocks a thread while recv()'ing; a dedicated pool keeps that off
# the request-serving executors.
_TERMINAL_IO = ThreadPoolExecutor(max_workers=64, thread_name_prefix="terminal-io")


def _bounded_terminal_input(value: object) -> str:
    return str(value)[:MAX_INPUT_CHARS]


def parse_client_message(text: str):
    """Pure parser for a client frame. Returns ('input', data) |
    ('resize', cols, rows) | ('ignore',). Malformed JSON is treated as raw
    input so a plain xterm attach still works."""
    try:
        msg = json.loads(text)
    except (ValueError, TypeError):
        return ("input", _bounded_terminal_input(text))
    if not isinstance(msg, dict):
        return ("input", _bounded_terminal_input(text))
    t = msg.get("t")
    if t == "i":
        return ("input", _bounded_terminal_input(msg.get("d", "")))
    if t == "r":
        try:
            cols = min(MAX_RESIZE_COLUMNS, max(1, int(msg.get("c", 80))))
            rows = min(MAX_RESIZE_ROWS, max(1, int(msg.get("r", 24))))
            return ("resize", cols, rows)
        except (TypeError, ValueError):
            return ("ignore",)
    return ("ignore",)


def _ws_identity(websocket: WebSocket) -> Identity | None:
    cookie = websocket.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    return _identity_from_session_cookie(cookie)


def _same_origin(websocket: WebSocket) -> bool:
    """A browser terminal may only be opened by this panel's own origin.

    WebSocket handshakes carry cookies, so a hostile page on a sibling hosted
    domain could otherwise drive an authenticated customer's shell. Browsers
    always send Origin for WebSockets; absence fails closed as well.
    """
    origin = websocket.headers.get("origin")
    scheme = "https" if websocket.url.scheme == "wss" else "http"
    expected = f"{scheme}://{websocket.url.netloc}"
    return origin == expected


def _authorized(identity: Identity, username: str) -> bool:
    """Admin may open a terminal for any account; a customer only for their own.
    Either way the SSH login is as the account user, never root."""
    if identity.role == "admin":
        return True
    if identity.account_id is None:
        return False
    from shared.db import read_session
    from shared.models import Account

    with read_session() as db:
        account = db.get(Account, identity.account_id)
        return account is not None and account.username == username


def _connect_ssh(username: str, private_key_pem: str, host: str, port: int, quiet=False):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key_pem))
    client.connect(
        host, port=port, username=username, pkey=pkey, timeout=10,
        allow_agent=False, look_for_keys=False,
    )
    if quiet:
        chan=client.get_transport().open_session()
        chan.get_pty(term="xterm-256color",width=80,height=24)
        # An exec session skips SSH's login MOTD. Bash stays interactive for
        # editing/job control, without loading login or interactive rc files.
        chan.exec_command("exec /bin/bash --noprofile --norc -i")
    else:
        chan = client.invoke_shell(term="xterm-256color", width=80, height=24)
    chan.settimeout(_RECV_TIMEOUT)
    return client, chan


def _read_chan(chan) -> str | None:
    """Blocking (up to _RECV_TIMEOUT) read. '' = nothing this interval, None =
    channel closed."""
    import socket

    try:
        data = chan.recv(4096)
        if not data:
            return None
        return data.decode("utf-8", errors="replace")
    except socket.timeout:
        return ""
    except Exception:
        return None


@router.websocket("/ws/accounts/{username}/terminal")
async def terminal_ws(websocket: WebSocket, username: str):
    if not _same_origin(websocket):
        await websocket.close(code=4403)
        return
    identity = _ws_identity(websocket)
    if identity is None:
        await websocket.close(code=4401)  # unauthenticated
        return
    from api.security import enforce_listener_role
    try:enforce_listener_role(identity,websocket)
    except HTTPException:
        await websocket.close(code=4403)
        return
    if not _authorized(identity, username):
        await websocket.close(code=4403)  # forbidden
        return

    await websocket.accept()
    loop = asyncio.get_event_loop()

    # 1) Open the ephemeral SSH session via the daemon (audit-logged there).
    try:
        session = await loop.run_in_executor(None, lambda: call_daemon("terminal.open", identity, username=username))
    except HTTPException as exc:
        await websocket.send_text(f"\r\n\x1b[31m[could not open terminal: {exc.detail}]\x1b[0m\r\n")
        await websocket.close()
        return

    session_id = session["session_id"]
    private_key = session["private_key"]

    def _close_daemon_session():
        try:
            call_daemon("terminal.close", identity, username=username, session_id=session_id)
        except Exception:
            logger.warning("failed to close terminal session %s for %s", session_id, username)

    # 2) Connect to sshd as the account user with the in-memory ephemeral key.
    try:
        client, chan = await loop.run_in_executor(
            _TERMINAL_IO, lambda: _connect_ssh(username, private_key, session["host"], session["port"], quiet=True)
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("terminal SSH connect failed for %s: %s", username, exc)
        await websocket.send_text(f"\r\n\x1b[31m[SSH connection failed: {exc}]\x1b[0m\r\n")
        await loop.run_in_executor(None, _close_daemon_session)
        await websocket.close()
        return

    try:
        if identity.role=="admin":
            from shared.db import read_session
            from shared.models import BrandingSettings
            from shared.terminal_welcome import render_terminal_banner
            with read_session() as db:
                branding=db.get(BrandingSettings,1)
                banner=render_terminal_banner(branding.terminal_banner if branding else None)
            if banner:await websocket.send_text(banner)
        await _pump(websocket, chan, loop)
    finally:
        try:
            chan.close()
            client.close()
        except Exception:
            pass
        await loop.run_in_executor(None, _close_daemon_session)
        try:
            await websocket.close()
        except Exception:
            pass


async def _pump(websocket: WebSocket, chan, loop):
    stop = asyncio.Event()

    async def ssh_to_ws():
        while not stop.is_set():
            data = await loop.run_in_executor(_TERMINAL_IO, lambda: _read_chan(chan))
            if data is None:
                break  # channel closed
            if data:
                try:
                    await websocket.send_text(data)
                except Exception:
                    break
        stop.set()

    async def ws_to_ssh():
        while not stop.is_set():
            try:
                text = await asyncio.wait_for(websocket.receive_text(), timeout=IDLE_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                try:
                    await websocket.send_text("\r\n\x1b[33m[session closed after 30 minutes idle]\x1b[0m\r\n")
                except Exception:
                    pass
                break
            except WebSocketDisconnect:
                break
            except Exception:
                break
            kind, *rest = parse_client_message(text)
            try:
                if kind == "input":
                    chan.send(rest[0])
                elif kind == "resize":
                    chan.resize_pty(width=rest[0], height=rest[1])
            except Exception:
                break
        stop.set()

    reader = asyncio.create_task(ssh_to_ws())
    writer = asyncio.create_task(ws_to_ssh())
    await asyncio.wait({reader, writer}, return_when=asyncio.FIRST_COMPLETED)
    stop.set()
    for task in (reader, writer):
        if not task.done():
            task.cancel()
    await asyncio.gather(reader, writer, return_exceptions=True)
