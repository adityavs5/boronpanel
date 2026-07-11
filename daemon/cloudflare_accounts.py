"""Cloudflare account pool (docs/PLAN-cloudflare.md Phase 2+3, goal feature 1).

Replaces the single global CLOUDFLARE_API_TOKEN with a pool of Cloudflare
accounts so a provider can spread customer zones across several accounts
(each Cloudflare account has a finite zone budget; `max_zones` is the soft
cap this pool round-robins under). Each account's API token is stored
Fernet-encrypted at rest (daemon/appcrypto.py) and never returned to the UI.

Zone assignment (goal): round-robin across accounts that are active AND have
capacity (zone_count < max_zones); an account at its cap is skipped. We pick
the eligible account with the fewest assigned zones (ties broken by id) --
deterministic, load-balancing, and equivalent to round-robin as zones are
added one at a time.

Backwards compatibility: a zone enabled before the pool existed has
cloudflare_zones.cf_account_id = NULL and falls back to the legacy
settings.cloudflare_api_token. migrate_single_token() folds that legacy
config into a first pool row at daemon startup so the whole fleet converges
on the pool.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from shared.config import settings
from shared.db import write_session
from shared.models import CloudflareAccount, CloudflareZone

from daemon import appcrypto, cloudflare

logger = logging.getLogger("borond.cloudflare_accounts")

MAX_ZONES_DEFAULT = 800
NAME_MAX_LEN = 64


class CloudflareAccountError(Exception):
    pass


# --- serialization / helpers ------------------------------------------------


def _public(row: CloudflareAccount) -> dict:
    """Account row minus the token, for API/UI. `capacity` and `full` are
    derived so the UI needn't know the max-zones rule."""
    return {
        "id": row.id,
        "name": row.name,
        "account_id": row.account_id,
        "zone_count": row.zone_count,
        "max_zones": row.max_zones,
        "capacity_remaining": max(0, row.max_zones - row.zone_count),
        "full": row.zone_count >= row.max_zones,
        "active": row.active,
    }


def _sync_zone_count(session, cf_account_id: int) -> None:
    """Recompute the denormalized zone_count from the CloudflareZone rows
    actually assigned to this account -- called after every enable/disable so
    the cache can never drift (cheaper to reason about than +/- deltas)."""
    row = session.get(CloudflareAccount, cf_account_id)
    if row is None:
        return
    row.zone_count = (
        session.scalar(
            select(func.count()).select_from(CloudflareZone).where(CloudflareZone.cf_account_id == cf_account_id)
        )
        or 0
    )


def token_for_id(cf_account_id: int | None) -> str | None:
    """Decrypt and return the API token for a pool account, or None (the
    caller then falls back to the legacy settings token)."""
    if cf_account_id is None:
        return None
    with write_session() as session:
        row = session.get(CloudflareAccount, cf_account_id)
        enc = row.api_token_enc if row is not None else None
    if not enc:
        return None
    return appcrypto.decrypt_secret(enc)


# --- assignment -------------------------------------------------------------


def _pick_account(session) -> CloudflareAccount | None:
    """The eligible account with the most remaining capacity (fewest zones
    relative to its cap), or None. Ordered by zone_count then id so the
    choice is stable and fills evenly."""
    candidates = session.scalars(
        select(CloudflareAccount).where(CloudflareAccount.active.is_(True))
    ).all()
    eligible = [a for a in candidates if a.zone_count < a.max_zones]
    if not eligible:
        return None
    eligible.sort(key=lambda a: (a.zone_count, a.id))
    return eligible[0]


def assign_for_new_zone() -> tuple[dict | None, str | None]:
    """Pick where a new zone should live. Returns (assignment, reason):

      - (snapshot, None)  -> use this pool account. snapshot is
        {id, account_id, token}, token decrypted for cloudflare.use_token().
      - (None, None)      -> pool is empty but a legacy single token is
        configured; fall back to it (cf_account_id stays NULL).
      - (None, reason)    -> refuse, with a human-readable reason (no
        account with capacity / nothing configured at all).
    """
    with write_session() as session:
        pool_size = session.scalar(select(func.count()).select_from(CloudflareAccount)) or 0
        picked = _pick_account(session)
        if picked is not None:
            return (
                {"id": picked.id, "account_id": picked.account_id, "token": appcrypto.decrypt_secret(picked.api_token_enc)},
                None,
            )
    if pool_size > 0:
        return (None, "all Cloudflare pool accounts are at capacity -- raise a max_zones or add another account")
    if settings.cloudflare_api_token:
        return (None, None)  # legacy single-token config
    return (
        None,
        "Cloudflare is not configured -- add an account under admin Cloudflare settings, "
        "or set CLOUDFLARE_API_TOKEN in secrets.env",
    )


def has_capacity() -> bool:
    """Can a new zone be placed anywhere (a pool account with room, or the
    legacy token)? Used by the auto-enable path (feature 6) to skip cleanly."""
    _, reason = assign_for_new_zone()
    return reason is None


# --- CRUD ops (server.py registers these under cf.account_*) -----------------


def _validate_name(name) -> str:
    if not isinstance(name, str) or not name.strip():
        raise CloudflareAccountError("account name is required")
    name = name.strip()
    if len(name) > NAME_MAX_LEN:
        raise CloudflareAccountError(f"account name must be <= {NAME_MAX_LEN} characters")
    return name


def _verify_token_live(token: str, account_id: str) -> None:
    """Prove the token is valid and can see the account's zone surface before
    we store it -- the same check cf.health does, scoped to this account."""
    with cloudflare.use_token(token):
        verify = cloudflare.verify_token()
        if verify.get("status") != "active":
            raise CloudflareAccountError(
                f"Cloudflare says this token's status is '{verify.get('status')}', expected 'active'"
            )
        params = {"per_page": 1}
        if account_id:
            params["account.id"] = account_id
        cloudflare.list_zones(params)


def add_account(params: dict) -> dict:
    """cf.account_add: validate + live-verify the token, then store it
    encrypted. Makes outbound HTTPS calls (REPORTING_OP)."""
    name = _validate_name(params.get("name"))
    token = params.get("api_token")
    if not isinstance(token, str) or not token.strip():
        raise CloudflareAccountError("api_token is required")
    token = token.strip()
    account_id = (params.get("account_id") or "").strip()
    if not account_id:
        raise CloudflareAccountError("account_id (Cloudflare's account identifier) is required")
    try:
        max_zones = int(params.get("max_zones", MAX_ZONES_DEFAULT))
    except (TypeError, ValueError):
        raise CloudflareAccountError("max_zones must be an integer")
    if max_zones < 1:
        raise CloudflareAccountError("max_zones must be >= 1")

    try:
        _verify_token_live(token, account_id)
    except cloudflare.CloudflareError as exc:
        raise CloudflareAccountError(f"Cloudflare rejected the token: {exc}") from exc

    with write_session() as session:
        if session.scalar(select(CloudflareAccount).where(CloudflareAccount.name == name)) is not None:
            raise CloudflareAccountError(f"a Cloudflare account named '{name}' already exists")
        row = CloudflareAccount(
            name=name,
            api_token_enc=appcrypto.encrypt_secret(token),
            account_id=account_id,
            zone_count=0,
            max_zones=max_zones,
            active=True,
        )
        session.add(row)
        session.flush()
        result = _public(row)
    logger.info("added Cloudflare pool account '%s' (max_zones=%d)", name, max_zones)
    return result


def list_accounts(params: dict) -> dict:
    """cf.account_list: pool overview for the admin UI. No API calls -- uses
    the denormalized zone_count (kept in step on every enable/disable) so the
    list is cheap. Tokens are never included."""
    with write_session() as session:
        rows = session.scalars(select(CloudflareAccount).order_by(CloudflareAccount.id)).all()
        accounts = [_public(r) for r in rows]
    return {
        "accounts": accounts,
        "legacy_single_token": bool(settings.cloudflare_api_token) and not accounts,
    }


def test_account(params: dict) -> dict:
    """cf.account_test: live token + reachability check for one pool account
    (REPORTING_OP). Reports the current live zone count for the account too."""
    account_pk = params.get("id")
    with write_session() as session:
        row = session.get(CloudflareAccount, account_pk) if account_pk is not None else None
        if row is None:
            raise CloudflareAccountError(f"no Cloudflare pool account with id {account_pk!r}")
        token = appcrypto.decrypt_secret(row.api_token_enc)
        cf_account_id = row.account_id
        name = row.name
    result: dict = {"id": account_pk, "name": name, "token_valid": False, "api_ok": False, "error": None}
    try:
        with cloudflare.use_token(token):
            verify = cloudflare.verify_token()
            result["token_valid"] = verify.get("status") == "active"
            zone_params = {"account.id": cf_account_id} if cf_account_id else None
            live = cloudflare.list_zones(zone_params)
            result["api_ok"] = True
            result["live_zone_count"] = len(live)
    except cloudflare.CloudflareError as exc:
        result["error"] = str(exc)
        return result
    result["ok"] = bool(result["token_valid"] and result["api_ok"])
    return result


def delete_account(params: dict) -> dict:
    """cf.account_delete: remove a pool account. Refuses while it still
    serves zones -- those must be reverted/migrated first, or the token they
    depend on would vanish. `force` overrides only after that check."""
    account_pk = params.get("id")
    with write_session() as session:
        row = session.get(CloudflareAccount, account_pk) if account_pk is not None else None
        if row is None:
            raise CloudflareAccountError(f"no Cloudflare pool account with id {account_pk!r}")
        assigned = (
            session.scalar(
                select(func.count()).select_from(CloudflareZone).where(CloudflareZone.cf_account_id == row.id)
            )
            or 0
        )
        if assigned and not params.get("force"):
            raise CloudflareAccountError(
                f"account '{row.name}' still serves {assigned} zone(s); revert or migrate them first "
                "(or pass force=true, which orphans those zones onto the legacy token)"
            )
        name = row.name
        if assigned:
            # force: detach the zones (cf_account_id -> NULL) so they fall
            # back to the legacy single token, and so the FK doesn't block
            # the delete. They keep working only if a legacy token is set.
            for z in session.scalars(select(CloudflareZone).where(CloudflareZone.cf_account_id == row.id)).all():
                z.cf_account_id = None
            session.flush()
        session.delete(row)
    logger.info("deleted Cloudflare pool account '%s'", name)
    return {"id": account_pk, "name": name, "status": "deleted"}


def set_account(params: dict) -> dict:
    """cf.account_set: update the mutable fields of a pool account
    (max_zones, active). Token rotation goes through add/delete to keep the
    live-verify guarantee."""
    account_pk = params.get("id")
    with write_session() as session:
        row = session.get(CloudflareAccount, account_pk) if account_pk is not None else None
        if row is None:
            raise CloudflareAccountError(f"no Cloudflare pool account with id {account_pk!r}")
        if "max_zones" in params:
            try:
                mx = int(params["max_zones"])
            except (TypeError, ValueError):
                raise CloudflareAccountError("max_zones must be an integer")
            if mx < 1:
                raise CloudflareAccountError("max_zones must be >= 1")
            row.max_zones = mx
        if "active" in params:
            row.active = bool(params["active"])
        result = _public(row)
    return result


# --- startup migration ------------------------------------------------------


def migrate_single_token() -> bool:
    """Fold the legacy single-token config into a first pool row (goal
    feature 1: "Migrate existing single-token config into first row").
    Idempotent: only fires when a legacy token is configured AND the pool is
    empty. Does not make a live call (startup must not block on the network);
    cf.account_test validates it later."""
    if not settings.cloudflare_api_token:
        return False
    with write_session() as session:
        if session.scalar(select(func.count()).select_from(CloudflareAccount)):
            return False  # pool already populated
        row = CloudflareAccount(
            name="default",
            api_token_enc=appcrypto.encrypt_secret(settings.cloudflare_api_token),
            account_id=settings.cloudflare_account_id or "",
            zone_count=0,
            max_zones=MAX_ZONES_DEFAULT,
            active=True,
        )
        session.add(row)
        session.flush()
        # Adopt any pre-pool zones (cf_account_id NULL) onto this first row.
        for z in session.scalars(select(CloudflareZone).where(CloudflareZone.cf_account_id.is_(None))).all():
            z.cf_account_id = row.id
        _sync_zone_count(session, row.id)
    logger.info("migrated legacy Cloudflare single-token config into pool account 'default'")
    return True
