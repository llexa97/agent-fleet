from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from apps.api.agent_fleet_api.model_base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from packages.contracts.enums import AgentStatus, HarnessType, IsolationMode, SpaceKind


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        CheckConstraint("status IN ('active', 'suspended')", name="status"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_sessions"

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(128))
    user_agent: Mapped[str | None] = mapped_column(String(512))


class Space(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "spaces"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_spaces_tenant_slug"),
        CheckConstraint("kind IN ('business', 'personal', 'custom')", name="kind"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default=SpaceKind.CUSTOM.value, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class Actor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "actors"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('human', 'agent', 'system', 'workflow')", name="actor_type"
        ),
        UniqueConstraint("tenant_id", "user_id", name="uq_actors_tenant_user"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(2048))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Agent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "space_id", "handle", name="uq_agents_space_handle"),
        CheckConstraint(
            "status IN ('active', 'suspended', 'offline', 'busy', 'error')", name="status"
        ),
        Index("ix_agents_space_status", "tenant_id", "space_id", "status"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("actors.id", ondelete="CASCADE"), unique=True
    )
    handle: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(
        String(24), default=AgentStatus.ACTIVE.value, nullable=False
    )
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    tools: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    budget_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    delegation_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class AgentRuntimeBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_runtime_bindings"
    __table_args__ = (
        CheckConstraint(
            "harness IN ('codex', 'claude', 'fake', 'hermes', 'goose', 'gemini', 'opencode')",
            name="harness",
        ),
        CheckConstraint(
            "isolation_mode IN ('per_session', 'per_agent', 'pooled')", name="isolation_mode"
        ),
        Index(
            "uq_runtime_agent_enabled",
            "agent_id",
            unique=True,
            postgresql_where=text("enabled IS TRUE"),
            sqlite_where=text("enabled = 1"),
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    harness: Mapped[str] = mapped_column(String(32), default=HarnessType.FAKE.value, nullable=False)
    worker_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workers.id", ondelete="SET NULL"), index=True
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), index=True
    )
    model: Mapped[str | None] = mapped_column(String(120))
    runner_selector: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    isolation_mode: Mapped[str] = mapped_column(
        String(24), default=IsolationMode.PER_SESSION.value, nullable=False
    )
    runtime_options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AgentPermission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_permissions"
    __table_args__ = (
        UniqueConstraint("agent_id", "capability", name="uq_agent_permissions_capability"),
        CheckConstraint(
            "policy IN ('deny', 'ask', 'allow_once', 'allow_session', 'allow_agent', 'allow')",
            name="policy",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    capability: Mapped[str] = mapped_column(String(120), nullable=False)
    policy: Mapped[str] = mapped_column(String(24), default="ask", nullable=False)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
