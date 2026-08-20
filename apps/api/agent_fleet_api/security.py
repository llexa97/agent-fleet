import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type
from fastapi import HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.config import Settings
from apps.api.agent_fleet_api.models_identity import Actor, User, UserSession
from packages.shared.time import as_utc, utcnow

password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65_536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


@dataclass(frozen=True, slots=True)
class Principal:
    tenant_id: UUID
    user_id: UUID
    actor_id: UUID
    session_id: UUID
    email: str
    display_name: str
    is_owner: bool


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(secret: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), secret.encode(), hashlib.sha256).hexdigest()


def hash_network_value(value: str | None, pepper: str) -> str | None:
    if not value:
        return None
    return hash_secret(value, pepper)


def session_cookie_name(settings: Settings) -> str:
    return "__Host-agent_fleet_session" if settings.cookie_secure else "agent_fleet_session"


def set_auth_cookies(
    response: Response,
    *,
    session_token: str,
    csrf_token: str,
    settings: Settings,
) -> None:
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        session_cookie_name(settings),
        session_token,
        max_age=max_age,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        "agent_fleet_csrf",
        csrf_token,
        max_age=max_age,
        secure=settings.cookie_secure,
        httponly=False,
        samesite="strict",
        path="/",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(session_cookie_name(settings), path="/")
    response.delete_cookie("agent_fleet_csrf", path="/")


async def create_user_session(
    db: AsyncSession,
    *,
    user: User,
    request: Request,
    settings: Settings,
) -> tuple[UserSession, str, str]:
    session_token = generate_secret()
    csrf_token = generate_secret()
    now = utcnow()
    session = UserSession(
        tenant_id=user.tenant_id,
        user_id=user.id,
        token_hash=hash_secret(session_token, settings.session_secret),
        csrf_hash=hash_secret(csrf_token, settings.session_secret),
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
        last_seen_at=now,
        ip_hash=hash_network_value(
            request.client.host if request.client else None, settings.session_secret
        ),
        user_agent=(request.headers.get("user-agent") or "")[:512] or None,
    )
    db.add(session)
    await db.flush()
    return session, session_token, csrf_token


async def authenticate_request(
    request: Request,
    db: AsyncSession,
    settings: Settings,
    *,
    require_csrf: bool = False,
) -> Principal:
    token = request.cookies.get(session_cookie_name(settings))
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Authentification requise")
    token_hash = hash_secret(token, settings.session_secret)
    statement = (
        select(UserSession, User, Actor)
        .join(User, User.id == UserSession.user_id)
        .join(
            Actor,
            (Actor.user_id == User.id)
            & (Actor.tenant_id == User.tenant_id)
            & (Actor.actor_type == "human"),
        )
        .where(UserSession.token_hash == token_hash)
    )
    row = (await db.execute(statement)).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session invalide")
    session, user, actor = row
    now = utcnow()
    if (
        session.revoked_at is not None
        or as_utc(session.expires_at) <= now
        or user.status != "active"
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session expirée ou révoquée")
    if require_csrf:
        header_token = request.headers.get("x-csrf-token")
        cookie_token = request.cookies.get("agent_fleet_csrf")
        if (
            not header_token
            or not cookie_token
            or not hmac.compare_digest(header_token, cookie_token)
            or not hmac.compare_digest(
                hash_secret(header_token, settings.session_secret), session.csrf_hash
            )
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Jeton CSRF invalide")
    session.last_seen_at = now
    return Principal(
        tenant_id=user.tenant_id,
        user_id=user.id,
        actor_id=actor.id,
        session_id=session.id,
        email=user.email,
        display_name=user.display_name,
        is_owner=user.is_owner,
    )
