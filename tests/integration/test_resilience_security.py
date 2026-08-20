from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from apps.api.agent_fleet_api.config import get_settings
from apps.api.agent_fleet_api.database import SessionFactory
from apps.api.agent_fleet_api.models_collaboration import Channel, Message, Trace
from apps.api.agent_fleet_api.models_execution import (
    AgentSession,
    Delivery,
    DeliveryQueue,
    PermissionDecision,
    PermissionRequest,
)
from apps.api.agent_fleet_api.models_identity import Actor, Agent, Space, Tenant
from apps.api.agent_fleet_api.models_infrastructure import Worker, WorkerCommand
from apps.api.agent_fleet_api.realtime import RealtimeHub
from apps.api.agent_fleet_api.schemas import (
    MentionInput,
    MessageCreate,
    PermissionDecisionInput,
    TaskCreate,
    TaskUpdate,
)
from apps.api.agent_fleet_api.security import Principal
from apps.api.agent_fleet_api.services.fleet_tool_service import FleetToolService
from apps.api.agent_fleet_api.services.message_service import post_message
from apps.api.agent_fleet_api.services.permission_service import decide_permission
from apps.api.agent_fleet_api.services.task_service import create_task, update_task
from apps.api.agent_fleet_api.services.trace_service import transition_trace
from apps.api.agent_fleet_api.services.worker_event_service import WorkerEventService
from packages.contracts.enums import PermissionDecisionKind
from packages.contracts.worker_protocol import WorkerMessageType, new_envelope
from packages.shared.errors import DomainError, ForbiddenError, NotFoundError
from packages.shared.time import as_utc, utcnow
from scripts.seed_demo import (
    AGENTS,
    AXEL_ACTOR_ID,
    BUSINESS_ID,
    CLIENT_TAXI_ID,
    PERSONAL_ID,
    TENANT_ID,
    USER_ID,
    WORKER_A_ID,
    WORKER_B_ID,
    seed,
)
from services.dispatcher.service import Dispatcher


@dataclass(frozen=True, slots=True)
class Execution:
    delivery_id: UUID
    session_id: UUID
    trace_id: UUID
    generation: int
    worker_id: UUID


@pytest.fixture
async def demo_seeded() -> None:
    await seed()


def _principal() -> Principal:
    return Principal(
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        actor_id=AXEL_ACTOR_ID,
        session_id=uuid4(),
        email="axel@example.com",
        display_name="Axel",
        is_owner=True,
    )


async def _mention_cto(
    *,
    idempotency_key: str = "resilience-human-message",
    trace_policy: dict[str, int | float | bool] | None = None,
) -> tuple[UUID, UUID]:
    cto = AGENTS[0]
    async with SessionFactory() as db:
        created = await post_message(
            db,
            tenant_id=TENANT_ID,
            author_actor_id=AXEL_ACTOR_ID,
            author_type="human",
            channel_id=CLIENT_TAXI_ID,
            data=MessageCreate(
                content="@cto traite ce scénario de résilience",
                mentions=[
                    MentionInput(
                        target_type="agent",
                        target_id=cto["id"],
                        handle_at_creation="cto",
                    )
                ],
                expects_response=True,
            ),
            idempotency_key=idempotency_key,
            commit=False,
        )
        delivery = await db.scalar(
            select(Delivery).where(Delivery.message_id == created.message.id)
        )
        assert delivery is not None
        trace = await db.get(Trace, delivery.trace_id)
        assert trace is not None
        if trace_policy is not None:
            trace.policy = {**trace.policy, **trace_policy}
        await db.commit()
        return created.message.id, delivery.id


async def _online_worker(worker_id: UUID = WORKER_A_ID) -> None:
    async with SessionFactory() as db:
        worker = await db.get(Worker, worker_id)
        assert worker is not None
        worker.status = "online"
        worker.last_heartbeat_at = utcnow()
        worker.max_sessions = 4
        worker.available_sessions = 4
        worker.active_sessions = 0
        await db.commit()


async def _dispatch_once() -> bool:
    dispatcher = Dispatcher(
        SessionFactory,
        get_settings(),
        RealtimeHub(),
        dispatcher_id="resilience-test-dispatcher",
    )
    return await dispatcher.run_once()


async def _dispatch_and_start(
    *, trace_policy: dict[str, int | float | bool] | None = None
) -> Execution:
    await _online_worker()
    _message_id, delivery_id = await _mention_cto(trace_policy=trace_policy)
    assert await _dispatch_once()
    async with SessionFactory() as db:
        delivery = await db.get(Delivery, delivery_id)
        assert delivery is not None
        assert delivery.session_id is not None
        assert delivery.worker_id is not None
        session_id = delivery.session_id
        trace_id = delivery.trace_id
        generation = delivery.execution_generation
        worker_id = delivery.worker_id
        worker = await db.get(Worker, worker_id)
        assert worker is not None
        event = new_envelope(
            message_type=WorkerMessageType.SESSION_STARTED,
            worker_id=worker_id,
            trace_id=trace_id,
            session_id=session_id,
            idempotency_key=f"session-started:{delivery_id}:{generation}",
            payload={
                "delivery_id": str(delivery_id),
                "generation": generation,
                "harness_session_id": f"fake-{session_id}",
                "acp_protocol_version": "1",
                "capabilities": {"load_session": True},
            },
        )
        assert await WorkerEventService(get_settings()).handle(db, worker, event) == "accepted"
    return Execution(delivery_id, session_id, trace_id, generation, worker_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_offline_worker_keeps_delivery_for_bounded_retry(demo_seeded: None) -> None:
    message_id, delivery_id = await _mention_cto()

    assert await _dispatch_once()
    assert not await _dispatch_once()

    async with SessionFactory() as db:
        delivery = await db.get(Delivery, delivery_id)
        assert delivery is not None
        assert delivery.message_id == message_id
        assert delivery.status == "retry_scheduled"
        assert delivery.attempt_count == 0
        assert delivery.error is not None
        assert delivery.error["code"] == "no_compatible_worker"
        assert as_utc(delivery.available_at) > utcnow()
        assert await db.scalar(select(func.count(Delivery.id))) == 1
        assert await db.scalar(select(func.count(WorkerCommand.id))) == 0
        queue = await db.scalar(
            select(DeliveryQueue).where(DeliveryQueue.queue_key == delivery.queue_key)
        )
        assert queue is not None
        assert queue.pending_count == 1
        assert queue.lease_owner is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_delivery_lease_is_reclaimed_without_capacity_leak(
    demo_seeded: None,
) -> None:
    await _online_worker()
    _message_id, delivery_id = await _mention_cto()
    assert await _dispatch_once()

    async with SessionFactory() as db:
        first = await db.get(Delivery, delivery_id)
        assert first is not None
        assert first.session_id is not None
        first_session_id = first.session_id
        first.status = "processing"
        first.lease_expires_at = utcnow() - timedelta(seconds=1)
        first_session = await db.get(AgentSession, first_session_id)
        assert first_session is not None
        first_session.status = "active"
        first_session.harness_session_id = "stale-acp-session"
        queue = await db.scalar(
            select(DeliveryQueue).where(DeliveryQueue.queue_key == first.queue_key)
        )
        assert queue is not None
        queue.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()

    assert not await _dispatch_once()
    async with SessionFactory() as db:
        requeued = await db.get(Delivery, delivery_id)
        assert requeued is not None
        assert requeued.status == "retry_scheduled"
        assert not requeued.active_slot
        assert requeued.attempt_count == 1
        requeued.available_at = utcnow() - timedelta(seconds=1)
        queue = await db.scalar(
            select(DeliveryQueue).where(DeliveryQueue.queue_key == requeued.queue_key)
        )
        assert queue is not None
        queue.next_wake_at = requeued.available_at
        queue.lease_expires_at = None
        await db.commit()

    assert await _dispatch_once()
    async with SessionFactory() as db:
        reclaimed = await db.get(Delivery, delivery_id)
        stale_session = await db.get(AgentSession, first_session_id)
        worker = await db.get(Worker, WORKER_A_ID)
        assert reclaimed is not None
        assert stale_session is not None
        assert worker is not None
        assert reclaimed.status == "claimed"
        assert reclaimed.attempt_count == 2
        assert reclaimed.execution_generation == 2
        assert reclaimed.session_id != first_session_id
        assert stale_session.status == "failed"
        assert not stale_session.is_current
        assert worker.active_sessions == 1
        assert worker.available_sessions == 3
        assert (
            await db.scalar(
                select(func.count(WorkerCommand.id)).where(
                    WorkerCommand.command_type == "cancel_prompt",
                    WorkerCommand.session_id == first_session_id,
                )
            )
            == 1
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_usage_over_trace_budget_stops_trace_and_requests_prompt_cancel(
    demo_seeded: None,
) -> None:
    execution = await _dispatch_and_start(trace_policy={"max_cost_eur": 0.01})
    async with SessionFactory() as db:
        worker = await db.get(Worker, execution.worker_id)
        assert worker is not None
        event = new_envelope(
            message_type=WorkerMessageType.USAGE_UPDATE,
            worker_id=execution.worker_id,
            trace_id=execution.trace_id,
            session_id=execution.session_id,
            idempotency_key=f"usage:{execution.delivery_id}:over-budget",
            payload={"tokens": 10, "cost_eur": "0.02"},
        )
        assert await WorkerEventService(get_settings()).handle(db, worker, event) == "accepted"

    async with SessionFactory() as db:
        trace = await db.get(Trace, execution.trace_id)
        delivery = await db.get(Delivery, execution.delivery_id)
        command = await db.scalar(
            select(WorkerCommand).where(
                WorkerCommand.trace_id == execution.trace_id,
                WorkerCommand.command_type == "cancel_prompt",
            )
        )
        assert trace is not None
        assert delivery is not None
        assert command is not None
        assert trace.status == "limit_reached"
        assert trace.stop_reason == "max_cost"
        assert Decimal(str(trace.cost_eur)) == Decimal("0.020000")
        assert delivery.status == "processing"
        assert command.payload == {"reason": "max_cost"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_denied_permission_resumes_prompt_with_explicit_deny_command(
    demo_seeded: None,
) -> None:
    execution = await _dispatch_and_start()
    async with SessionFactory() as db:
        worker = await db.get(Worker, execution.worker_id)
        assert worker is not None
        event = new_envelope(
            message_type=WorkerMessageType.PERMISSION_REQUEST,
            worker_id=execution.worker_id,
            trace_id=execution.trace_id,
            session_id=execution.session_id,
            idempotency_key=f"permission:{execution.delivery_id}:shell",
            payload={
                "delivery_id": str(execution.delivery_id),
                "generation": execution.generation,
                "request_id": "permission-shell-1",
                "capability": "shell",
                "summary": "Exécuter une commande destructive de test",
                "details": {"command": "rm -rf build/"},
            },
        )
        service = WorkerEventService(get_settings())
        assert await service.handle(db, worker, event) == "accepted"
        assert await service.handle(db, worker, event) == "duplicate"

    async with SessionFactory() as db:
        request = await db.scalar(
            select(PermissionRequest).where(PermissionRequest.session_id == execution.session_id)
        )
        assert request is not None
        assert request.status == "pending"
        await decide_permission(
            db,
            _principal(),
            request.id,
            PermissionDecisionInput(
                decision=PermissionDecisionKind.DENY,
                reason="Action non nécessaire",
            ),
        )
        request_id = request.id

    async with SessionFactory() as db:
        request = await db.get(PermissionRequest, request_id)
        session = await db.get(AgentSession, execution.session_id)
        delivery = await db.get(Delivery, execution.delivery_id)
        trace = await db.get(Trace, execution.trace_id)
        decisions = list(
            (
                await db.scalars(
                    select(PermissionDecision).where(
                        PermissionDecision.permission_request_id == request_id
                    )
                )
            ).all()
        )
        command = await db.scalar(
            select(WorkerCommand).where(
                WorkerCommand.command_type == "deny_permission",
                WorkerCommand.session_id == execution.session_id,
            )
        )
        assert request is not None
        assert session is not None
        assert delivery is not None
        assert trace is not None
        assert request.status == "denied"
        assert [item.decision for item in decisions] == ["deny"]
        assert session.status == "active"
        assert delivery.status == "processing"
        assert delivery.lease_expires_at is not None
        assert trace.status == "running"
        assert command is not None
        assert command.payload["decision"] == "deny"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_trace_cancel_preserves_cancellation_after_late_worker_completion(
    demo_seeded: None,
) -> None:
    execution = await _dispatch_and_start()
    async with SessionFactory() as db:
        await transition_trace(db, _principal(), execution.trace_id, "cancel")

    async with SessionFactory() as db:
        delivery = await db.get(Delivery, execution.delivery_id)
        command = await db.scalar(
            select(WorkerCommand).where(
                WorkerCommand.trace_id == execution.trace_id,
                WorkerCommand.command_type == "cancel_prompt",
            )
        )
        queue = (
            await db.scalar(
                select(DeliveryQueue).where(DeliveryQueue.queue_key == delivery.queue_key)
            )
            if delivery is not None
            else None
        )
        assert delivery is not None
        assert command is not None
        assert queue is not None
        assert delivery.status == "cancelled"
        assert not delivery.active_slot
        assert queue.lease_owner is None
        assert queue.lease_expires_at is None
        message_count_before = int(await db.scalar(select(func.count(Message.id))) or 0)

        worker = await db.get(Worker, execution.worker_id)
        assert worker is not None
        late_completion = new_envelope(
            message_type=WorkerMessageType.SESSION_COMPLETED,
            worker_id=execution.worker_id,
            trace_id=execution.trace_id,
            session_id=execution.session_id,
            idempotency_key=f"late-completion:{execution.delivery_id}",
            payload={
                "delivery_id": str(execution.delivery_id),
                "generation": execution.generation,
                "final_text": "Ce résultat tardif ne doit pas être publié.",
                "tokens": 2,
                "cost_eur": 0,
            },
        )
        assert (
            await WorkerEventService(get_settings()).handle(db, worker, late_completion)
            == "accepted"
        )

    async with SessionFactory() as db:
        trace = await db.get(Trace, execution.trace_id)
        delivery = await db.get(Delivery, execution.delivery_id)
        session = await db.get(AgentSession, execution.session_id)
        assert trace is not None
        assert delivery is not None
        assert session is not None
        assert trace.status == "cancelled"
        assert trace.stop_reason == "cancelled_by_human"
        assert delivery.status == "cancelled"
        assert session.status == "cancelled"
        assert int(await db.scalar(select(func.count(Message.id))) or 0) == message_count_before


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fleet_tools_enforce_session_identity_and_space_channel_scope(
    demo_seeded: None,
) -> None:
    cto = AGENTS[0]
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
            logical_key=f"security:{uuid4()}",
            harness_type="fake",
            status="active",
        )
        db.add(session)
        await db.flush()
        personal_channel_id = await db.scalar(
            select(Channel.id).where(Channel.space_id == PERSONAL_ID).limit(1)
        )
        assert personal_channel_id is not None
        foreign_tenant = Tenant(slug=f"foreign-{uuid4()}", name="Tenant étranger")
        db.add(foreign_tenant)
        await db.flush()
        foreign_space = Space(
            tenant_id=foreign_tenant.id,
            slug="foreign-space",
            name="Espace étranger",
            kind="business",
        )
        db.add(foreign_space)
        await db.flush()
        foreign_channel = Channel(
            tenant_id=foreign_tenant.id,
            space_id=foreign_space.id,
            slug="foreign-channel",
            name="Channel étranger",
            kind="private",
        )
        db.add(foreign_channel)
        await db.flush()
        service = FleetToolService()

        with pytest.raises(ForbiddenError):
            await service.execute(
                db,
                session_id=session.id,
                worker_id=WORKER_A_ID,
                tool_name="fleet.read_channel_history",
                arguments={"channel_id": str(personal_channel_id)},
                call_id="cross-space-read",
            )
        with pytest.raises(ForbiddenError):
            await service.execute(
                db,
                session_id=session.id,
                worker_id=WORKER_A_ID,
                tool_name="fleet.read_channel_history",
                arguments={"channel_id": str(foreign_channel.id)},
                call_id="cross-tenant-read",
            )
        with pytest.raises(ForbiddenError):
            await service.execute(
                db,
                session_id=session.id,
                worker_id=WORKER_A_ID,
                tool_name="fleet.post_message",
                arguments={
                    "channel_id": str(CLIENT_TAXI_ID),
                    "content": "Tentative d’usurpation",
                    "author_id": str(AGENTS[1]["actor_id"]),
                },
                call_id="identity-spoof",
            )
        with pytest.raises(NotFoundError):
            await service.execute(
                db,
                session_id=session.id,
                worker_id=WORKER_B_ID,
                tool_name="fleet.list_agents",
                arguments={"channel_id": str(CLIENT_TAXI_ID)},
                call_id="worker-spoof",
            )
        assert int(await db.scalar(select(func.count(Message.id))) or 0) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_message_idempotency_creates_one_delivery_and_one_queue_item(
    demo_seeded: None,
) -> None:
    first_message_id, first_delivery_id = await _mention_cto(
        idempotency_key="same-message-idempotency-key"
    )
    second_message_id, second_delivery_id = await _mention_cto(
        idempotency_key="same-message-idempotency-key"
    )

    assert second_message_id == first_message_id
    assert second_delivery_id == first_delivery_id
    async with SessionFactory() as db:
        assert await db.scalar(select(func.count(Delivery.id))) == 1
        queue = await db.scalar(select(DeliveryQueue))
        assert queue is not None
        assert queue.pending_count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_task_reassignment_cannot_cross_tenant_or_channel_scope(
    demo_seeded: None,
) -> None:
    async with SessionFactory() as db:
        task = await create_task(
            db,
            principal=_principal(),
            actor_type="human",
            actor_id=AXEL_ACTOR_ID,
            tenant_id=TENANT_ID,
            data=TaskCreate(
                space_id=BUSINESS_ID,
                channel_id=CLIENT_TAXI_ID,
                title="Tâche à réassigner de manière sûre",
            ),
            idempotency_key="secure-task-reassignment",
            commit=False,
        )
        foreign_tenant = Tenant(slug=f"foreign-task-{uuid4()}", name="Tenant étranger")
        db.add(foreign_tenant)
        await db.flush()
        foreign_space = Space(
            tenant_id=foreign_tenant.id,
            slug="foreign-tasks",
            name="Tâches étrangères",
            kind="business",
        )
        db.add(foreign_space)
        await db.flush()
        foreign_actor = Actor(
            tenant_id=foreign_tenant.id,
            space_id=foreign_space.id,
            actor_type="agent",
            display_name="Agent étranger",
        )
        db.add(foreign_actor)
        await db.flush()
        foreign_agent = Agent(
            tenant_id=foreign_tenant.id,
            space_id=foreign_space.id,
            actor_id=foreign_actor.id,
            handle="foreign-agent",
            display_name="Agent étranger",
            role="Hors périmètre",
            instructions="",
        )
        db.add(foreign_agent)
        await db.flush()

        with pytest.raises(NotFoundError):
            await update_task(
                db,
                tenant_id=TENANT_ID,
                actor_type="human",
                actor_id=AXEL_ACTOR_ID,
                task_id=task.id,
                data=TaskUpdate(assigned_agent_id=foreign_agent.id),
                commit=False,
            )
        assert task.assigned_agent_id is None

        outsider_actor = Actor(
            tenant_id=TENANT_ID,
            space_id=BUSINESS_ID,
            actor_type="agent",
            display_name="Agent sans channel",
        )
        db.add(outsider_actor)
        await db.flush()
        outsider = Agent(
            tenant_id=TENANT_ID,
            space_id=BUSINESS_ID,
            actor_id=outsider_actor.id,
            handle="outside-client-taxi",
            display_name="Agent sans channel",
            role="Hors channel",
            instructions="",
        )
        db.add(outsider)
        await db.flush()
        with pytest.raises(DomainError, match="membre du channel"):
            await update_task(
                db,
                tenant_id=TENANT_ID,
                actor_type="human",
                actor_id=AXEL_ACTOR_ID,
                task_id=task.id,
                data=TaskUpdate(assigned_agent_id=outsider.id),
                commit=False,
            )
        assert task.assigned_agent_id is None
