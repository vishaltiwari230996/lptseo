import hashlib
import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.config import settings
from app.security import (
    create_token, is_admin, is_creator, is_geo_editor, verify_google_id_token,
)
from app.services import firestore_repo

router = APIRouter()
logger = logging.getLogger("agentos.auth")

# Shown to every rejected account, whatever the reason. Deliberately says
# nothing about whether the address is known here — a rejected stranger and a
# de-provisioned colleague must be indistinguishable to the caller.
_NOT_ALLOWED = (
    "This Google account is not authorised to use this console. "
    "Ask a workspace admin to grant access."
)


class GoogleLogin(BaseModel):
    credential: str = Field(..., min_length=10, description="Google ID token (JWT)")
    # The browser's IANA timezone (e.g. "Asia/Kolkata"); stamped onto run rows so
    # the admin tables show local time. Defaults to UTC if the client omits it.
    timezone: str = ""


def is_allowed_email(email: str) -> bool:
    """Whether a *verified* Google address is permitted to sign in.

    Holding a valid Google token proves identity, not membership. Sign-in is
    the one door that has to enforce it, because Cloud Run serves this API
    --allow-unauthenticated.

    Allowed when the address is a project owner (Creator — they are named in
    config/env and must never be locked out of their own panel), is listed in
    ALLOWED_EMAILS, or its domain is listed in ALLOWED_EMAIL_DOMAINS. With both
    lists empty nobody but an owner gets in: an unconfigured allowlist means
    "no one", never "everyone".
    """
    address = email.strip().lower()
    if not address:
        return False
    if is_creator(address):
        return True
    if address in settings.allowed_email_set:
        return True
    domain = address.rpartition("@")[2]
    return bool(domain) and domain in settings.allowed_email_domain_set


@router.post("/auth/google")
def google_login(body: GoogleLogin) -> dict:
    """Verify a Google ID token, upsert the user, return an app JWT.

    A fresh session id + the caller's timezone are baked into the token so every
    later request can be attributed to this sign-in and stamped with local time.
    """
    claims = verify_google_id_token(body.credential)
    # Gate BEFORE the upsert: an unauthorised sign-in must not create a user
    # document (no junk tenants, and no "account exists" oracle either).
    if not is_allowed_email(claims["email"]):
        logger.warning("Sign-in refused for non-allowlisted account: %s", claims["email"])
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_ALLOWED)
    user = firestore_repo.get_or_create_google_user(
        email=claims["email"],
        name=claims["name"],
        picture=claims["picture"],
        google_sub=claims["sub"],
    )
    session_id = firestore_repo.new_session_id()
    token = create_token(
        user["id"], user["email"], session_id=session_id,
        timezone=(body.timezone or "UTC").strip() or "UTC",
    )
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user.get("name", ""),
            "picture": user.get("picture", ""),
            "is_admin": is_admin(user["email"]),
            "is_creator": is_creator(user["email"]),
            # Derived from config here, exactly like the two above, and NOT
            # read back out of the token — the token carries no such claim on
            # purpose (see get_current_user), because a claim minted at sign-in
            # outlives its own revocation by the 7-day token life.
            #
            # This is a display hint, never the enforcement. The server decides
            # again on every request in ``require_geo_editor``; all this does is
            # let the console show a non-editor the read-only view instead of
            # buttons that answer 403 when pressed.
            "is_geo_editor": is_geo_editor(user["email"]),
        },
    }


# --------------------------------------------------------------------------- #
# Local-dev sign-in — added by the standalone SEO extract, not in the parent repo
# --------------------------------------------------------------------------- #

class DevLogin(BaseModel):
    """Who to sign in as. Same timezone field as :class:`GoogleLogin`."""

    email: str = Field(..., min_length=3, description="Address to sign in as")
    timezone: str = ""


@router.post("/auth/dev")
def dev_login(body: DevLogin) -> dict:
    """Mint an app JWT without Google, for running this extract on a laptop.

    ``/auth/google`` needs two things a local offline run does not have: a
    Google Web Client ID to verify an ID token against, and a reachable
    Firestore to upsert the user into. Without this endpoint the extract can
    be started but never signed in to, which makes the frontend half of it
    unrunnable.

    Three properties keep it from being a back door:

    * **Both switches, deliberately.** Refused with 404 — not 403, which would
      confirm it exists — unless ``LOCAL_DEV_AUTH=1`` *and* ``APP_ENV`` is
      exactly ``development``. ``app_env`` defaults to "production" when unset
      (see ``config.Settings``), so a deployment that never heard of this
      endpoint cannot be talked into serving it.
    * **The allowlist still applies.** It calls the same
      :func:`is_allowed_email` the Google path does, so this widens *how* you
      prove who you are, never *who* is allowed in. An address that cannot
      sign in with Google cannot sign in here either.
    * **No user document.** The id is derived from the address rather than
      allocated, so repeated local sign-ins stay one stable identity and
      nothing is written anywhere.

    Roles are resolved from config exactly as they are for a Google sign-in —
    ``get_current_user`` re-derives them per request regardless, so there is
    no way to mint a token here that claims more than the address is owed.
    """
    if not (settings.local_dev_auth and settings.app_env == "development"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not Found"
        )

    email = body.email.strip().lower()
    if not is_allowed_email(email):
        logger.warning("Local dev sign-in refused for non-allowlisted account: %s", email)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_ALLOWED)

    # Stable per-address id, so runs recorded across restarts belong to one
    # user. "dev-" prefixed so a row that reaches a shared datastore is
    # obviously not a real account.
    user_id = "dev-" + hashlib.sha256(email.encode("utf-8")).hexdigest()[:24]
    name = email.partition("@")[0].replace(".", " ").title()
    logger.warning("LOCAL DEV SIGN-IN as %s - Google verification bypassed", email)
    token = create_token(
        user_id, email, session_id=firestore_repo.new_session_id(),
        timezone=(body.timezone or "UTC").strip() or "UTC",
    )
    return {
        "token": token,
        "user": {
            "id": user_id,
            "email": email,
            "name": name,
            "picture": "",
            "is_admin": is_admin(email),
            "is_creator": is_creator(email),
            "is_geo_editor": is_geo_editor(email),
        },
    }
