from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.contracts.enums import ActorType


class EventEnvelope(BaseModel):
    """Enveloppe durable et versionnée des événements internes."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9_.-]+$")
    event_version: int = Field(default=1, ge=1)
    tenant_id: UUID
    space_id: UUID | None = None
    channel_id: UUID | None = None
    actor_type: ActorType
    actor_id: UUID | None = None
    trace_id: UUID | None = None
    correlation_id: UUID = Field(default_factory=uuid4)
    causation_id: UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=255)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at doit inclure un fuseau horaire")
        return value
