#!/usr/bin/python3
"""One-connection SMTP frontend for the Boron account submission socket."""
from __future__ import annotations

import os
from pathlib import Path
import re
import socket
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from daemon import mail_submission  # noqa: E402

COMMAND_BYTES = 2048
MAIL_RE = re.compile(r"^FROM:\s*<([^>]*)>(?:\s+.*)?$", re.I)
RCPT_RE = re.compile(r"^TO:\s*<([^>]+)>(?:\s+.*)?$", re.I)


def main() -> int:
    sock = socket.socket(fileno=0)
    sock.settimeout(60)
    _pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    stream = sock.makefile("rwb", buffering=0)

    def reply(code: int, message: str) -> None:
        stream.write(f"{code} {message}\r\n".encode("ascii", errors="replace"))

    envelope = None
    recipients: list[str] = []
    reply(220, "Boron account submission ready")
    while True:
        line = stream.readline(COMMAND_BYTES + 1)
        if not line:
            return 0
        if len(line) > COMMAND_BYTES or not line.endswith(b"\n"):
            reply(500, "Command line too long")
            return 1
        command = line.decode("utf-8", errors="replace").strip()
        verb, _, argument = command.partition(" ")
        verb = verb.upper()
        if verb in {"EHLO", "HELO"}:
            stream.write(f"250-Boron\r\n250-SIZE {mail_submission.MAX_MESSAGE_BYTES}\r\n250 8BITMIME\r\n".encode())
        elif verb == "NOOP":
            reply(250, "OK")
        elif verb == "RSET":
            envelope, recipients = None, []
            reply(250, "Reset")
        elif verb == "QUIT":
            reply(221, "Bye")
            return 0
        elif verb == "MAIL":
            match = MAIL_RE.match(argument)
            if not match:
                reply(501, "Use MAIL FROM:<address>")
                continue
            envelope, recipients = match.group(1), []
            reply(250, "Sender accepted")
        elif verb == "RCPT":
            match = RCPT_RE.match(argument)
            if envelope is None or not match:
                reply(503, "Send MAIL FROM before RCPT TO")
                continue
            if len(recipients) >= mail_submission.MAX_RECIPIENTS:
                reply(452, "Too many recipients")
                continue
            try:
                recipients.append(mail_submission.validate_email_address(match.group(1)))
            except Exception:
                reply(501, "Invalid recipient address")
                continue
            reply(250, "Recipient accepted")
        elif verb == "DATA":
            if envelope is None or not recipients:
                reply(503, "Envelope sender and recipient required")
                continue
            reply(354, "End data with <CRLF>.<CRLF>")
            body = bytearray()
            while True:
                data_line = stream.readline(COMMAND_BYTES + mail_submission.MAX_MESSAGE_BYTES + 1)
                if not data_line:
                    return 1
                if data_line in (b".\r\n", b".\n"):
                    break
                if data_line.startswith(b".."):
                    data_line = data_line[1:]
                body.extend(data_line)
                if len(body) > mail_submission.MAX_MESSAGE_BYTES:
                    reply(552, "Message exceeds 25 MB")
                    return 1
            try:
                prepared, sender = mail_submission.prepare_account_message(uid, bytes(body), envelope)
                mail_submission.submit_via_postfix(prepared, sender, recipients)
            except mail_submission.MailSubmissionError as exc:
                reply(550, str(exc)[:300])
            else:
                reply(250, "Queued")
            envelope, recipients = None, []
        else:
            reply(502, "Command not supported")


if __name__ == "__main__":
    raise SystemExit(main())
