from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.agent_fleet_api.model_base import Base
from apps.api.agent_fleet_api.models_collaboration import Trace
from apps.api.agent_fleet_api.schemas import RuntimeBindingInput
from apps.api.agent_fleet_api.services.message_service import normalized_message_hash
from apps.api.agent_fleet_api.services.orchestration_policy import check_delivery_allowed
from packages.contracts.enums import HarnessType
from packages.shared.time import utcnow


def test_real_harness_requires_explicit_worker_and_workspace() -> None:
    with pytest.raises(ValidationError):
        RuntimeBindingInput(harness=HarnessType.CODEX)

    binding = RuntimeBindingInput(
        harness=HarnessType.OPENCODE,
        worker_id=uuid4(),
        workspace_id=uuid4(),
    )
    assert binding.workspace_id is not None


def test_message_normalization_detects_cosmetic_repetition() -> None:
    assert normalized_message_hash("  Même   message\n") == normalized_message_hash("même message")


@pytest.mark.asyncio
async def test_policy_blocks_self_mention() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    agent_id = uuid4()
    trace = Trace(
        tenant_id=uuid4(),
        space_id=uuid4(),
        initiator_actor_id=uuid4(),
        status="running",
        policy={"allow_self_mention": False},
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    async with factory() as db:
        check = await check_delivery_allowed(
            db,
            trace=trace,
            source_agent_id=agent_id,
            target_agent_id=agent_id,
            depth=1,
            message_hash="a" * 64,
        )
    assert not check.allowed
    assert check.reason == "self_mention"
    await engine.dispose()


@pytest.mark.asyncio
async def test_policy_blocks_excessive_depth() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    trace = Trace(
        tenant_id=uuid4(),
        space_id=uuid4(),
        initiator_actor_id=uuid4(),
        status="running",
        policy={"max_hops": 2},
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    async with factory() as db:
        check = await check_delivery_allowed(
            db,
            trace=trace,
            source_agent_id=uuid4(),
            target_agent_id=uuid4(),
            depth=3,
            message_hash="b" * 64,
        )
    assert check.reason == "max_hops"
    await engine.dispose()
