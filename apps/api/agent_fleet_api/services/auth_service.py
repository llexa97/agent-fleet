import hmac
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.config import Settings
from apps.api.agent_fleet_api.models_collaboration import Channel, ChannelMember
from apps.api.agent_fleet_api.models_identity import Actor, Space, Tenant, User, UserSession
from apps.api.agent_fleet_api.schemas import BootstrapRequest
from apps.api.agent_fleet_api.security import (
    Principal,
    create_user_session,
    hash_password,
    verify_password,
)
from apps.api.agent_fleet_api.services.audit import add_audit_event
from packages.shared.time import utcnow


@dataclass(slots=True)
class LoginResult:
    principal: Principal
    session: UserSession
    session_token: str
    csrf_token: str


DEFAULT_SPACES: dict[str, list[str]] = {
    "business": [
        "direction",
        "finance",
        "administratif",
        "marketing",
        "infrastructure",
        "clients",
    ],
    "personal": [
        "assistant-perso",
        "maison",
        "homelab",
        "finance-perso",
        "projets-perso",
    ],
}


async def _serialize_bootstrap(db: AsyncSession) -> None:
    if db.bind and db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(748119331)"))


async def bootstrap_owner(
    db: AsyncSession,
    *,
    request_data: BootstrapRequest,
    bootstrap_token: str,
    request: Request,
    settings: Settings,
) -> LoginResult:
    if not hmac.compare_digest(bootstrap_token, settings.bootstrap_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Jeton bootstrap invalide")
    await _serialize_bootstrap(db)
    if (await db.scalar(select(func.count(User.id)))) != 0:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Le propriétaire existe déjà")

    tenant = Tenant(slug="default", name=request_data.tenant_name)
    db.add(tenant)
    await db.flush()
    user = User(
        tenant_id=tenant.id,
        email=str(request_data.email).strip().lower(),
        display_name=request_data.display_name.strip(),
        password_hash=hash_password(request_data.password),
        is_owner=True,
        status="active",
    )
    db.add(user)
    await db.flush()
    actor = Actor(
        tenant_id=tenant.id,
        actor_type="human",
        user_id=user.id,
        display_name=user.display_name,
        is_active=True,
    )
    db.add(actor)
    await db.flush()

    for kind, channel_slugs in DEFAULT_SPACES.items():
        space = Space(
            tenant_id=tenant.id,
            slug=kind,
            name=kind.capitalize(),
            kind=kind,
            description=f"Espace {kind} isolé",
        )
        db.add(space)
        await db.flush()
        for channel_slug in channel_slugs:
            channel = Channel(
                tenant_id=tenant.id,
                space_id=space.id,
                slug=channel_slug,
                name=channel_slug.replace("-", " ").title(),
                kind="discussion",
            )
            db.add(channel)
            await db.flush()
            db.add(
                ChannelMember(
                    tenant_id=tenant.id,
                    space_id=space.id,
                    channel_id=channel.id,
                    actor_id=actor.id,
                    role="owner",
                )
            )

    session, token, csrf = await create_user_session(
        db, user=user, request=request, settings=settings
    )
    add_audit_event(
        db,
        tenant_id=tenant.id,
        actor_type="human",
        actor_id=actor.id,
        action="auth.owner_bootstrapped",
        resource_type="user",
        resource_id=user.id,
    )
    await db.commit()
    principal = Principal(
        tenant_id=tenant.id,
        user_id=user.id,
        actor_id=actor.id,
        session_id=session.id,
        email=user.email,
        display_name=user.display_name,
        is_owner=True,
    )
    return LoginResult(principal, session, token, csrf)


async def login(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    request: Request,
    settings: Settings,
) -> LoginResult | None:
    user = await db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or user.status != "active" or not verify_password(user.password_hash, password):
        return None
    actor = await db.scalar(
        select(Actor).where(
            Actor.tenant_id == user.tenant_id,
            Actor.user_id == user.id,
            Actor.actor_type == "human",
        )
    )
    if actor is None:
        return None
    session, token, csrf = await create_user_session(
        db, user=user, request=request, settings=settings
    )
    now = utcnow()
    user.last_login_at = now
    add_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_type="human",
        actor_id=actor.id,
        action="auth.login",
        resource_type="user_session",
        resource_id=session.id,
    )
    await db.commit()
    return LoginResult(
        Principal(
            tenant_id=user.tenant_id,
            user_id=user.id,
            actor_id=actor.id,
            session_id=session.id,
            email=user.email,
            display_name=user.display_name,
            is_owner=user.is_owner,
        ),
        session,
        token,
        csrf,
    )


async def revoke_session(db: AsyncSession, principal: Principal) -> None:
    session = await db.get(UserSession, principal.session_id)
    if session is not None and session.tenant_id == principal.tenant_id:
        session.revoked_at = utcnow()
        add_audit_event(
            db,
            tenant_id=principal.tenant_id,
            actor_type="human",
            actor_id=principal.actor_id,
            action="auth.logout",
            resource_type="user_session",
            resource_id=session.id,
        )
        await db.commit()
