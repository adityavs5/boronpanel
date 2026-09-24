"""Reseller plans, identities, quotas, and ownership-scoped account actions."""
from __future__ import annotations

import logging
import threading

from sqlalchemy import func, select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, PanelUser, ResellerAccount, ResellerPlan, ResellerProfile
from shared.validation import ValidationError, generate_strong_password, validate_domain, validate_php_version, validate_username

from daemon import audit, handlers_account, handlers_auth

logger = logging.getLogger("borond.resellers")
_state_lock = threading.RLock()


class ResellerError(Exception):
    pass


def _bounded(value, name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValidationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _plan_dict(row: ResellerPlan) -> dict:
    result = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    result["account_cpu_cores"] = row.account_cpu_pct / 100
    return result


def _validate_plan(params: dict, current: ResellerPlan | None = None) -> dict:
    get = lambda key, fallback: params.get(key, getattr(current, key) if current is not None else fallback)
    name = str(get("name", "")).strip()
    if not name or len(name) > 100:
        raise ValidationError("name is required and must be at most 100 characters")
    fields = {
        "name": name,
        "max_accounts": _bounded(get("max_accounts", 10), "max_accounts", 10, 1, 10000),
        "max_total_disk_mb": _bounded(get("max_total_disk_mb", 102400), "max_total_disk_mb", 102400, 1, 100_000_000),
        "account_quota_soft_mb": _bounded(get("account_quota_soft_mb", 4096), "account_quota_soft_mb", 4096, 1, 10_000_000),
        "account_quota_hard_mb": _bounded(get("account_quota_hard_mb", 5120), "account_quota_hard_mb", 5120, 1, 10_000_000),
        "account_cpu_pct": _bounded(get("account_cpu_pct", 50), "account_cpu_pct", 50, 1, 25600),
        "account_mem_mb": _bounded(get("account_mem_mb", 1024), "account_mem_mb", 1024, 64, 65536),
        "account_io_mb": _bounded(get("account_io_mb", 50), "account_io_mb", 50, 1, 10000),
        "account_pids_max": _bounded(get("account_pids_max", 100), "account_pids_max", 100, 10, 10000),
        "php_version": validate_php_version(str(get("php_version", settings.default_php_version)), settings.php_versions),
    }
    if fields["account_quota_hard_mb"] < fields["account_quota_soft_mb"]:
        raise ValidationError("account_quota_hard_mb must be at least account_quota_soft_mb")
    if fields["max_total_disk_mb"] < fields["account_quota_hard_mb"]:
        raise ValidationError("max_total_disk_mb must fit at least one account hard quota")
    return fields


def create_plan(params: dict) -> dict:
    fields = _validate_plan(params)
    with _state_lock, write_session() as session:
        if session.scalar(select(ResellerPlan.id).where(ResellerPlan.name == fields["name"])) is not None:
            raise ResellerError(f"reseller plan '{fields['name']}' already exists")
        row = ResellerPlan(**fields)
        session.add(row)
        session.flush()
        return _plan_dict(row)


def _profile_usage(session, profile_id: int) -> tuple[int, int]:
    accounts = session.scalars(
        select(Account)
        .join(ResellerAccount, ResellerAccount.account_id == Account.id)
        .where(ResellerAccount.reseller_id == profile_id, Account.status != "terminated")
    ).all()
    return len(accounts), sum(account.quota_hard_mb for account in accounts)


def _require_capacity(session, profile: ResellerProfile, plan: ResellerPlan) -> None:
    account_count, disk_mb = _profile_usage(session, profile.id)
    if account_count > plan.max_accounts:
        raise ResellerError(
            f"plan allows {plan.max_accounts} accounts, but reseller currently has {account_count}"
        )
    if disk_mb > plan.max_total_disk_mb:
        raise ResellerError(
            f"plan allows {plan.max_total_disk_mb} MB, but reseller currently allocates {disk_mb} MB"
        )


def update_plan(params: dict) -> dict:
    plan_id = int(params["plan_id"])
    with _state_lock, write_session() as session:
        row = session.get(ResellerPlan, plan_id)
        if row is None:
            raise ResellerError(f"reseller plan {plan_id} not found")
        fields = _validate_plan(params, row)
        duplicate = session.scalar(select(ResellerPlan.id).where(ResellerPlan.name == fields["name"], ResellerPlan.id != plan_id))
        if duplicate is not None:
            raise ResellerError(f"reseller plan '{fields['name']}' already exists")
        candidate = ResellerPlan(id=row.id, **fields)
        profiles = session.scalars(select(ResellerProfile).where(ResellerProfile.plan_id == plan_id)).all()
        for profile in profiles:
            _require_capacity(session, profile, candidate)
        for key, value in fields.items():
            setattr(row, key, value)
        session.flush()
        return _plan_dict(row)


def list_plans(params: dict | None = None) -> dict:
    with write_session() as session:
        rows = session.scalars(select(ResellerPlan).order_by(ResellerPlan.name)).all()
        counts = dict(session.execute(select(ResellerProfile.plan_id, func.count()).group_by(ResellerProfile.plan_id)).all())
        return {"plans": [{**_plan_dict(row), "reseller_count": counts.get(row.id, 0)} for row in rows]}


def delete_plan(params: dict) -> dict:
    plan_id = int(params["plan_id"])
    with _state_lock, write_session() as session:
        row = session.get(ResellerPlan, plan_id)
        if row is None:
            raise ResellerError(f"reseller plan {plan_id} not found")
        if session.scalar(select(ResellerProfile.id).where(ResellerProfile.plan_id == plan_id)) is not None:
            raise ResellerError("plan is assigned to a reseller")
        session.delete(row)
    return {"status": "deleted", "id": plan_id}


def _profile_dict(session, profile: ResellerProfile) -> dict:
    user = session.get(PanelUser, profile.panel_user_id)
    plan = session.get(ResellerPlan, profile.plan_id)
    account_count = session.scalar(
        select(func.count()).select_from(ResellerAccount).join(Account, Account.id == ResellerAccount.account_id).where(
            ResellerAccount.reseller_id == profile.id, Account.status != "terminated"
        )
    ) or 0
    return {
        "id": profile.id,
        "username": user.username,
        "company": profile.company,
        "status": profile.status,
        "plan_id": profile.plan_id,
        "plan_name": plan.name,
        "account_count": account_count,
        "max_accounts": plan.max_accounts,
        "created_at": profile.created_at.isoformat(),
    }


def create_reseller(params: dict) -> dict:
    username = validate_username(params["username"])
    password = params.get("password") or generate_strong_password()
    company = str(params.get("company") or "").strip() or None
    if company and len(company) > 160:
        raise ValidationError("company must be at most 160 characters")
    plan_id = int(params["plan_id"])
    with _state_lock:
        with write_session() as session:
            if session.get(ResellerPlan, plan_id) is None:
                raise ResellerError(f"reseller plan {plan_id} not found")
        user = handlers_auth.create_panel_user({"username": username, "password": password, "role": "reseller"})
        try:
            with write_session() as session:
                profile = ResellerProfile(panel_user_id=user["id"], plan_id=plan_id, company=company, status="active")
                session.add(profile)
                session.flush()
                result = _profile_dict(session, profile)
        except Exception:
            with write_session() as session:
                orphan = session.get(PanelUser, user["id"])
                if orphan is not None:
                    session.delete(orphan)
            raise
    result["initial_password"] = password
    return result


def list_resellers(params: dict | None = None) -> dict:
    with write_session() as session:
        rows = session.scalars(select(ResellerProfile).order_by(ResellerProfile.created_at.desc())).all()
        return {"resellers": [_profile_dict(session, row) for row in rows]}


def update_reseller(params: dict) -> dict:
    profile_id = int(params["reseller_id"])
    with _state_lock, write_session() as session:
        profile = session.get(ResellerProfile, profile_id)
        if profile is None:
            raise ResellerError(f"reseller {profile_id} not found")
        if "plan_id" in params:
            plan_id = int(params["plan_id"])
            plan = session.get(ResellerPlan, plan_id)
            if plan is None:
                raise ResellerError(f"reseller plan {plan_id} not found")
            _require_capacity(session, profile, plan)
            profile.plan_id = plan_id
        if "status" in params:
            status = str(params["status"])
            if status not in ("active", "suspended"):
                raise ValidationError("status must be active or suspended")
            profile.status = status
            user = session.get(PanelUser, profile.panel_user_id)
            user.disabled = status != "active"
        if "company" in params:
            company = str(params.get("company") or "").strip() or None
            if company and len(company) > 160:
                raise ValidationError("company must be at most 160 characters")
            profile.company = company
        session.flush()
        return _profile_dict(session, profile)


def move_account(params: dict) -> dict:
    """Assign an account to a reseller, move it, or return it to admin ownership."""
    username = validate_username(params["username"])
    raw_target = params.get("reseller_id")
    target_id = int(raw_target) if raw_target is not None else None
    with _state_lock, write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None or account.status == "terminated":
            raise ResellerError(f"account '{username}' not found")
        current = session.scalar(select(ResellerAccount).where(ResellerAccount.account_id == account.id))
        if current is not None and current.reseller_id == target_id:
            target = session.get(ResellerProfile, target_id) if target_id is not None else None
            return {"username": username, "reseller_id": target_id,
                    "reseller_username": _profile_dict(session, target)["username"] if target else None}

        target = None
        if target_id is not None:
            target = session.get(ResellerProfile, target_id)
            if target is None:
                raise ResellerError(f"reseller {target_id} not found")
            if target.status != "active":
                raise ResellerError("accounts can only be assigned to an active reseller")
            plan = session.get(ResellerPlan, target.plan_id)
            count, disk_mb = _profile_usage(session, target.id)
            if count + 1 > plan.max_accounts:
                raise ResellerError("target reseller account limit reached")
            if disk_mb + account.quota_hard_mb > plan.max_total_disk_mb:
                raise ResellerError("target reseller disk allocation limit reached")

        if current is not None:
            session.delete(current)
            session.flush()
        if target is not None:
            session.add(ResellerAccount(reseller_id=target.id, account_id=account.id))
        return {"username": username, "reseller_id": target_id,
                "reseller_username": _profile_dict(session, target)["username"] if target else None}


def _profile_for_username(session, username: str, *, active: bool = True) -> ResellerProfile:
    profile = session.scalar(select(ResellerProfile).join(PanelUser, PanelUser.id == ResellerProfile.panel_user_id).where(
        PanelUser.username == username
    ))
    if profile is None or (active and profile.status != "active"):
        raise ResellerError("reseller profile is unavailable")
    return profile


def _owned_account(session, reseller_username: str, account_username: str) -> tuple[ResellerProfile, Account]:
    profile = _profile_for_username(session, reseller_username)
    account = session.scalar(select(Account).join(ResellerAccount, ResellerAccount.account_id == Account.id).where(
        ResellerAccount.reseller_id == profile.id,
        Account.username == validate_username(account_username),
    ))
    if account is None:
        raise ResellerError(f"account '{account_username}' is not owned by this reseller")
    return profile, account


def dashboard(params: dict) -> dict:
    reseller_username = validate_username(params["reseller_username"])
    with write_session() as session:
        profile = _profile_for_username(session, reseller_username)
        plan = session.get(ResellerPlan, profile.plan_id)
        accounts = session.scalars(select(Account).join(ResellerAccount, ResellerAccount.account_id == Account.id).where(
            ResellerAccount.reseller_id == profile.id, Account.status != "terminated"
        ).order_by(Account.username)).all()
        used_disk = sum(account.quota_hard_mb for account in accounts)
        return {
            "profile": _profile_dict(session, profile),
            "plan": _plan_dict(plan),
            "usage": {"accounts": len(accounts), "disk_mb": used_disk},
            "accounts": [handlers_account._account_to_dict(account) for account in accounts],
        }


def create_account(params: dict) -> dict:
    reseller_username = validate_username(params["reseller_username"])
    username = validate_username(params["username"])
    primary_domain = validate_domain(params["primary_domain"]) if params.get("primary_domain") else None
    password = params.get("password") or generate_strong_password()
    with _state_lock:
        with write_session() as session:
            profile = _profile_for_username(session, reseller_username)
            plan = session.get(ResellerPlan, profile.plan_id)
            accounts = session.scalars(select(Account).join(ResellerAccount, ResellerAccount.account_id == Account.id).where(
                ResellerAccount.reseller_id == profile.id, Account.status != "terminated"
            )).all()
            if len(accounts) >= plan.max_accounts:
                raise ResellerError("reseller account limit reached")
            if sum(account.quota_hard_mb for account in accounts) + plan.account_quota_hard_mb > plan.max_total_disk_mb:
                raise ResellerError("reseller disk allocation limit reached")
            if session.scalar(select(PanelUser.id).where(PanelUser.username == username)) is not None:
                raise ResellerError(f"panel login '{username}' already exists")
            plan_values = _plan_dict(plan)
            profile_id = profile.id
        account = handlers_account.create_account({
            "username": username,
            "password": password,
            "primary_domain": primary_domain,
            "php_version": plan_values["php_version"],
            "quota_soft_mb": plan_values["account_quota_soft_mb"],
            "quota_hard_mb": plan_values["account_quota_hard_mb"],
            "cpu_pct": plan_values["account_cpu_pct"],
            "mem_mb": plan_values["account_mem_mb"],
            "io_mb": plan_values["account_io_mb"],
            "pids_max": plan_values["account_pids_max"],
        })
        try:
            handlers_auth.create_panel_user({"username": username, "password": password, "role": "customer", "account_id": account["id"]})
            with write_session() as session:
                session.add(ResellerAccount(reseller_id=profile_id, account_id=account["id"]))
        except Exception:
            try:
                handlers_account.terminate_account({"username": username})
            except Exception:  # noqa: BLE001 - preserve the provisioning error
                logger.exception("failed to compensate reseller account creation for %s", username)
            raise
    audit.record_account_event("created", username, actor=reseller_username, role="reseller", detail="reseller account")
    return {**account, "initial_password": password}


def lifecycle(params: dict) -> dict:
    reseller_username = validate_username(params["reseller_username"])
    username = validate_username(params["username"])
    action = str(params["action"])
    handlers = {
        "suspend": handlers_account.suspend_account,
        "unsuspend": handlers_account.unsuspend_account,
        "terminate": handlers_account.terminate_account,
    }
    if action not in handlers:
        raise ValidationError("action must be suspend, unsuspend, or terminate")
    with _state_lock:
        with write_session() as session:
            _owned_account(session, reseller_username, username)
        result = handlers[action]({"username": username})
    audit.record_account_event(
        {"suspend": "suspended", "unsuspend": "unsuspended", "terminate": "terminated"}[action],
        username, actor=reseller_username, role="reseller", detail="reseller action",
    )
    return result
