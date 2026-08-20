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
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from apps.api.agent_fleet_api.model_base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from packages.contracts.enums import ChannelKind, TaskStatus, TraceStatus


class Channel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "channels"
    __table_args__ = (
        UniqueConstraint("space_id", "slug", name="uq_channels_space_slug"),
        CheckConstraint("kind IN ('discussion', 'project', 'private')", name="kind"),
        Index("ix_channels_tenant_space", "tenant_id", "space_id", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(24), default=ChannelKind.DISCUSSION.value, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ChannelMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "channel_members"
    __table_args__ = (
        UniqueConstraint("channel_id", "actor_id", name="uq_channel_members_actor"),
        CheckConstraint("role IN ('owner', 'admin', 'member', 'viewer')", name="role"),
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
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("actors.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(24), default="member", nullable=False)


class AgentChannelMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_channel_memberships"
    __table_args__ = (
        UniqueConstraint("channel_id", "agent_id", name="uq_agent_memberships_channel_agent"),
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
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    activation_modes: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: ["mention_only", "assigned_only"], nullable=False
    )
    can_read_history: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Thread(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "threads"
    __table_args__ = (Index("ix_threads_channel_updated", "channel_id", "updated_at"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(255))
    root_message_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    created_by_actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("actors.id", ondelete="RESTRICT")
    )
    summary: Mapped[str | None] = mapped_column(Text)
    summary_message_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Trace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "traces"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'paused', 'waiting_approval', 'completed', 'failed', "
            "'cancelled', 'limit_reached')",
            name="status",
        ),
        Index("ix_traces_channel_created", "channel_id", "created_at"),
        Index("ix_traces_status_deadline", "status", "deadline"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("channels.id", ondelete="SET NULL"), index=True
    )
    thread_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("threads.id", ondelete="SET NULL"), index=True
    )
    parent_trace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("traces.id", ondelete="SET NULL"), index=True
    )
    trigger_message_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    trigger_task_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    initiator_actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("actors.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default=TraceStatus.RUNNING.value, nullable=False
    )
    turn_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_depth_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delegation_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parallel_agents_peak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_eur: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"), nullable=False)
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_reason: Mapped[str | None] = mapped_column(String(255))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TraceParticipant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trace_participants"
    __table_args__ = (UniqueConstraint("trace_id", "agent_id", name="uq_trace_participants_agent"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("traces.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    parent_agent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )
    first_delivery_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    turn_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Message(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_messages_tenant_idempotency"),
        CheckConstraint(
            "author_type IN ('human', 'agent', 'system', 'workflow')", name="author_type"
        ),
        Index("ix_messages_channel_created", "channel_id", "created_at", "id"),
        Index("ix_messages_thread_created", "thread_id", "created_at", "id"),
        Index("ix_messages_trace_created", "trace_id", "created_at"),
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
    author_type: Mapped[str] = mapped_column(String(24), nullable=False)
    author_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("actors.id", ondelete="RESTRICT"), index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reply_to_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), index=True
    )
    trace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("traces.id", ondelete="SET NULL"), index=True
    )
    task_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), index=True)
    expects_response: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_technical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class MessageMention(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "message_mentions"
    __table_args__ = (
        UniqueConstraint("message_id", "target_type", "target_id", name="uq_mentions_target"),
        CheckConstraint("target_type IN ('human', 'agent')", name="target_type"),
        Index("ix_mentions_target_created", "target_id", "created_at"),
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
    message_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    target_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    handle_at_creation: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Task(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('backlog', 'queued', 'running', 'waiting_input', 'waiting_approval', "
            "'blocked', 'review', 'completed', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint("priority >= 0 AND priority <= 4", name="priority"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_tasks_tenant_idempotency"),
        Index("ix_tasks_agent_status", "assigned_agent_id", "status", "priority"),
        Index("ix_tasks_channel_created", "channel_id", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("channels.id", ondelete="SET NULL"), index=True
    )
    thread_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("threads.id", ondelete="SET NULL"), index=True
    )
    trace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("traces.id", ondelete="SET NULL"), index=True
    )
    parent_task_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    created_by_actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("actors.id", ondelete="RESTRICT"), index=True
    )
    requester_agent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    assigned_agent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=TaskStatus.BACKLOG.value, nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    workspace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), index=True
    )
    expected_artifacts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class TaskDependency(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (
        UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependencies_pair"),
        CheckConstraint("task_id <> depends_on_task_id", name="not_self"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    depends_on_task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )


class TaskEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "task_events"
    __table_args__ = (Index("ix_task_events_task_created", "task_id", "created_at"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(32))
    new_status: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
