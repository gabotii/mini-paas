from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response, status
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import (
    AuthServiceDep,
    CurrentUser,
    LimiterDep,
    SettingsDep,
    bearer_scheme,
    rate_limit,
)
from app.core.config import Settings
from app.core.errors import Unauthorized
from app.core.rate_limit import Rule
from app.core.redis import get_async_redis
from app.core.security import decode_access_token
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut, WebSocketTicket
from app.services.auth_service import Session
from app.services.ws_tickets import TICKET_TTL_SECONDS, WebSocketTickets

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "paas_refresh"
COOKIE_PATH = "/api/auth"


def _set_refresh_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_ttl_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=COOKIE_PATH,
    )


def _token_response(response: Response, settings: Settings, session: Session) -> TokenResponse:
    _set_refresh_cookie(response, settings, session.refresh_token)
    return TokenResponse(
        access_token=session.access.token,
        expires_in=session.access.expires_in,
        user=UserOut.model_validate(session.user),
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("register", "rate_limit_register"))],
    summary="Create an account and start a session",
)
async def register(
    data: RegisterRequest, response: Response, service: AuthServiceDep, settings: SettingsDep
) -> TokenResponse:
    return _token_response(response, settings, await service.register(data))


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit("login", "rate_limit_login"))],
    summary="Log in with email and password",
)
async def login(
    data: LoginRequest,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> TokenResponse:
    # A second, per-account budget slows down distributed guessing against one email.
    await limiter.hit("login-email", data.email, Rule.parse(settings.rate_limit_login))
    return _token_response(response, settings, await service.login(data))


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate the refresh cookie and get a new access token",
)
async def refresh(
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    paas_refresh: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    try:
        return _token_response(response, settings, await service.refresh(paas_refresh))
    except Unauthorized:
        response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)
        raise


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="End the session")
async def logout(
    service: AuthServiceDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    paas_refresh: Annotated[str | None, Cookie()] = None,
) -> Response:
    user_id = None
    if credentials is not None:
        try:
            user_id = decode_access_token(settings, credentials.credentials)
        except Unauthorized:
            user_id = None
    await service.logout(paas_refresh, user_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)
    return response


@router.get("/me", response_model=UserOut, summary="Current user")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.post(
    "/ws-ticket",
    response_model=WebSocketTicket,
    dependencies=[Depends(rate_limit("ws-ticket", "rate_limit_ws_ticket", per="user"))],
    summary="Exchange the access token for a single-use WebSocket ticket (30 s)",
)
async def ws_ticket(user: CurrentUser) -> WebSocketTicket:
    ticket = await WebSocketTickets(get_async_redis()).issue(user.id)
    return WebSocketTicket(ticket=ticket, expires_in=TICKET_TTL_SECONDS)
