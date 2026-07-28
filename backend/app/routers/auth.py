"""Sign in / sign out."""

from __future__ import annotations

import os

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])

STATE_COOKIE = "adhoc_oauth_state"
# Cookies are Secure everywhere except plain-HTTP localhost, where the browser
# would refuse to store them and sign-in would fail with no visible reason.
SECURE = os.environ.get("ADHOC_INSECURE_COOKIES") != "1"


def _redirect_uri(request: Request) -> str:
    """Must match the redirect URI registered on the Google OAuth client.

    Built from the forwarded host so it is correct behind Vercel's proxy, where
    request.url would otherwise report the internal origin.
    """
    if configured := os.environ.get("ADHOC_OAUTH_REDIRECT_URI"):
        return configured
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}/api/auth/callback"


@router.get("/me")
def me(user: auth.User | None = Depends(auth.current_user)):
    """Who am I, and is signing in even possible here?"""
    return {
        "user": user.as_dict() if user else None,
        "authenticated": user is not None,
        "configured": auth.configured(),
        "dev_mode": auth.DEV_AUTH and bool(auth.DEV_USER),
        "allowed_domain": auth.ALLOWED_DOMAIN,
    }


@router.get("/login")
def login(request: Request, next: str = "/"):
    if not auth.configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Google sign-in is not configured. Set GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET and ADHOC_SESSION_SECRET."
            ),
        )
    state = auth.new_state()
    resp = RedirectResponse(auth.authorize_url(_redirect_uri(request), state))
    # CSRF: the callback compares this against the state Google echoes back.
    resp.set_cookie(
        STATE_COOKIE, f"{state}|{next}",
        httponly=True, secure=SECURE, samesite="lax", max_age=600, path="/",
    )
    return resp


@router.get("/callback")
def callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    adhoc_oauth_state: str | None = Cookie(default=None, alias=STATE_COOKIE),
):
    if error:
        raise HTTPException(status_code=401, detail=f"Google returned: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="No authorization code.")

    expected, _, next_path = (adhoc_oauth_state or "").partition("|")
    if not expected or state != expected:
        raise HTTPException(
            status_code=400,
            detail="Sign-in state did not match. Start again from the sign-in link.",
        )

    claims = auth.exchange_code(code, _redirect_uri(request))
    email = str(claims["email"]).lower()
    session = auth.issue_session(email, str(claims.get("name") or email))

    # Only ever redirect somewhere inside this app: an attacker-supplied `next`
    # of https://elsewhere/ would otherwise make this an open redirect.
    target = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        auth.SESSION_COOKIE, session,
        httponly=True, secure=SECURE, samesite="lax", max_age=auth.SESSION_TTL, path="/",
    )
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp


@router.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE, path="/")
    return resp
