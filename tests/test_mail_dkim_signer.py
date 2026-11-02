"""DKIM signing for the identity-bound submission paths.

Live regression: authenticated 587 submission from a DKIM-enabled domain
bounced "DKIM signing service is unavailable" because the filter ran a bare
``opendkim-testmsg`` from a PATH without /usr/sbin; that tool also signed
with rsa-sha1 and failed on messages without a Date header.
"""
import base64
import hashlib
import importlib.util
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from daemon import mail_dkim_signer as signer
from daemon import mail_submission

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


MESSAGE = (
    b"From: Alice <alice@demo.example>\r\n"
    b"To: bob@remote.example\r\n"
    b"Subject: Quarterly   report\r\n"
    b"\tcontinued\r\n"
    b"Date: Mon, 02 Nov 2026 10:00:00 +0000\r\n"
    b"Message-ID: <1@demo.example>\r\n"
    b"\r\n"
    b"Hello  Bob \r\n"
    b"\r\n"
    b"\r\n"
)


# --- an independent RFC 6376 verifier (section 6.1), written from the RFC ----

def _unfold(value: bytes) -> bytes:
    return re.sub(rb"\r?\n", b"", value)


def _canon_header(field: bytes) -> bytes:
    name, value = field.split(b":", 1)
    value = re.sub(rb"[ \t]+", b" ", _unfold(value)).strip()
    return name.strip().lower() + b":" + value


def _canon_body(body: bytes) -> bytes:
    lines = body.replace(b"\r\n", b"\n").split(b"\n")
    lines = [re.sub(rb"[ \t]+", b" ", line).rstrip(b" ") for line in lines]
    while lines and lines[-1] == b"":
        lines.pop()
    return b"".join(line + b"\r\n" for line in lines)


def _fields(head: bytes) -> list[bytes]:
    out = []
    for line in head.split(b"\r\n"):
        if line[:1] in (b" ", b"\t"):
            out[-1] += b"\r\n" + line
        elif line:
            out.append(line)
    return out


def verify(message: bytes, public_key) -> dict:
    head, body = message.split(b"\r\n\r\n", 1)
    fields = _fields(head)
    signature_field = next(f for f in fields if f.lower().startswith(b"dkim-signature:"))
    tags = {}
    for part in _unfold(signature_field.split(b":", 1)[1]).split(b";"):
        if part.strip():
            name, value = part.split(b"=", 1)
            tags[name.strip().decode()] = re.sub(rb"[ \t]", b"", value).decode()
    assert tags["a"] == "rsa-sha256" and tags["c"] == "relaxed/relaxed"
    body_hash = base64.b64encode(hashlib.sha256(_canon_body(body)).digest()).decode()
    if body_hash != tags["bh"]:
        raise InvalidSignature("body hash mismatch")
    remaining = [f for f in fields if f is not signature_field]
    hashed = b""
    for name in tags["h"].split(":"):
        matches = [f for f in remaining if f.split(b":", 1)[0].strip().lower() == name.encode()]
        if matches:
            chosen = matches[-1]
            remaining.remove(chosen)
            hashed += _canon_header(chosen) + b"\r\n"
    stripped = re.sub(rb"(b=)[^;]*$", rb"\1", signature_field, flags=re.DOTALL)
    hashed += _canon_header(stripped)
    public_key.verify(base64.b64decode(tags["b"]), hashed, padding.PKCS1v15(), hashes.SHA256())
    return tags


# --- canonicalization vectors (RFC 6376 section 3.4.5) ------------------------

def test_relaxed_canonicalization_matches_rfc_example():
    assert signer.relaxed_header(b"A: X\r\n") == b"a:X"
    assert signer.relaxed_header(b"B : Y\t\r\n\tZ  \r\n") == b"b:Y Z"
    assert signer.relaxed_body(b" C \r\nD \t E\r\n\r\n\r\n") == b" C\r\nD E\r\n"
    assert signer.relaxed_body(b"") == b""


def test_signature_is_rsa_sha256_and_verifies(key):
    signed = signer.sign(MESSAGE, domain="demo.example", selector="default",
                         private_key_pem=_pem(key), timestamp=1_793_000_000)
    assert signed.endswith(MESSAGE)
    tags = verify(signed, key.public_key())
    assert tags["d"] == "demo.example" and tags["s"] == "default" and tags["t"] == "1793000000"
    names = tags["h"].split(":")
    assert names.count("from") == 2  # over-signed: an added From breaks the signature
    assert {"to", "subject", "date", "message-id"} <= set(names)
    assert all(len(line) <= 998 for line in signed.split(b"\r\n"))


def test_tampering_breaks_the_signature(key):
    signed = signer.sign(MESSAGE, domain="demo.example", selector="default", private_key_pem=_pem(key))
    with pytest.raises(InvalidSignature):
        verify(signed.replace(b"Hello  Bob", b"Hello Eve"), key.public_key())
    with pytest.raises(InvalidSignature):
        verify(signed.replace(b"Subject: Quarterly", b"Subject: Annual"), key.public_key())
    added_from = signed.replace(b"To: bob", b"From: mallory@evil.example\r\nTo: bob")
    with pytest.raises(InvalidSignature):
        verify(added_from, key.public_key())


def test_message_without_date_is_signed(key):
    raw = b"From: alice@demo.example\r\nTo: bob@remote.example\r\nSubject: x\r\n\r\nbody\r\n"
    verify(signer.sign(raw, domain="demo.example", selector="default", private_key_pem=_pem(key)),
           key.public_key())


def test_lf_only_input_keeps_its_line_endings(key):
    raw = MESSAGE.replace(b"\r\n", b"\n")
    signed = signer.sign(raw, domain="demo.example", selector="default", private_key_pem=_pem(key))
    assert b"\r" not in signed
    verify(signed.replace(b"\n", b"\r\n"), key.public_key())


def test_rejects_unusable_keys_and_identities(key):
    with pytest.raises(signer.DkimSigningError):
        signer.sign(MESSAGE, domain="demo.example", selector="default", private_key_pem=b"nope")
    with pytest.raises(signer.DkimSigningError):
        signer.sign(MESSAGE, domain="demo.example", selector="bad;sel", private_key_pem=_pem(key))
    with pytest.raises(signer.DkimSigningError, match="From"):
        signer.sign(b"To: x@y.example\r\n\r\nx\r\n", domain="demo.example", selector="default",
                    private_key_pem=_pem(key))


# --- the Postfix submission filter (boron-sign pipe transport) -----------------

def _key_table(tmp_path, monkeypatch, key, domain="demo.example"):
    base = tmp_path / "dkim"
    (base / domain).mkdir(parents=True)
    key_path = base / domain / "default.private"
    key_path.write_bytes(_pem(key))
    table = tmp_path / "KeyTable"
    table.write_text(f"default._domainkey.{domain} {domain}:default:{key_path}\n")
    monkeypatch.setattr(mail_submission.settings, "dkim_base_dir", str(base))
    real_path = mail_submission.Path
    monkeypatch.setattr(mail_submission, "Path",
                        lambda value: table if str(value) == "/etc/opendkim/boron/KeyTable" else real_path(value))


def test_submission_filter_signs_owned_domain_without_date_header(tmp_path, monkeypatch, key):
    _key_table(tmp_path, monkeypatch, key)
    raw = b"From: Alice <alice@demo.example>\r\nTo: bob@demo.example\r\nSubject: hi\r\n\r\nbody\r\n"
    signed, envelope = mail_submission.prepare_submission_filter(raw, "alice@demo.example",
                                                                 login="alice@demo.example")
    assert envelope == "alice@demo.example"
    tags = verify(signed, key.public_key())
    assert "date" in tags["h"].split(":")


def _filter_module():
    spec = importlib.util.spec_from_file_location("mail_sign_filter_exit", REPO / "scripts" / "mail_sign_filter.py")
    saved = {name: os.environ.get(name) for name in ("BORON_CONFIG", "BORON_SECRETS", "BORON_API_SECRETS")}
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return module


@pytest.mark.parametrize("error,code", [
    (mail_submission.MailSubmissionError("From header must use a domain owned by this account"), 77),
    (mail_submission.MailSubmissionUnavailable("DKIM signing key is unavailable"), 75),
    (OSError("unexpected"), 75),
])
def test_filter_exit_codes_never_claim_user_unknown(monkeypatch, capsys, error, code):
    module = _filter_module()

    def fail(raw, sender, login=None):
        raise error

    monkeypatch.setattr(module.mail_submission, "prepare_submission_filter", fail)
    monkeypatch.setattr(module.sys, "argv", ["filter", "--sender", "a@demo.example",
                                             "--login=a@demo.example", "--recipient", "b@demo.example"])
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(buffer=SimpleNamespace(read=lambda n: b"x")))
    assert module.main() == code
    assert code != 67  # EX_NOUSER became Postfix's "5.1.1 user unknown"


def test_filter_import_does_not_leak_config_warnings_into_bounces(tmp_path):
    import subprocess
    import sys

    script = (
        "import runpy, sys; sys.argv=['f']\n"
        f"spec = {str(REPO / 'scripts' / 'mail_sign_filter.py')!r}\n"
        "import importlib.util as u\n"
        "m = u.module_from_spec(u.spec_from_file_location('f', spec))\n"
        "u.spec_from_file_location('f', spec).loader.exec_module(m)\n"
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("BORON_")}
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            env=env, cwd=tmp_path, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    assert "schema was migrated" not in result.stderr
