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


class Worker(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_workers_tenant_name"),
        CheckConstraint(
            "status IN ('registered', 'online', 'offline', 'draining', 'revoked', 'error')",
            name="status",
        ),
        Index("ix_workers_status_heartbeat", "tenant_id", "status", "last_heartbeat_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(255))
    version: Mapped[str | None] = mapped_column(String(64))
    protocol_version: Mapped[str | None] = mapped_column(String(32))
    boot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[str] = mapped_column(String(24), default="registered", nullable=False)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    max_sessions: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    available_sessions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_sessions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    logical_address: Mapped[str | None] = mapped_column(String(255))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class WorkerCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "worker_credentials"
    __table_args__ = (Index("ix_worker_credentials_active", "worker_id", "revoked_at"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    worker_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workers.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    token_hint: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_from_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("worker_credentials.id", ondelete="SET NULL")
    )


class WorkerHarness(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "worker_harnesses"
    __table_args__ = (
        UniqueConstraint("worker_id", "harness_type", name="uq_worker_harnesses_type"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    worker_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workers.id", ondelete="CASCADE"), index=True
    )
    harness_type: Mapped[str] = mapped_column(String(32), nullable=False)
    adapter: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str | None] = mapped_column(String(120))
    available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    health: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("worker_id", "external_id", name="uq_workspaces_worker_external"),
        CheckConstraint("status IN ('available', 'offline', 'disabled', 'error')", name="status"),
        Index("ix_workspaces_tenant_worker", "tenant_id", "worker_id", "status"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), index=True
    )
    worker_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workers.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    # Le chemin est un inventaire venant du worker; aucune route utilisateur n'accepte ce champ.
    root: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_root: Mapped[str] = mapped_column(Text, nullable=False)
    read_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="available", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class WorkerCommand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "worker_commands"
    __table_args__ = (
        UniqueConstraint("worker_id", "idempotency_key", name="uq_worker_commands_idempotency"),
        CheckConstraint(
            "status IN ('pending', 'sent', 'acked', 'rejected', 'failed', 'cancelled')",
            name="status",
        ),
        Index("ix_worker_commands_pending", "worker_id", "status", "available_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    worker_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workers.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("traces.id", ondelete="SET NULL"), index=True
    )
    session_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    command_type: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class WorkerMessageReceipt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "worker_message_receipts"
    __table_args__ = (
        UniqueConstraint("worker_id", "message_id", name="uq_worker_receipts_message"),
        Index("ix_worker_receipts_created", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    worker_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workers.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkerLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "worker_logs"
    __table_args__ = (Index("ix_worker_logs_worker_created", "worker_id", "created_at"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    worker_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workers.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    session_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
