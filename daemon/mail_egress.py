"""Restrict direct outbound SMTP (TCP/25) to Postfix and root.

Every hosted domain's SPF record authorizes this server's address, so a
tenant process that talks straight to a remote MX can send SPF/DMARC-aligned
mail as any other tenant's domain. Only Postfix's own SMTP client (and root)
may open remote port 25; tenants submit through the identity-bound frontends
(daemon/mail_submission.py), which Postfix then delivers.

The rules live in a dedicated chain that is rebuilt atomically with
``iptables-restore --noflush`` and jumped to from the top of OUTPUT, ahead of
UFW's own chains (see daemon/filebrowser.py for why appending has no effect).
The rule covers loopback too. Local port 25 accepts mail for every hosted
domain from any sender, and a tenant owns forwarders on its own domains: a
tenant connecting to 127.0.0.1:25 (or to the server's own public address,
which also travels over lo) could send MAIL FROM:<ceo@other-tenant> to its own
forwarder and Postfix would relay it, SPF-aligned, to any outside mailbox.
Tenants have no legitimate unauthenticated use of local port 25: PHP mail(),
cron and sendmail use the identity socket, and authenticated clients
(webmail, SMTP plugins) use submission on 587.
``boron-mail-egress.service`` applies this at boot and on every update; its
ExecStop removes it.
"""
from __future__ import annotations

import argparse
import logging
import os
import pwd

from daemon.procutil import run

logger = logging.getLogger("borond.mail_egress")
CHAIN = "BORON-SMTP-OUT"
SMTP_PORT = "25"
EXEMPT_USERS = ("root", "postfix")
JUMP = ["-p", "tcp", "--dport", SMTP_PORT, "-j", CHAIN]
FAMILIES = (("iptables", "iptables-restore"), ("ip6tables", "ip6tables-restore"))


class MailEgressError(RuntimeError):
    pass


def exempt_uids() -> list[int]:
    uids = []
    for name in EXEMPT_USERS:
        try:
            uids.append(pwd.getpwnam(name).pw_uid)
        except KeyError:
            continue
    return sorted(set(uids)) or [0]


def render_chain(uids: list[int]) -> str:
    lines = ["*filter", f":{CHAIN} - [0:0]"]
    lines += [f"-A {CHAIN} -m owner --uid-owner {uid} -j RETURN" for uid in uids]
    lines += [f"-A {CHAIN} -j REJECT --reject-with tcp-reset", "COMMIT", ""]
    return "\n".join(lines)


def _ipv6_enabled() -> bool:
    return os.path.exists("/proc/net/if_inet6")


def _families():
    for tool, restore in FAMILIES:
        if tool == "ip6tables" and not _ipv6_enabled():
            continue
        yield tool, restore


def apply() -> dict:
    """Rebuild the chain atomically, then make sure OUTPUT jumps to it first."""
    uids = exempt_uids()
    payload = render_chain(uids)
    applied = []
    for tool, restore in _families():
        try:
            result = run([restore, "--noflush"], input_text=payload, timeout=20)
        except OSError as exc:
            raise MailEgressError(f"{restore} is unavailable: {exc}") from exc
        if not result.ok:
            raise MailEgressError(f"{restore} failed: {result.stderr.strip() or result.stdout.strip()}")
        if not run([tool, "-C", "OUTPUT", *JUMP], timeout=10).ok:
            inserted = run([tool, "-I", "OUTPUT", "1", *JUMP], timeout=10)
            if not inserted.ok:
                raise MailEgressError(f"{tool} OUTPUT jump failed: {inserted.stderr.strip()}")
        applied.append(tool)
    return {"chain": CHAIN, "exempt_uids": uids, "families": applied}


def remove() -> dict:
    removed = []
    for tool, _restore in _families():
        for _ in range(16):  # duplicates from manual edits, bounded
            if not run([tool, "-C", "OUTPUT", *JUMP], timeout=10).ok:
                break
            run([tool, "-D", "OUTPUT", *JUMP], timeout=10)
        run([tool, "-F", CHAIN], timeout=10)
        run([tool, "-X", CHAIN], timeout=10)
        removed.append(tool)
    return {"chain": CHAIN, "families": removed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "remove"))
    args = parser.parse_args(argv)
    try:
        apply() if args.action == "apply" else remove()
    except MailEgressError as exc:
        logger.error("%s", exc)
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
