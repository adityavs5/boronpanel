"""Admin CLI: ``boron-license <command>``. Run on the license server."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from boron_license import config, service
from boron_license.db import create_schema, make_engine, make_sessionmaker
from boron_license.models import AdminToken, Tier
from boron_license.service import LicenseError
from boron_license.signing import (
    Signer,
    load_private_key,
    public_pem,
    write_private_key,
)


def _actor() -> str:
    return "cli:" + (os.environ.get("SUDO_USER") or getpass.getuser())


def _date(value: str) -> datetime:
    """YYYY-MM-DD means the end of that day, UTC; full ISO timestamps are kept."""
    try:
        if len(value) == 10:
            return datetime.strptime(f"{value} 23:59:59 +0000", "%Y-%m-%d %H:%M:%S %z")
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date: {value}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _print(data) -> None:
    print(json.dumps(data, indent=2, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="boron-license", description="Boron license server administration")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create tables and default tiers")
    key = sub.add_parser("gen-signing-key", help="create the license signing key (once)")
    key.add_argument("--path", help="defaults to BORON_LICENSE_SIGNING_KEY")
    sub.add_parser("public-key", help="print the public key panels must embed")

    tier = sub.add_parser("tier", help="manage tiers").add_subparsers(dest="tier_command", required=True)
    tier.add_parser("list")
    tier_set = tier.add_parser("set", help="create or change a tier")
    tier_set.add_argument("name")
    limit = tier_set.add_mutually_exclusive_group(required=True)
    limit.add_argument("--max-accounts", type=int)
    limit.add_argument("--unlimited", action="store_true")

    issue = sub.add_parser("issue", help="issue a new license and print its key once")
    issue.add_argument("--tier", required=True)
    issue.add_argument("--name", required=True, help="customer name")
    issue.add_argument("--email", required=True, help="customer email")
    issue.add_argument("--expires", type=_date, help="YYYY-MM-DD (end of day, UTC); omit for no expiry")
    issue.add_argument("--ip", help="bind now instead of on first activation")
    issue.add_argument("--channel", default="stable", choices=service.CHANNELS)
    issue.add_argument("--notes", default="")

    listing = sub.add_parser("list", help="list or search licenses")
    listing.add_argument("query", nargs="?", default="", help="email, name, IP, key, key hint or ID")
    listing.add_argument("--status", choices=service.STATUSES)
    listing.add_argument("--limit", type=int, default=50)

    show = sub.add_parser("show", help="show a license and its installations")
    show.add_argument("id", type=int)

    for name, help_text in (("suspend", "stop a license (reversible)"), ("resume", "reactivate a license"),
                            ("cancel", "end a license permanently")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("id", type=int)
        command.add_argument("--reason", default="")

    rebind = sub.add_parser("rebind", help="move a license to another server IP")
    rebind.add_argument("id", type=int)
    target = rebind.add_mutually_exclusive_group(required=True)
    target.add_argument("--ip")
    target.add_argument("--clear", action="store_true", help="bind on the next activation")

    change = sub.add_parser("update", help="change tier, expiry, channel or customer details")
    change.add_argument("id", type=int)
    change.add_argument("--tier")
    expiry = change.add_mutually_exclusive_group()
    expiry.add_argument("--expires", type=_date)
    expiry.add_argument("--no-expiry", action="store_true")
    change.add_argument("--channel", choices=service.CHANNELS)
    change.add_argument("--name")
    change.add_argument("--email")
    change.add_argument("--notes")

    token = sub.add_parser("token", help="admin API tokens").add_subparsers(dest="token_command", required=True)
    token.add_parser("list")
    create = token.add_parser("create", help="create a token and print it once")
    create.add_argument("name")
    revoke = token.add_parser("revoke")
    revoke.add_argument("name")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = config.load()

    if args.command == "gen-signing-key":
        path = settings.signing_key_path if args.path is None else type(settings.signing_key_path)(args.path)
        if path.exists():
            print(f"error: {path} already exists; refusing to replace a signing key", file=sys.stderr)
            return 1
        key = Ed25519PrivateKey.generate()
        write_private_key(path, key)
        print(f"Wrote {path}")
        print(public_pem(key.public_key()), end="")
        return 0
    if args.command == "public-key":
        signer = Signer(load_private_key(settings.signing_key_path))
        print(f"key_id: {signer.key_id}")
        print(public_pem(signer.public_key), end="")
        return 0

    engine = make_engine(settings.database_url)
    if args.command == "init-db":
        create_schema(engine)
    sessions = make_sessionmaker(engine)
    with sessions() as session:
        try:
            output = _run(session, args)
            session.commit()
        except LicenseError as exc:
            session.rollback()
            print(f"error: {exc.code}", file=sys.stderr)
            return 1
    if output is not None:
        _print(output)
    return 0


def _run(session, args):
    actor = _actor()
    if args.command == "init-db":
        return {"schema": "ready", "tiers_created": service.seed_tiers(session)}
    if args.command == "tier":
        if args.tier_command == "set":
            service.upsert_tier(session, args.name, None if args.unlimited else args.max_accounts)
        return [{"name": t.name, "max_accounts": t.max_accounts}
                for t in session.scalars(select(Tier).order_by(Tier.id))]
    if args.command == "issue":
        license_, key = service.issue(session, tier=args.tier, customer_name=args.name,
                                      customer_email=args.email, actor=actor, expires_at=args.expires,
                                      bound_ip=args.ip, channel=args.channel, notes=args.notes)
        return {"license_key": key, "note": "Shown once. Send it to the customer now; it cannot be recovered.",
                "license": service.describe(license_)}
    if args.command == "list":
        return [service.describe(row) for row in
                service.search(session, query=args.query, status=args.status, limit=args.limit)]
    if args.command == "show":
        return {"license": service.describe(service.get_license(session, args.id)),
                "installations": service.installations(session, args.id)}
    if args.command in ("suspend", "resume", "cancel"):
        status = {"suspend": "suspended", "resume": "active", "cancel": "cancelled"}[args.command]
        return service.describe(service.set_status(session, args.id, status, actor=actor, reason=args.reason))
    if args.command == "rebind":
        return service.describe(service.rebind(session, args.id, None if args.clear else args.ip, actor=actor))
    if args.command == "update":
        return service.describe(service.update(
            session, args.id, actor=actor, tier=args.tier, expires_at=args.expires,
            clear_expiry=args.no_expiry, channel=args.channel, customer_name=args.name,
            customer_email=args.email, notes=args.notes))
    if args.command == "token":
        if args.token_command == "create":
            token = service.create_admin_token(session, args.name, actor=actor)
            return {"name": args.name, "token": token, "note": "Shown once. Store it in a password manager."}
        if args.token_command == "revoke":
            service.revoke_admin_token(session, args.name, actor=actor)
        return [{"name": t.name, "created_at": t.created_at, "last_used_at": t.last_used_at,
                 "revoked": t.revoked_at is not None}
                for t in session.scalars(select(AdminToken).order_by(AdminToken.id))]
    raise AssertionError(args.command)


if __name__ == "__main__":
    sys.exit(main())
