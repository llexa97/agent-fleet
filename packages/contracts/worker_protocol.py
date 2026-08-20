from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.contracts.enums import HarnessType

PROTOCOL_VERSION: Final[Literal["1.0"]] = "1.0"


def _default_protocol_versions() -> list[str]:
    return [PROTOCOL_VERSION]


class ControlMessageType(StrEnum):
    WELCOME = "welcome"
    SYNC_CONFIGURATION = "sync_configuration"
    START_SESSION = "start_session"
    RESUME_SESSION = "resume_session"
    PROMPT = "prompt"
    CANCEL_PROMPT = "cancel_prompt"
    CLOSE_SESSION = "close_session"
    APPROVE_PERMISSION = "approve_permission"
    DENY_PERMISSION = "deny_permission"
    SHUTDOWN_SESSION = "shutdown_session"
    TOOL_RESULT = "tool_result"
    EVENT_ACK = "event_ack"
    PING = "ping"


class WorkerMessageType(StrEnum):
    HELLO = "hello"
    INVENTORY = "inventory"
    HEARTBEAT = "heartbeat"
    ACK = "ack"
    SESSION_STARTED = "session_started"
    SESSION_RESUMED = "session_resumed"
    SESSION_UPDATE = "session_update"
    PERMISSION_REQUEST = "permission_request"
    USAGE_UPDATE = "usage_update"
    SESSION_COMPLETED = "session_completed"
    SESSION_FAILED = "session_failed"
    TOOL_CALL = "tool_call"
    LOG = "log"
    PONG = "pong"


WireMessageType = ControlMessageType | WorkerMessageType


class WorkerCapacity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_sessions: int = Field(ge=1, le=128)
    available_sessions: int = Field(ge=0, le=128)

    @model_validator(mode="after")
    def available_cannot_exceed_maximum(self) -> "WorkerCapacity":
        if self.available_sessions > self.max_sessions:
            raise ValueError("available_sessions ne peut pas dépasser max_sessions")
        return self


class HarnessInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: HarnessType
    adapter: str = Field(min_length=1, max_length=120)
    version: str | None = Field(default=None, max_length=120)
    available: bool
    capabilities: list[str] = Field(default_factory=list, max_length=128)


class WorkspaceInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    display_name: str = Field(min_length=1, max_length=160)
    root: str = Field(min_length=1, max_length=4096)
    read_only: bool = False


class WorkerInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: UUID
    hostname: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=64)
    protocol_versions: list[str] = Field(default_factory=_default_protocol_versions)
    labels: list[str] = Field(default_factory=list, max_length=128)
    capacity: WorkerCapacity
    harnesses: list[HarnessInventory] = Field(default_factory=list, max_length=64)
    workspaces: list[WorkspaceInventory] = Field(default_factory=list, max_length=512)


class WireEnvelope(BaseModel):
    """Message strict du protocole Control Plane ↔ Worker."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    message_type: WireMessageType
    message_id: UUID = Field(default_factory=uuid4)
    command_id: UUID
    worker_id: UUID
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trace_id: UUID | None = None
    session_id: UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=255)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp doit inclure un fuseau horaire")
        return value


class HelloPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_version: str = Field(min_length=1, max_length=64)
    supported_protocol_versions: list[str] = Field(min_length=1, max_length=16)
    boot_id: UUID
    inventory: WorkerInventory


class SessionUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: UUID
    sequence: int = Field(ge=0)
    update_type: Literal[
        "status",
        "agent_message_chunk",
        "plan",
        "tool_call",
        "tool_result",
        "terminal_output",
        "file_change",
        "usage",
        "error",
    ]
    content: dict[str, Any]


def new_envelope(
    *,
    message_type: WireMessageType,
    worker_id: UUID,
    command_id: UUID | None = None,
    trace_id: UUID | None = None,
    session_id: UUID | None = None,
    idempotency_key: str | None = None,
    payload: dict[str, Any] | None = None,
) -> WireEnvelope:
    command = command_id or uuid4()
    return WireEnvelope(
        message_type=message_type,
        command_id=command,
        worker_id=worker_id,
        trace_id=trace_id,
        session_id=session_id,
        idempotency_key=idempotency_key or f"wire:{command}",
        payload=payload or {},
    )
