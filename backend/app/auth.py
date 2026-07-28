"""Authentication.

Before this, `confirmed_by` was a text input the browser filled in, so the audit
trail recorded what someone typed rather than who they were. Price
confirmations and Good to Bill approvals are signoff; they need an identity the
user cannot choose.

Google OIDC, restricted to one hosted domain, with the session in an HttpOnly
signed cookie issued by this backend. Chosen over the alternatives because:

  * Vercel Deployment Protection gates the deployment but does not hand the
    FastAPI service a verified user, so `approved_by` would still have nothing
    trustworthy to write. Worth turning on as a second layer; not sufficient
    alone.
  * Auth.js/NextAuth assumes a Next.js server. The frontend here is plain Vite.
  * Anything with its own user table means passwords to store and reset.

The `hd` claim is checked *and* the email domain is checked. `hd` alone is the
documented mechanism but is absent for consumer accounts, and a missing claim
must fail closed rather than skip the check.

No token is stored server-side: the cookie is a signed, expiring assertion, so
there is no session table to keep and serverless invocations share no state.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from fastapi import Cookie, Depends, HTTPException, Request

# ------------------------------------------------------------------ configuration
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

# The Google hosted domain allowed to sign in. Everything else is rejected even
# with a valid Google account.
ALLOWED_DOMAIN = os.environ.get("ADHOC_ALLOWED_DOMAIN", "sesolabor.com").lower()

# Signs the session cookie. Without it there is no way to issue a session, so
# the app runs unauthenticated-but-locked rather than pretending to be secure.
SESSION_SECRET = os.environ.get("ADHOC_SESSION_SECRET", "")
SESSION_COOKIE = "adhoc_session"
SESSION_TTL = int(os.environ.get("ADHOC_SESSION_TTL", str(12 * 3600)))

# Comma-separated emails that may reopen closed periods and edit mappings.
ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ADHOC_ADMIN_EMAILS", "").split(",")
    if e.strip()
}

# Local development only. Set ADHOC_DEV_USER to an email to skip Google
# entirely. Guarded by an explicit second flag so that setting one variable by
# accident in a deployed environment cannot open the app: ADHOC_DEV_AUTH must
# also be exactly "1", and it is never set on Vercel.
DEV_AUTH = os.environ.get("ADHOC_DEV_AUTH") == "1"
DEV_USER = os.environ.get("ADHOC_DEV_USER", "")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and SESSION_SECRET)


@dataclass(frozen=True)
class User:
    email: str
    name: str
    is_admin: bool

    def as_dict(self) -> dict[str, Any]:
        return {"email": self.email, "name": self.name, "is_admin": self.is_admin}


# ------------------------------------------------------------------- cookie codec
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: bytes) -> str:
    return _b64e(hmac.new(SESSION_SECRET.encode(), payload, hashlib.sha256).digest())


def issue_session(email: str, name: str) -> str:
    body = json.dumps(
        {"email": email, "name": name, "exp": int(time.time()) + SESSION_TTL},
        separators=(",", ":"),
    ).encode()
    return f"{_b64e(body)}.{_sign(body)}"


def read_session(token: str | None) -> User | None:
    if not token or not SESSION_SECRET:
        return None
    try:
        body_b64, signature = token.split(".", 1)
        body = _b64d(body_b64)
    except (ValueError, TypeError):
        return None
    # compare_digest, not ==: a plain comparison leaks how much of the signature
    # matched through its timing.
    if not hmac.compare_digest(signature, _sign(body)):
        return None
    try:
        claims = json.loads(body)
    except json.JSONDecodeError:
        return None
    if claims.get("exp", 0) < time.time():
        return None
    email = str(claims.get("email", "")).lower()
    if not email.endswith(f"@{ALLOWED_DOMAIN}"):
        # The domain is re-checked on every request, not just at login, so
        # tightening ADHOC_ALLOWED_DOMAIN takes effect without waiting for
        # already-issued cookies to expire.
        return None
    return User(email=email, name=str(claims.get("name") or email), is_admin=email in ADMIN_EMAILS)


# ------------------------------------------------------------------- OIDC flow
def authorize_url(redirect_uri: str, state: str) -> str:
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            # Ask Google to show only accounts on our domain. A hint, not a
            # control — the claims are still verified below.
            "hd": ALLOWED_DOMAIN,
            "prompt": "select_account",
        }
    )


def new_state() -> str:
    return secrets.token_urlsafe(24)


def exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    """Swap the authorization code for an id_token and return its claims."""
    data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode()
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed host
        token = json.loads(resp.read().decode())

    id_token = token.get("id_token")
    if not id_token:
        raise HTTPException(status_code=401, detail="Google did not return an id_token.")

    # The id_token arrives over a direct TLS call to Google's token endpoint
    # using the client secret, so the transport authenticates it; the signature
    # does not need re-verifying here. What does need checking is that the
    # claims are the ones we require.
    try:
        claims = json.loads(_b64d(id_token.split(".")[1]))
    except (IndexError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Malformed id_token.") from exc

    if claims.get("aud") != GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=401, detail="id_token was issued for another client.")
    if not claims.get("email_verified"):
        raise HTTPException(status_code=403, detail="That Google account has no verified email.")

    email = str(claims.get("email", "")).lower()
    # Fail closed when `hd` is absent: consumer accounts have no hosted domain,
    # and treating "no claim" as "claim satisfied" would let any gmail.com
    # address through whenever Google omitted it.
    if claims.get("hd", "").lower() != ALLOWED_DOMAIN or not email.endswith(f"@{ALLOWED_DOMAIN}"):
        raise HTTPException(
            status_code=403,
            detail=f"Sign in with your @{ALLOWED_DOMAIN} account.",
        )
    return claims


# ------------------------------------------------------------------ dependencies
def current_user(
    request: Request,
    adhoc_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User | None:
    """The signed-in user, or None. Never raises — use `require_user` for that."""
    if DEV_AUTH and DEV_USER:
        # Local only. Visible in /api/auth/me as dev_mode so it cannot be
        # mistaken for a real session while testing.
        return User(
            email=DEV_USER.lower(),
            name=os.environ.get("ADHOC_DEV_USER_NAME", DEV_USER),
            is_admin=True,
        )
    return read_session(adhoc_session)


def require_user(user: User | None = Depends(current_user)) -> User:
    """Guards every action that records who did it."""
    if user is None:
        if not configured() and not DEV_AUTH:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Authentication is not configured, so this action cannot record "
                    "who performed it. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET "
                    "and ADHOC_SESSION_SECRET."
                ),
            )
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="That action is restricted to administrators.",
        )
    return user
