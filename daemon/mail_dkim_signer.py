"""RFC 6376 DKIM signing (rsa-sha256, relaxed/relaxed) for Boron's own frontends.

The identity-bound submission paths (daemon/mail_submission.py) sign mail
themselves instead of handing it to the OpenDKIM milter, because the milter
cannot tell which tenant a message belongs to. The earlier implementation
shelled out to ``opendkim-testmsg``, a test utility that is not on the
Postfix pipe(8) PATH, has no algorithm option (it emits RFC 8301-forbidden
rsa-sha1 on Ubuntu's OpenDKIM 2.11) and exits with a syntax error on any
message without a Date header. Signing here needs only the ``cryptography``
package the panel already depends on, so the unprivileged Postfix filter,
the SO_PEERCRED sendmail socket and panel notifications all share one
deterministic signer.

The module deliberately imports nothing from Boron: the Postfix filter runs
as the OpenDKIM account without access to the panel's configuration.
"""
from __future__ import annotations

import base64
import hashlib
import re
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

# RFC 6376 section 5.4.1 recommends these; From is listed twice so a second
# From header added after signing breaks the signature (over-signing, the
# same protection OpenDKIM's "OversignHeaders From" gave the milter path).
SIGNED_HEADERS = (
    "from", "from", "sender", "reply-to", "subject", "date", "message-id",
    "to", "cc", "mime-version", "content-type", "content-transfer-encoding",
    "in-reply-to", "references", "list-id", "list-unsubscribe",
)
_OVERSIGNED = {"from"}
_WSP_RUN = re.compile(rb"[ \t]+")
_LINE_SPLIT = re.compile(rb"\r?\n")
_SELECTOR = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")
MIN_KEY_BITS = 1024


class DkimSigningError(ValueError):
    pass


def load_private_key(pem: bytes) -> rsa.RSAPrivateKey:
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except (TypeError, ValueError) as exc:
        raise DkimSigningError("DKIM private key could not be parsed") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise DkimSigningError("DKIM private key is not an RSA key")
    if key.key_size < MIN_KEY_BITS:
        raise DkimSigningError("DKIM private key is shorter than 1024 bits")
    return key


def split_message(raw: bytes) -> tuple[list[bytes], bytes]:
    """Return (header fields, body) as found in ``raw``.

    Each header field keeps its continuation lines so canonicalization sees
    exactly what a verifier will; the signed output is the caller's input
    unchanged with one field prepended.
    """
    match = re.search(rb"\r?\n\r?\n", raw)
    if match is None:
        head, body = raw, b""
    else:
        head, body = raw[:match.start()], raw[match.end():]
    fields: list[bytes] = []
    # Split on line ends only: bytes.splitlines() would also break on \x0b,
    # \x0c, \x1c... inside a header value and change what is hashed.
    for line in re.findall(rb"[^\n]*\n|[^\n]+$", head):
        if line[:1] in (b" ", b"\t") and fields:
            fields[-1] += line
        elif line.strip():
            fields.append(line)
    return fields, body


def _newline(raw: bytes) -> bytes:
    end = raw.find(b"\n")
    return b"\r\n" if end < 0 or (end > 0 and raw[end - 1:end] == b"\r") else b"\n"


def _field_name(field: bytes) -> bytes:
    return field.split(b":", 1)[0].strip().lower()


def relaxed_header(field: bytes) -> bytes:
    """RFC 6376 section 3.4.2, without the trailing CRLF."""
    name, _, value = field.partition(b":")
    value = re.sub(rb"\r?\n(?=[ \t])", b"", value)
    value = value.replace(b"\r\n", b"").replace(b"\n", b"")
    value = _WSP_RUN.sub(b" ", value).strip(b" \t")
    return name.strip().lower() + b":" + value


def relaxed_body(body: bytes) -> bytes:
    """RFC 6376 section 3.4.4 (relaxed), with lines terminated by CRLF."""
    lines = _LINE_SPLIT.split(body)
    if lines and lines[-1] == b"":
        lines.pop()
    lines = [_WSP_RUN.sub(b" ", line).rstrip(b" ") for line in lines]
    while lines and lines[-1] == b"":
        lines.pop()
    return b"".join(line + b"\r\n" for line in lines)


def _selected_fields(fields: list[bytes], names: tuple[str, ...]) -> tuple[list[str], list[bytes]]:
    """Pick header instances bottom-up (RFC 6376 section 5.4.2)."""
    remaining: dict[bytes, list[bytes]] = {}
    for field in fields:
        remaining.setdefault(_field_name(field), []).append(field)
    signed_names: list[str] = []
    chosen: list[bytes] = []
    for name in names:
        instances = remaining.get(name.encode())
        if instances:
            chosen.append(instances.pop())
            signed_names.append(name)
        elif name in _OVERSIGNED and name in signed_names:
            # A listed name with no remaining instance hashes as empty and
            # makes any later-added instance invalidate the signature.
            signed_names.append(name)
    return signed_names, chosen


def _fold_tags(tags: list[str]) -> str:
    return ";\r\n\t".join(tags)


def _fold_base64(value: str, width: int = 72) -> str:
    return "\r\n\t".join(value[index:index + width] for index in range(0, len(value), width))


def sign(raw: bytes, *, domain: str, selector: str, private_key_pem: bytes,
         timestamp: int | None = None) -> bytes:
    """Return ``raw`` with a DKIM-Signature field prepended."""
    domain = (domain or "").strip().lower().rstrip(".")
    selector = (selector or "").strip().lower()
    if not domain or not _SELECTOR.fullmatch(domain):
        raise DkimSigningError("DKIM signing domain is invalid")
    if not _SELECTOR.fullmatch(selector):
        raise DkimSigningError("DKIM selector is invalid")
    key = load_private_key(private_key_pem)
    fields, body = split_message(raw)
    if not any(_field_name(field) == b"from" for field in fields):
        raise DkimSigningError("message has no From header to sign")
    body_hash = base64.b64encode(hashlib.sha256(relaxed_body(body)).digest()).decode()
    signed_names, chosen = _selected_fields(fields, SIGNED_HEADERS)
    stamp = int(time.time()) if timestamp is None else int(timestamp)
    tags = [
        "v=1", "a=rsa-sha256", "c=relaxed/relaxed", f"d={domain}", f"s={selector}",
        f"t={stamp}", "h=" + ":".join(signed_names), f"bh={body_hash}", "b=",
    ]
    unsigned_field = ("DKIM-Signature: " + _fold_tags(tags)).encode()
    hashed = b"".join(relaxed_header(field) + b"\r\n" for field in chosen) + relaxed_header(unsigned_field)
    signature = key.sign(hashed, padding.PKCS1v15(), hashes.SHA256())
    encoded = base64.b64encode(signature).decode()
    newline = _newline(raw)
    header = unsigned_field + _fold_base64(encoded).encode()
    if newline == b"\n":
        header = header.replace(b"\r\n", b"\n")
    return header + newline + raw
