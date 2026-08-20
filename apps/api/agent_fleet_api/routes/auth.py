from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from apps.api.agent_fleet_api.dependencies import (
    Config,
    CurrentPrincipal,
    Database,
    MutatingPrincipal,
)
from apps.api.agent_fleet_api.rate_limit import login_limiter
from apps.api.agent_fleet_api.schemas import AuthResponse, BootstrapRequest, LoginRequest
from apps.api.agent_fleet_api.security import clear_auth_cookies, set_auth_cookies
from apps.api.agent_fleet_api.services.auth_service import bootstrap_owner, login, revoke_session

router = APIRouter(prefix="/auth", tags=["auth"])


def _response(result: object) -> AuthResponse:
    principal = result.principal  # type: ignore[attr-defined]
    csrf = result.csrf_token  # type: ignore[attr-defined]
    return AuthResponse(
        user_id=principal.user_id,
        actor_id=principal.actor_id,
        tenant_id=principal.tenant_id,
        email=principal.email,
        display_name=principal.display_name,
        is_owner=principal.is_owner,
        csrf_token=csrf,
    )


@router.post("/bootstrap", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def bootstrap(
    body: BootstrapRequest,
    request: Request,
    response: Response,
    db: Database,
    settings: Config,
    x_bootstrap_token: str = Header(alias="X-Bootstrap-Token"),
) -> AuthResponse:
    result = await bootstrap_owner(
        db,
        request_data=body,
        bootstrap_token=x_bootstrap_token,
        request=request,
        settings=settings,
    )
    set_auth_cookies(
        response,
        session_token=result.session_token,
        csrf_token=result.csrf_token,
        settings=settings,
    )
    return _response(result)


@router.post("/login", response_model=AuthResponse)
async def login_route(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Database,
    settings: Config,
) -> AuthResponse:
    client_key = request.client.host if request.client else "unknown"
    if not await login_limiter.allow(
        f"login:{client_key}", limit=settings.max_login_attempts_per_minute
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Trop de tentatives")
    result = await login(
        db, email=str(body.email), password=body.password, request=request, settings=settings
    )
    if result is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides")
    set_auth_cookies(
        response,
        session_token=result.session_token,
        csrf_token=result.csrf_token,
        settings=settings,
    )
    return _response(result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_route(
    response: Response,
    db: Database,
    settings: Config,
    principal: MutatingPrincipal,
) -> None:
    await revoke_session(db, principal)
    clear_auth_cookies(response, settings)


@router.get("/me", response_model=AuthResponse)
async def me(principal: CurrentPrincipal) -> AuthResponse:
    return AuthResponse(
        user_id=principal.user_id,
        actor_id=principal.actor_id,
        tenant_id=principal.tenant_id,
        email=principal.email,
        display_name=principal.display_name,
        is_owner=principal.is_owner,
    )
