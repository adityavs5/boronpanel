"""Mint API tokens in tests the way borond does: bound to an administrator.

borond's dispatcher sets ``_creator_panel_user_id`` from the root-resolved
credential; direct handler calls in tests must supply a real creator too.
"""
from __future__ import annotations

from sqlalchemy import select

from daemon import handlers_auth
from shared.db import write_session
from shared.models import PanelUser

TOKEN_CREATOR_USERNAME = "token-creator"


def token_creator_id(username: str | None = None) -> int:
    """An enabled administrator's id (created on first use)."""
    with write_session() as db:
        if username is not None:
            existing = db.scalar(select(PanelUser.id).where(
                PanelUser.username == username, PanelUser.role == "admin",
            ))
        else:
            existing = db.scalar(select(PanelUser.id).where(
                PanelUser.role == "admin", PanelUser.disabled.is_(False),
            ).order_by(PanelUser.id))
    if existing is not None:
        return existing
    return handlers_auth.create_panel_user({
        "username": username or TOKEN_CREATOR_USERNAME,
        "password": "TokenCreator123!", "role": "admin",
    })["id"]


def mint_token(params: dict, creator: str | None = None) -> dict:
    return handlers_auth.create_api_token({
        **params, "_creator_panel_user_id": token_creator_id(creator),
    })
