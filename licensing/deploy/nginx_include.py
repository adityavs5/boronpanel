#!/usr/bin/env python3
"""Add the protected-downloads include to the download.boronpanel.com server.

    python3 nginx_include.py /etc/nginx/sites-available/boron-dist.conf

Finds the ``server { ... }`` block that listens on 443 with
``server_name download.boronpanel.com`` and inserts

    include snippets/boron-download-protected.conf;

on the line before its ``location / { return 404; }``. Idempotent: a block
that already has the include is left alone. Anything unexpected (no such
block, no catch-all location) changes nothing and exits 2, so an operator can
add the line by hand. The file is replaced atomically, keeping its mode; the
previous version is kept next to it as ``<file>.bak-boron``. Standard library
only, because it runs with the system python3 during installation.

Exit codes: 0 inserted, 1 already present, 2 not changed (needs a human).
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile

HOST = "download.boronpanel.com"
INCLUDE = "include snippets/boron-download-protected.conf;"
CATCH_ALL = re.compile(r"^(?P<indent>[ \t]*)location[ \t]+/[ \t]*\{[ \t]*return[ \t]+404[ \t]*;[ \t]*\}")


class NotChanged(Exception):
    pass


def server_blocks(text: str) -> list[tuple[int, int]]:
    """(start, end) offsets of every top-level ``server { ... }`` block.

    Braces inside comments and quoted strings are ignored.
    """
    blocks, depth, start, quote, i = [], 0, None, "", 0
    while i < len(text):
        char = text[i]
        if quote:
            if char == "\\":
                i += 1
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#":
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline
            continue
        elif char == "{":
            if depth == 0 and re.search(r"(?:^|[\s;}])server\s*$", text[:i]):
                start = text.rfind("server", 0, i)
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                raise NotChanged("unbalanced braces")
            if depth == 0 and start is not None:
                blocks.append((start, i + 1))
                start = None
        i += 1
    if depth or quote:
        raise NotChanged("unbalanced braces or quotes")
    return blocks


def _uncommented(block: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in block.splitlines())


def _is_download_tls(block: str) -> bool:
    code = _uncommented(block)
    names = re.findall(r"^\s*server_name\s+([^;]+);", code, re.MULTILINE)
    listens = re.findall(r"^\s*listen\s+([^;]+);", code, re.MULTILINE)
    return (any(HOST in name.split() for name in names)
            and any(re.search(r"(?:^|[:\s])443(?:\s|$)", listen) for listen in listens))


def insert_include(text: str) -> tuple[str, bool]:
    """Return the new text and whether it changed. Raises NotChanged."""
    targets = [(s, e) for s, e in server_blocks(text) if _is_download_tls(text[s:e])]
    if len(targets) != 1:
        raise NotChanged(f"expected one 443 server block for {HOST}, found {len(targets)}")
    start, end = targets[0]
    block = text[start:end]
    if re.search(r"^\s*" + re.escape(INCLUDE), _uncommented(block), re.MULTILINE):
        return text, False
    lines = block.splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = CATCH_ALL.match(line)
        if match:
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            lines.insert(index, f"{match.group('indent')}{INCLUDE}{newline}")
            return text[:start] + "".join(lines) + text[end:], True
    raise NotChanged(f"no 'location / {{ return 404; }}' line in the {HOST} block")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    path = argv[1]
    with open(path, encoding="utf-8") as handle:
        original = handle.read()
    try:
        updated, changed = insert_include(original)
    except NotChanged as exc:
        print(f"{path}: not changed: {exc}", file=sys.stderr)
        return 2
    if not changed:
        print(f"{path}: include already present")
        return 1
    mode = os.stat(path).st_mode & 0o7777
    directory = os.path.dirname(os.path.abspath(path))
    shutil.copy2(path, path + ".bak-boron")
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".boron-include-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(updated)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    print(f"{path}: inserted {INCLUDE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
