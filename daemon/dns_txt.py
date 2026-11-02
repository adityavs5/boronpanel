"""TXT record presentation format, shared by both DNS providers.

RFC 1035 SS3.3: a TXT record holds one or more character-strings of at most
255 octets each, which resolvers concatenate. PowerDNS content and zone files
use the presentation form -- quoted strings with \\X and \\DDD escapes -- and
Cloudflare stores one string that it splits itself. daemon/dnsprovider.py and
daemon/cloudflare.py read and write TXT only through these helpers, so the
two providers can never disagree about what a value means.
"""
from __future__ import annotations

import dns.exception
import dns.rdataclass
import dns.rdatatype
import dns.tokenizer
from dns.rdtypes.ANY.TXT import TXT

from shared.validation import ValidationError

# A longer value (a 2048-bit DKIM key is ~410 octets) must be published as
# several strings. PowerDNS accepts one oversized string, but zone-file
# parsers (dnspython in the advanced editor and the snapshot restore
# validator) reject it, so every write is split.
TXT_STRING_OCTETS = 255


def _lenient_unescape(inner: str) -> bytes:
    out = bytearray()
    raw = inner.encode("utf-8", "surrogateescape")
    i = 0
    while i < len(raw):
        char = raw[i]
        if char == 0x5C and i + 1 < len(raw):  # backslash
            digits = raw[i + 1:i + 4]
            if len(digits) == 3 and digits.isdigit() and int(digits) <= 255:
                out.append(int(digits))
                i += 4
                continue
            out.append(raw[i + 1])
            i += 2
            continue
        out.append(char)
        i += 1
    return bytes(out)


def txt_strings(value: str) -> list[bytes]:
    """The character-strings of one TXT value in presentation format.

    Quoted and bare words are each one string (zone-file grammar). Content
    that grammar cannot read -- legacy rows stored before validation, such
    as a raw newline inside quotes -- is read leniently as one string so it
    can still be displayed, compared and rewritten instead of failing.
    """
    if not isinstance(value, str):
        raise ValidationError("TXT values must be text")
    try:
        tokenizer = dns.tokenizer.Tokenizer(value)
        strings = []
        while True:
            token = tokenizer.get()
            if token.is_eof():
                break
            if not (token.is_quoted_string() or token.is_identifier()):
                raise dns.exception.SyntaxError("unexpected token")
            strings.append(token.unescape_to_bytes().value)
        return strings
    except dns.exception.DNSException:
        stripped = value.strip()
        if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
            return [_lenient_unescape(stripped[1:-1])]
        return [stripped.encode("utf-8", "surrogateescape")]


def txt_bytes(value: str) -> bytes:
    """The logical TXT data: its character-strings concatenated."""
    return b"".join(txt_strings(value))


def txt_text(value: str) -> str:
    """The logical TXT data as text, for comparisons and display."""
    return txt_bytes(value).decode("utf-8", "replace")


def txt_presentation(data: bytes) -> str:
    """Canonical PowerDNS content: escaped strings of at most 255 octets."""
    chunks = [data[i:i + TXT_STRING_OCTETS] for i in range(0, len(data), TXT_STRING_OCTETS)] or [b""]
    return TXT(dns.rdataclass.IN, dns.rdatatype.TXT, chunks).to_text()


def normalize_txt(value: str) -> str:
    return txt_presentation(txt_bytes(value))
