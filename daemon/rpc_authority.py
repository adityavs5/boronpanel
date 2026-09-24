"""Root-owned credential resolution and fail-closed RPC operation policy."""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, ApiToken, DnsClusterPeer, Domain, ImpersonationSession, MailDomain, PanelUser, ResellerAccount, ResellerProfile, RestoreJob, Session, TotpCredential
from shared.session_ids import session_digest
from shared.passwords import verify_password
from shared.validation import ValidationError, validate_domain

from daemon.rpc_policy import POLICY_BY_OPERATION

TOKEN_MAX_AGE = dt.timedelta(days=90)


class AuthenticationError(PermissionError):
    pass


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True)
class Principal:
    role: str
    username: str
    account_id: int | None
    panel_user_id: int | None
    auth_method: str
    session_hash: str | None = None
    reseller_id: int | None = None
    impersonating: bool = False


def _utc(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def resolve_principal(credential: object) -> Principal:
    """Ignore all client identity labels; derive immutable identity from DB."""
    if not isinstance(credential, dict) or set(credential) != {"type", "value"}:
        raise AuthenticationError("panel credential required")
    kind, raw = credential.get("type"), credential.get("value")
    if kind not in ("session", "token", "cluster") or not isinstance(raw, str) or not raw or len(raw) > 4096:
        raise AuthenticationError("invalid panel credential")
    now = dt.datetime.now(dt.timezone.utc)
    with write_session() as db:
        if kind == "cluster":
            digest = hashlib.sha256(raw.encode()).hexdigest()
            peer = db.scalar(select(DnsClusterPeer).where(
                DnsClusterPeer.credential_hash == digest,
                DnsClusterPeer.peer_type == "boron",
                DnsClusterPeer.enabled.is_(True),
            ))
            if peer is None:
                raise AuthenticationError("invalid DNS cluster credential")
            return Principal("cluster", peer.name, None, None, "cluster")
        if kind == "session":
            digest = session_digest(raw)
            session = db.scalar(select(Session).where(Session.session_id == digest))
            if session is None or session.revoked or _utc(session.expires_at) <= now:
                raise AuthenticationError("invalid or expired panel session")
            user = db.get(PanelUser, session.panel_user_id)
            if user is None or user.disabled or user.role not in ("admin", "reseller", "customer"):
                raise AuthenticationError("panel user unavailable")
            imp = db.scalar(select(ImpersonationSession).where(
                ImpersonationSession.session_id == digest,
                ImpersonationSession.ended_at.is_(None),
            ))
            if imp is not None:
                account = db.get(Account, imp.account_id)
                if user.role != "admin" or account is None or imp.admin_panel_user_id != user.id:
                    raise AuthenticationError("invalid impersonation session")
                return Principal("customer", user.username, account.id, user.id,
                                 "session", digest, impersonating=True)
            reseller_id = None
            if user.role == "reseller":
                reseller_id = db.scalar(select(ResellerProfile.id).where(
                    ResellerProfile.panel_user_id == user.id,
                    ResellerProfile.status == "active",
                ))
                if reseller_id is None:
                    raise AuthenticationError("reseller unavailable")
            if user.role == "customer":
                account = db.get(Account, user.account_id) if user.account_id is not None else None
                if account is None or account.status != "active":
                    raise AuthenticationError("customer account unavailable")
            return Principal(user.role, user.username, user.account_id, user.id,
                             "session", digest, reseller_id)

        digest = hashlib.sha256(raw.encode()).hexdigest()
        token = db.scalar(select(ApiToken).where(ApiToken.token_hash == digest))
        if token is None or token.revoked_at is not None or _utc(token.created_at) + TOKEN_MAX_AGE <= now:
            raise AuthenticationError("invalid or expired API token")
        if token.role not in ("admin", "customer"):
            raise AuthenticationError("invalid API token role")
        if token.role == "customer":
            account = db.get(Account, token.account_id) if token.account_id is not None else None
            if account is None or account.status != "active":
                raise AuthenticationError("API token account unavailable")
        return Principal(token.role, token.label, token.account_id, None, "token")


def _target_account_id(db, params: dict, *, allow_new_domain: bool = False) -> int | None:
    username = params.get("username")
    domain = params.get("domain")
    from_username = None
    from_domain = None
    if isinstance(username, str) and username:
        from_username = db.scalar(select(Account.id).where(Account.username == username))
        if from_username is None:
            raise AuthorizationError("account target unavailable")
    if isinstance(domain, str) and domain:
        try:
            domain = validate_domain(domain)
        except ValidationError as exc:
            raise AuthorizationError("domain target unavailable") from exc
        from_domain = db.scalar(select(Domain.account_id).where(Domain.domain == domain))
        mail_owner = db.scalar(select(MailDomain.account_id).where(MailDomain.domain == domain))
        target_owner = from_domain if from_domain is not None else from_username
        if mail_owner is not None and mail_owner != target_owner:
            raise AuthorizationError("domain resource ownership is inconsistent")
        if from_domain is None and not (allow_new_domain and from_username is not None):
            raise AuthorizationError("domain target unavailable")
    if from_username is not None and from_domain is not None and from_username != from_domain:
        raise AuthorizationError("account and domain targets disagree")
    return from_username if from_username is not None else from_domain


def _owns_account(db, principal: Principal, account_id: int) -> bool:
    if principal.role == "admin":
        return True
    if principal.role == "customer":
        return principal.account_id == account_id
    if principal.role == "reseller" and principal.reseller_id is not None:
        return db.scalar(select(ResellerAccount.id).where(
            ResellerAccount.reseller_id == principal.reseller_id,
            ResellerAccount.account_id == account_id,
        )) is not None
    return False


def authorize(op: str, params: dict, principal: Principal | None) -> None:
    disposition = POLICY_BY_OPERATION.get(op)
    if disposition is None:
        raise AuthorizationError("operation has no reviewed policy")
    if disposition == "root_internal":
        raise AuthorizationError("operation is root-internal")
    if disposition == "root_internal_replace_with_login_protocol":
        raise AuthorizationError("legacy login primitive is unavailable")
    if disposition == "login_protocol":
        if principal is not None:
            raise AuthorizationError("login protocol requires an anonymous request")
        return
    if disposition == "cluster_protocol":
        if principal is None or principal.role != "cluster" or principal.auth_method != "cluster":
            raise AuthorizationError("DNS cluster credential required")
        return
    if principal is None:
        raise AuthenticationError("panel credential required")
    if disposition in ("global_admin", "admin_account_username"):
        if principal.role != "admin":
            raise AuthorizationError("administrator credential required")
        return
    if disposition == "account_row_owner_restore_job":
        with write_session() as db:
            job_id = params.get("restore_job_id")
            if isinstance(job_id, bool):
                raise AuthorizationError("restore job target unavailable")
            try:
                job = db.get(RestoreJob, int(job_id))
            except (TypeError, ValueError):
                raise AuthorizationError("restore job target unavailable") from None
            if job is None:
                raise AuthorizationError("restore job target unavailable")
            named_account = _target_account_id(db, {"username": params.get("username")})
            if named_account != job.account_id or not _owns_account(db, principal, job.account_id):
                raise AuthorizationError("restore job ownership required")
        return
    if disposition == "admin_impersonation_issue":
        if principal.role != "admin" or principal.auth_method != "session" or params.get("admin_username") != principal.username:
            raise AuthorizationError("current administrator session required")
        return
    if disposition == "admin_confirmed_update":
        if (principal.role != "admin" or principal.auth_method != "session" or principal.impersonating
                or params.get("confirm") is not True or params.get("initiated_by") != principal.username):
            raise AuthorizationError("confirmed administrator session required")
        with write_session() as db:
            has_totp = db.scalar(select(TotpCredential.id).where(
                TotpCredential.panel_user_id == principal.panel_user_id,
                TotpCredential.enabled.is_(True),
            )) is not None
        if has_totp:
            code = params.get("totp_code")
            if not isinstance(code, str) or not code.strip():
                raise AuthorizationError("second factor required")
            from daemon.totp import check_login_code

            if not check_login_code({"panel_user_id": principal.panel_user_id, "code": code})["valid"]:
                raise AuthorizationError("invalid second factor")
        return
    if disposition == "admin_impersonation_redeem":
        if (principal.role != "admin" or principal.auth_method != "session"
                or params.get("admin_username") != principal.username
                or params.get("admin_panel_user_id") != principal.panel_user_id
                or principal.session_hash != session_digest(params.get("admin_session_id", ""))):
            raise AuthorizationError("current administrator session required")
        return
    if disposition in ("account_username", "account_and_domain", "domain_owner",
                       "mixed_admin_or_account", "mixed_admin_or_account_and_domain",
                       "account_terminal_session_owner"):
        if principal.role == "admin":
            return
        with write_session() as db:
            account_id = _target_account_id(db, params,
                                            allow_new_domain=op in ("domain.add", "mail.create_domain"))
            if account_id is None or not _owns_account(db, principal, account_id):
                raise AuthorizationError("account ownership required")
        return
    if disposition == "customer_self_account":
        if principal.role != "customer" or principal.account_id is None or principal.impersonating:
            raise AuthorizationError("customer session required")
        with write_session() as db:
            target = _target_account_id(db, params)
            if target != principal.account_id:
                raise AuthorizationError("account ownership required")
        return
    if disposition == "self_panel_user_session_only":
        if principal.auth_method != "session" or principal.impersonating or principal.panel_user_id != params.get("panel_user_id"):
            raise AuthorizationError("current panel session required")
        return
    if disposition == "self_panel_user_session_with_password":
        if principal.auth_method != "session" or principal.impersonating or principal.panel_user_id != params.get("panel_user_id"):
            raise AuthorizationError("current panel session required")
        password = params.get("current_password")
        with write_session() as db:
            user = db.get(PanelUser, principal.panel_user_id)
            if not isinstance(password, str) or user is None or not verify_password(password, user.password_hash):
                raise AuthorizationError("current password required")
        return
    if disposition == "current_session_self":
        if principal.auth_method != "session" or principal.session_hash != session_digest(params.get("session_id", "")):
            raise AuthorizationError("current session required")
        return
    if disposition == "current_impersonation_session_self":
        if not principal.impersonating or principal.session_hash != session_digest(params.get("session_id", "")):
            raise AuthorizationError("current impersonation session required")
        return
    if disposition == "self_with_current_password_or_admin":
        if principal.role == "admin":
            return
        if principal.auth_method != "session" or principal.impersonating or params.get("username") != principal.username:
            raise AuthorizationError("current panel session required")
        password = params.get("current_password")
        with write_session() as db:
            user = db.get(PanelUser, principal.panel_user_id)
            if not isinstance(password, str) or user is None or not verify_password(password, user.password_hash):
                raise AuthorizationError("current password required")
        return
    if disposition == "reseller_self_or_owned_account":
        if principal.role != "reseller" or principal.reseller_id is None or params.get("reseller_username") != principal.username:
            raise AuthorizationError("active reseller session required")
        if op == "reseller.account.lifecycle":
            with write_session() as db:
                target = _target_account_id(db, params)
                if target is None or not _owns_account(db, principal, target):
                    raise AuthorizationError("reseller account ownership required")
        return
    raise AuthorizationError("operation policy is incomplete")
