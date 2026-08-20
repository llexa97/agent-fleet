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
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from apps.api.agent_fleet_api.model_base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from packages.shared.time import utcnow


class Workflow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("space_id", "name", name="uq_workflows_space_name"),
        CheckConstraint("status IN ('draft', 'active', 'paused', 'archived')", name="status"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("actors.id", ondelete="RESTRICT"), unique=True
    )
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(80), nullable=False)
    trigger_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    budget_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    webhook_secret_ref: Mapped[str | None] = mapped_column(String(255))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("actors.id", ondelete="RESTRICT")
    )


class WorkflowRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_workflow_runs_idempotency"),
        UniqueConstraint("workflow_id", "trigger_event_id", name="uq_workflow_runs_event"),
        CheckConstraint(
            "status IN ('pending', 'running', 'waiting', 'completed', 'failed', 'cancelled')",
            name="status",
        ),
        Index("ix_workflow_runs_status_created", "status", "created_at"),
        Index("ix_workflow_runs_due", "status", "available_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workflows.id", ondelete="RESTRICT"), index=True
    )
    trace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("traces.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(80), nullable=False)
    trigger_event_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("internal_events.id", ondelete="SET NULL"), index=True
    )
    trigger_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    current_action: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class WorkflowEventReceipt(UUIDPrimaryKeyMixin, Base):
    """Curseur durable par workflow; un événement filtré ne bloque jamais les suivants."""

    __tablename__ = "workflow_event_receipts"
    __table_args__ = (
        UniqueConstraint("workflow_id", "event_id", name="uq_workflow_event_receipts_event"),
        Index("ix_workflow_event_receipts_event", "event_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("internal_events.id", ondelete="CASCADE"), index=True
    )
    matched: Mapped[bool] = mapped_column(Boolean, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class InternalEvent(UUIDPrimaryKeyMixin, Base):
    """Transactional outbox; PostgreSQL reste la source de vérité."""

    __tablename__ = "internal_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_internal_events_idempotency"),
        Index("ix_internal_events_unpublished", "published_at", "occurred_at"),
        Index("ix_internal_events_tenant_occurred", "tenant_id", "occurred_at", "id"),
        Index("ix_internal_events_trace", "trace_id", "occurred_at"),
    )

    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    trace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("traces.id", ondelete="SET NULL"), index=True
    )
    correlation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    causation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    publish_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class IdempotencyRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "scope", "idempotency_key", name="uq_idempotency_scope_key"),
        Index("ix_idempotency_expires", "expires_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(80))
    resource_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    """Journal applicatif append-only; aucun service ne fournit update/delete."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_tenant_created", "tenant_id", "created_at", "id"),
        Index("ix_audit_events_actor_created", "actor_id", "created_at"),
        Index("ix_audit_events_resource", "resource_type", "resource_id", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    tenant_scope_verified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), default="success", nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
