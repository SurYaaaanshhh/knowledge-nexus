from datetime import datetime, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status

from app.auth import (
    SESSION_COOKIE,
    SESSION_DURATION,
    authenticate_user,
    get_session_user,
    register_user,
    require_current_user,
    revoke_session,
)
from app.config import AUTH_COOKIE_SECURE
from app.schemas import AuthCredentials

router = APIRouter(prefix="/api/auth")


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=int(SESSION_DURATION.total_seconds()),
        expires=datetime.now(timezone.utc) + SESSION_DURATION,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(credentials: AuthCredentials, response: Response):
    user, token = register_user(credentials.email, credentials.password)
    set_session_cookie(response, token)
    return {"user": user}


@router.post("/login")
async def login(credentials: AuthCredentials, response: Response):
    user, token = authenticate_user(credentials.email, credentials.password)
    set_session_cookie(response, token)
    return {"user": user}


@router.post("/logout")
async def logout(
    response: Response,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    revoke_session(session_token)
    response.delete_cookie(
        key=SESSION_COOKIE,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return {"status": "ok"}


@router.get("/me")
async def current_user(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    user = get_session_user(session_token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Please sign in to continue.",
        )
    return {"user": user}
