from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.contracts.worker_protocol import (
    ControlMessageType,
    WireEnvelope,
    WorkerCapacity,
    new_envelope,
)


def test_worker_capacity_refuses_impossible_inventory() -> None:
    with pytest.raises(ValidationError):
        WorkerCapacity(max_sessions=2, available_sessions=3)


def test_wire_protocol_is_strict_and_timezone_aware() -> None:
    payload = new_envelope(message_type=ControlMessageType.PING, worker_id=uuid4())
    assert payload.protocol_version == "1.0"
    with pytest.raises(ValidationError):
        WireEnvelope.model_validate(
            {
                **payload.model_dump(),
                "timestamp": datetime.now(),
                "unexpected": True,
            }
        )


def test_wire_round_trip_preserves_command_identity() -> None:
    command_id = uuid4()
    envelope = new_envelope(
        message_type=ControlMessageType.START_SESSION,
        worker_id=uuid4(),
        command_id=command_id,
        payload={"delivery_id": str(uuid4())},
    )
    decoded = WireEnvelope.model_validate_json(envelope.model_dump_json())
    assert decoded.command_id == command_id
    assert decoded.timestamp.tzinfo is not None
