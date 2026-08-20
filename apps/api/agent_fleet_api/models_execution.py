from datetime import datetime
from decimal import Decimal
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from apps.api.agent_fleet_api.model_base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from packages.contracts.enums import DeliveryStatus, SessionStatus


class DeliveryQueue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "delivery_queues"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "channel_id", name="uq_delivery_queues_logical"),
        Index("ix_delivery_queues_wake", "next_wake_at", "lease_expires_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    queue_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    pending_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_wake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Delivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_deliveries_idempotency"),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'dispatched', 'processing', 'waiting_approval', "
            "'completed', 'failed', 'retry_scheduled', 'cancelled', 'expired')",
            name="status",
        ),
        CheckConstraint("attempt_count >= 0 AND max_attempts >= 1", name="attempts"),
        CheckConstraint("depth >= 0", name="depth"),
        Index("ix_deliveries_pending", "status", "available_at", "created_at"),
        Index("ix_deliveries_agent_status", "target_agent_id", "status", "created_at"),
        Index("ix_deliveries_trace_created", "trace_id", "created_at"),
        Index(
            "uq_deliveries_active_queue_slot",
            "queue_key",
            unique=True,
            postgresql_where=text("active_slot IS TRUE"),
            sqlite_where=text("active_slot = 1"),
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    thread_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("threads.id", ondelete="SET NULL"), index=True
    )
    message_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    mention_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("message_mentions.id", ondelete="SET NULL"), index=True
    )
    target_agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    source_agent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    trace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("traces.id", ondelete="CASCADE"), index=True
    )
    parent_delivery_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("deliveries.id", ondelete="SET NULL"), index=True
    )
    task_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default=DeliveryStatus.PENDING.value, nullable=False
    )
    queue_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_message_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    execution_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_slot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    worker_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workers.id", ondelete="SET NULL"), index=True
    )
    session_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    retry_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class AgentSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('starting', 'active', 'waiting_approval', 'completed', 'failed', "
            "'cancelled', 'closed')",
            name="status",
        ),
        Index("ix_agent_sessions_worker_status", "worker_id", "status"),
        Index("ix_agent_sessions_agent_status", "agent_id", "status", "updated_at"),
        Index(
            "uq_agent_sessions_current_logical",
            "tenant_id",
            "logical_key",
            unique=True,
            postgresql_where=text("is_current IS TRUE"),
            sqlite_where=text("is_current = 1"),
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    thread_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("threads.id", ondelete="SET NULL"), index=True
    )
    task_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), index=True
    )
    worker_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workers.id", ondelete="RESTRICT"), index=True
    )
    trace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("traces.id", ondelete="CASCADE"), index=True
    )
    logical_key: Mapped[str] = mapped_column(String(512), nullable=False)
    harness_type: Mapped[str] = mapped_column(String(32), nullable=False)
    harness_session_id: Mapped[str | None] = mapped_column(String(255), index=True)
    protocol_version: Mapped[str] = mapped_column(String(32), default="1", nullable=False)
    negotiated_capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default=SessionStatus.STARTING.value, nullable=False
    )
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_event_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    publication_count_current_turn: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    usage_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_eur: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class SessionEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "session_events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_session_events_sequence"),
        UniqueConstraint("worker_id", "worker_event_id", name="uq_session_events_worker_event"),
        Index("ix_session_events_trace_created", "trace_id", "created_at"),
        Index("ix_session_events_session_created", "session_id", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True
    )
    worker_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workers.id", ondelete="RESTRICT"), index=True
    )
    worker_event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    trace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("traces.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    visible_to_user: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PermissionRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "permission_requests"
    __table_args__ = (
        UniqueConstraint("session_id", "external_request_id", name="uq_permissions_external"),
        CheckConstraint("status IN ('pending', 'approved', 'denied', 'expired')", name="status"),
        Index("ix_permission_requests_pending", "tenant_id", "status", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("traces.id", ondelete="CASCADE"), index=True
    )
    delivery_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("deliveries.id", ondelete="SET NULL"), index=True
    )
    external_request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    capability: Mapped[str] = mapped_column(String(120), nullable=False)
    action_summary: Mapped[str] = mapped_column(Text, nullable=False)
    action_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    workspace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class PermissionDecision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "permission_decisions"
    __table_args__ = (
        UniqueConstraint("permission_request_id", name="uq_permission_decisions_request"),
        CheckConstraint(
            "decision IN ('deny', 'allow_once', 'allow_session', 'allow_agent')",
            name="decision",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    permission_request_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("permission_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decided_by_actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("actors.id", ondelete="RESTRICT"), index=True
    )
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
