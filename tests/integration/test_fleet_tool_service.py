from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from apps.api.agent_fleet_api.database import SessionFactory
from apps.api.agent_fleet_api.models_collaboration import Task, Trace
from apps.api.agent_fleet_api.models_execution import AgentSession
from apps.api.agent_fleet_api.services.fleet_tool_service import FleetToolService
from scripts.seed_demo import (
    AGENTS,
    AXEL_ACTOR_ID,
    BUSINESS_ID,
    CLIENT_TAXI_ID,
    TENANT_ID,
    WORKER_A_ID,
    seed,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delegate_task_uses_session_channel_when_mcp_sends_null() -> None:
    await seed()
    cto = AGENTS[0]
    backend = AGENTS[1]

    async with SessionFactory() as db:
        trace = Trace(
            tenant_id=TENANT_ID,
            space_id=BUSINESS_ID,
            channel_id=CLIENT_TAXI_ID,
            initiator_actor_id=AXEL_ACTOR_ID,
            status="running",
            policy={},
        )
        db.add(trace)
        await db.flush()
        session = AgentSession(
            tenant_id=TENANT_ID,
            space_id=BUSINESS_ID,
            agent_id=cto["id"],
            channel_id=CLIENT_TAXI_ID,
            worker_id=WORKER_A_ID,
            trace_id=trace.id,
            logical_key=f"test:{uuid4()}",
            harness_type="fake",
            status="active",
        )
        db.add(session)
        await db.flush()

        result = await FleetToolService().execute(
            db,
            session_id=session.id,
            worker_id=WORKER_A_ID,
            tool_name="fleet.delegate_task",
            arguments={
                "agent_id": str(backend["id"]),
                "channel_id": None,
                "parent_task_id": None,
                "title": "Tester la délégation MCP",
                "description": "Le channel nul doit hériter de la session.",
                "priority": 2,
            },
            call_id="mcp-null-channel",
        )

        task = await db.scalar(select(Task).where(Task.id == UUID(result["task_id"])))
        assert task is not None
        assert task.channel_id == CLIENT_TAXI_ID
        assert task.assigned_agent_id == backend["id"]
        assert task.status == "queued"
