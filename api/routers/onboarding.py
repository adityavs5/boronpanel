"""Run A feature 4: client onboarding wizard. GET/PATCH
/api/v1/accounts/{u}/onboarding -- account-scoped (the customer marks
their own wizard done; an admin can read/reset-check any account's)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/onboarding", tags=["onboarding"])


class OnboardingBody(BaseModel):
    completed: bool
    skipped: bool = False


@api_router.get("")
def get_onboarding(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("onboarding.get", identity, username=username)


@api_router.patch("")
def set_onboarding(username: str, body: OnboardingBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("onboarding.set", identity, username=username, **body.model_dump())
