import asyncio
import random
from datetime import timedelta
from uuid import UUID

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.agent_fleet_api.config import Settings
from apps.api.agent_fleet_api.models_collaboration import Trace
from apps.api.agent_fleet_api.models_execution import AgentSession, Delivery, DeliveryQueue
from apps.api.agent_fleet_api.models_identity import Agent, AgentRuntimeBinding
from apps.api.agent_fleet_api.models_infrastructure import (
    Worker,
    WorkerCommand,
    WorkerHarness,
    Workspace,
)
from apps.api.agent_fleet_api.realtime import RealtimeHub
from apps.api.agent_fleet_api.services.audit import add_internal_event
from apps.api.agent_fleet_api.services.context_builder import ContextBuilder
from apps.api.agent_fleet_api.services.orchestration_policy import (
    current_trace_limit,
    trace_policy,
)
from packages.contracts.worker_protocol import ControlMessageType, new_envelope
from packages.shared.time import utcnow

logger = structlog.get_logger(__name__)


class Dispatcher:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        hub: RealtimeHub,
        *,
        dispatcher_id: str,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.hub = hub
        self.dispatcher_id = dispatcher_id
        self.context_builder = ContextBuilder()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        logger.info("dispatcher.started", dispatcher_id=self.dispatcher_id)
        while not self._stop.is_set():
            try:
                progressed = await self.run_once()
                await self.flush_commands()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("dispatcher.iteration_failed", dispatcher_id=self.dispatcher_id)
                progressed = False
            if not progressed:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.settings.dispatcher_poll_seconds
                    )
                except TimeoutError:
                    pass
        logger.info("dispatcher.stopped", dispatcher_id=self.dispatcher_id)

    async def _select_worker(
        self,
        db: AsyncSession,
        binding: AgentRuntimeBinding,
        *,
        existing_worker_id: UUID | None = None,
    ) -> tuple[Worker, Workspace | None] | None:
        connected = await self.hub.connected_workers()
        if self.settings.embedded_dispatcher and not connected:
            return None
        heartbeat_cutoff = utcnow() - timedelta(seconds=self.settings.worker_offline_after_seconds)
        statement = (
            select(Worker)
            .join(
                WorkerHarness,
                (WorkerHarness.worker_id == Worker.id)
                & (WorkerHarness.harness_type == binding.harness)
                & (WorkerHarness.available.is_(True)),
            )
            .where(
                Worker.tenant_id == binding.tenant_id,
                Worker.status == "online",
                Worker.revoked_at.is_(None),
                Worker.last_heartbeat_at >= heartbeat_cutoff,
            )
            .order_by(Worker.active_sessions, Worker.name)
            .with_for_update(skip_locked=True)
        )
        if connected:
            statement = statement.where(Worker.id.in_(connected))
        if binding.worker_id is not None:
            statement = statement.where(Worker.id == binding.worker_id)
        candidates = list((await db.scalars(statement)).all())
        required_labels = set(binding.runner_selector.get("labels", []))
        workspace: Workspace | None = None
        if binding.workspace_id is not None:
            workspace = await db.scalar(
                select(Workspace).where(
                    Workspace.id == binding.workspace_id,
                    Workspace.tenant_id == binding.tenant_id,
                    Workspace.status == "available",
                )
            )
            if workspace is None:
                return None
            candidates = [item for item in candidates if item.id == workspace.worker_id]
        if existing_worker_id is not None:
            candidates.sort(
                key=lambda item: (item.id != existing_worker_id, item.active_sessions, item.name)
            )
        for worker in candidates:
            has_capacity = worker.available_sessions > 0 or worker.id == existing_worker_id
            if has_capacity and required_labels.issubset(set(worker.labels)):
                return worker, workspace
        return None

    async def _retry_without_worker(
        self,
        db: AsyncSession,
        queue: DeliveryQueue,
        delivery: Delivery,
    ) -> None:
        delay = min(60, 2 ** min(delivery.attempt_count, 6))
        delivery.status = "retry_scheduled"
        delivery.available_at = utcnow() + timedelta(seconds=delay)
        delivery.error = {
            "code": "no_compatible_worker",
            "message": "Aucun worker en ligne ne satisfait harness, labels et workspace",
            "retry_in_seconds": delay,
        }
        queue.next_wake_at = delivery.available_at
        queue.lease_owner = None
        queue.lease_expires_at = None
        add_internal_event(
            db,
            event_type="delivery.retry_scheduled",
            tenant_id=delivery.tenant_id,
            space_id=delivery.space_id,
            channel_id=delivery.channel_id,
            actor_type="system",
            actor_id=None,
            trace_id=delivery.trace_id,
            idempotency_key=f"delivery.retry.no_worker:{delivery.id}:{delivery.execution_generation}",
            payload={"delivery_id": str(delivery.id), "reason": "no_compatible_worker"},
        )

    async def _defer_for_capacity(
        self,
        db: AsyncSession,
        queue: DeliveryQueue,
        delivery: Delivery,
        *,
        code: str,
        message: str,
    ) -> None:
        delay = max(1.0, self.settings.dispatcher_poll_seconds * 4)
        delivery.status = "retry_scheduled"
        delivery.available_at = utcnow() + timedelta(seconds=delay)
        delivery.error = {"code": code, "message": message, "retry_in_seconds": delay}
        queue.next_wake_at = delivery.available_at
        queue.lease_owner = None
        queue.lease_expires_at = None

    async def _reap_expired(self, db: AsyncSession) -> None:
        now = utcnow()
        expired = list(
            (
                await db.scalars(
                    select(Delivery)
                    .where(
                        Delivery.active_slot.is_(True),
                        Delivery.lease_expires_at.is_not(None),
                        Delivery.lease_expires_at < now,
                        Delivery.status.in_(
                            ["claimed", "dispatched", "processing", "waiting_approval"]
                        ),
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for delivery in expired:
            session = (
                await db.get(AgentSession, delivery.session_id)
                if delivery.session_id is not None
                else None
            )
            if session is not None and session.status in {
                "starting",
                "active",
                "waiting_approval",
            }:
                launch_commands = list(
                    (
                        await db.scalars(
                            select(WorkerCommand).where(
                                WorkerCommand.session_id == session.id,
                                WorkerCommand.command_type.in_(
                                    ["start_session", "resume_session", "prompt"]
                                ),
                                WorkerCommand.status.in_(["pending", "sent"]),
                            )
                        )
                    ).all()
                )
                may_be_running = session.harness_session_id is not None or any(
                    command.status == "sent" for command in launch_commands
                )
                for command in launch_commands:
                    command.status = "cancelled"
                    command.last_error = {
                        "code": "delivery_lease_expired",
                        "message": "Commande remplacée après expiration du lease",
                    }
                if may_be_running:
                    db.add(
                        WorkerCommand(
                            tenant_id=session.tenant_id,
                            worker_id=session.worker_id,
                            trace_id=session.trace_id,
                            session_id=session.id,
                            command_type="cancel_prompt",
                            idempotency_key=(
                                f"lease-cancel:{session.id}:{delivery.execution_generation}"
                            ),
                            payload={
                                "reason": "delivery_lease_expired",
                                "delivery_id": str(delivery.id),
                                "generation": delivery.execution_generation,
                            },
                            available_at=now,
                        )
                    )
                session.status = "failed"
                session.is_current = False
                session.ended_at = now
                session.error = {
                    "code": "delivery_lease_expired",
                    "message": "La session n’a pas renouvelé son lease",
                }
                worker = await db.get(Worker, session.worker_id)
                if worker is not None:
                    worker.available_sessions = min(
                        worker.max_sessions, worker.available_sessions + 1
                    )
                    worker.active_sessions = max(0, worker.active_sessions - 1)
            delivery.active_slot = False
            delivery.lease_owner = None
            delivery.lease_expires_at = None
            if delivery.attempt_count >= delivery.max_attempts:
                delivery.status = "failed"
                delivery.error = {"code": "lease_exhausted", "message": "Nombre de reprises épuisé"}
            else:
                delivery.status = "retry_scheduled"
                delivery.available_at = now + timedelta(seconds=2 ** min(delivery.attempt_count, 8))
                queue = await db.scalar(
                    select(DeliveryQueue).where(DeliveryQueue.queue_key == delivery.queue_key)
                )
                if queue is not None:
                    queue.pending_count += 1
                    queue.next_wake_at = delivery.available_at
                    queue.lease_owner = None
                    queue.lease_expires_at = None

    async def run_once(self) -> bool:
        now = utcnow()
        async with self.session_factory() as db:
            async with db.begin():
                await self._reap_expired(db)
                queue = await db.scalar(
                    select(DeliveryQueue)
                    .where(
                        DeliveryQueue.pending_count > 0,
                        or_(
                            DeliveryQueue.next_wake_at.is_(None), DeliveryQueue.next_wake_at <= now
                        ),
                        or_(
                            DeliveryQueue.lease_expires_at.is_(None),
                            DeliveryQueue.lease_expires_at <= now,
                        ),
                    )
                    .order_by(DeliveryQueue.next_wake_at, DeliveryQueue.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if queue is None:
                    return False
                queue.lease_owner = self.dispatcher_id
                queue.lease_expires_at = now + timedelta(
                    seconds=self.settings.delivery_lease_seconds
                )
                active_in_queue = await db.scalar(
                    select(Delivery)
                    .where(
                        Delivery.queue_key == queue.queue_key,
                        Delivery.active_slot.is_(True),
                        Delivery.status.in_(
                            ["claimed", "dispatched", "processing", "waiting_approval"]
                        ),
                    )
                    .order_by(Delivery.created_at)
                    .limit(1)
                )
                if active_in_queue is not None:
                    queue.lease_owner = f"active:{active_in_queue.id}"
                    queue.lease_expires_at = active_in_queue.lease_expires_at or (
                        now + timedelta(seconds=self.settings.delivery_lease_seconds)
                    )
                    return True
                delivery = await db.scalar(
                    select(Delivery)
                    .where(
                        Delivery.queue_key == queue.queue_key,
                        Delivery.status.in_(["pending", "retry_scheduled"]),
                        Delivery.available_at <= now,
                        Delivery.active_slot.is_(False),
                    )
                    .order_by(Delivery.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if delivery is None:
                    queue.pending_count = 0
                    queue.lease_owner = None
                    queue.lease_expires_at = None
                    return True
                trace = await db.get(Trace, delivery.trace_id)
                agent = await db.get(Agent, delivery.target_agent_id)
                binding = await db.scalar(
                    select(AgentRuntimeBinding).where(
                        AgentRuntimeBinding.agent_id == delivery.target_agent_id,
                        AgentRuntimeBinding.enabled.is_(True),
                    )
                )
                if (
                    trace is None
                    or trace.status != "running"
                    or agent is None
                    or agent.status != "active"
                ):
                    delivery.status = (
                        "cancelled" if trace and trace.status == "cancelled" else "failed"
                    )
                    delivery.error = {
                        "code": "dispatch_policy",
                        "message": "Trace ou agent inactif",
                    }
                    queue.pending_count = max(0, queue.pending_count - 1)
                    queue.lease_owner = None
                    queue.lease_expires_at = None
                    return True
                if binding is None:
                    await self._retry_without_worker(db, queue, delivery)
                    return True
                limit_reason = current_trace_limit(trace)
                if limit_reason is not None:
                    delivery.status = "cancelled"
                    delivery.error = {
                        "code": limit_reason,
                        "message": "La limite centrale de la trace est atteinte",
                    }
                    trace.status = "limit_reached"
                    trace.stop_reason = limit_reason
                    trace.completed_at = now
                    queue.pending_count = max(0, queue.pending_count - 1)
                    queue.lease_owner = None
                    queue.lease_expires_at = None
                    add_internal_event(
                        db,
                        event_type="trace.limit_reached",
                        tenant_id=trace.tenant_id,
                        space_id=trace.space_id,
                        channel_id=trace.channel_id,
                        actor_type="system",
                        actor_id=None,
                        trace_id=trace.id,
                        idempotency_key=f"trace.limit_reached:{trace.id}:{delivery.id}",
                        payload={"reason": limit_reason, "delivery_id": str(delivery.id)},
                    )
                    return True
                active_for_agent = int(
                    await db.scalar(
                        select(func.count(Delivery.id)).where(
                            Delivery.tenant_id == delivery.tenant_id,
                            Delivery.target_agent_id == agent.id,
                            Delivery.active_slot.is_(True),
                        )
                    )
                    or 0
                )
                if active_for_agent >= agent.max_concurrency:
                    await self._defer_for_capacity(
                        db,
                        queue,
                        delivery,
                        code="agent_concurrency_limit",
                        message="La limite de concurrence de l'agent est atteinte",
                    )
                    return True
                active_trace_agent_ids = set(
                    (
                        await db.scalars(
                            select(Delivery.target_agent_id)
                            .where(
                                Delivery.trace_id == trace.id,
                                Delivery.active_slot.is_(True),
                            )
                            .distinct()
                        )
                    ).all()
                )
                maximum_parallel = int(trace_policy(trace)["max_parallel_agents"])
                if (
                    agent.id not in active_trace_agent_ids
                    and len(active_trace_agent_ids) >= maximum_parallel
                ):
                    await self._defer_for_capacity(
                        db,
                        queue,
                        delivery,
                        code="trace_parallel_limit",
                        message="La limite d'agents parallèles de la trace est atteinte",
                    )
                    return True
                logical_key = ":".join(
                    [
                        str(agent.id),
                        str(delivery.channel_id),
                        str(delivery.task_id or delivery.thread_id or delivery.channel_id),
                        str(binding.workspace_id or "none"),
                    ]
                )
                session = await db.scalar(
                    select(AgentSession).where(
                        AgentSession.tenant_id == delivery.tenant_id,
                        AgentSession.logical_key == logical_key,
                        AgentSession.is_current.is_(True),
                    )
                )
                selected = await self._select_worker(
                    db,
                    binding,
                    existing_worker_id=session.worker_id if session is not None else None,
                )
                if selected is None:
                    await self._retry_without_worker(db, queue, delivery)
                    return True
                worker, workspace = selected

                def new_session() -> AgentSession:
                    return AgentSession(
                        tenant_id=delivery.tenant_id,
                        space_id=delivery.space_id,
                        agent_id=agent.id,
                        channel_id=delivery.channel_id,
                        thread_id=delivery.thread_id,
                        task_id=delivery.task_id,
                        workspace_id=binding.workspace_id,
                        worker_id=worker.id,
                        trace_id=delivery.trace_id,
                        logical_key=logical_key,
                        harness_type=binding.harness,
                        status="starting",
                    )

                if session is None or session.worker_id != worker.id:
                    if session is not None:
                        session.is_current = False
                    session = new_session()
                    db.add(session)
                    await db.flush()
                    command_type = "start_session"
                elif session.status == "active" and session.harness_session_id:
                    command_type = "prompt"
                else:
                    capabilities = session.negotiated_capabilities or {}
                    session_capabilities = capabilities.get("sessionCapabilities", {})
                    can_resume = bool(
                        capabilities.get("load_session")
                        or capabilities.get("resume_session")
                        or capabilities.get("resume")
                        or session_capabilities.get("resume")
                    )
                    if session.harness_session_id and can_resume:
                        command_type = "resume_session"
                        session.status = "starting"
                    else:
                        session.is_current = False
                        session = new_session()
                        db.add(session)
                        await db.flush()
                        command_type = "start_session"
                context = await self.context_builder.build(db, delivery)
                delivery.status = "claimed"
                delivery.error = None
                delivery.active_slot = True
                delivery.lease_owner = self.dispatcher_id
                delivery.lease_expires_at = now + timedelta(
                    seconds=self.settings.delivery_lease_seconds
                )
                delivery.claimed_at = now
                delivery.attempt_count += 1
                delivery.execution_generation += 1
                delivery.worker_id = worker.id
                delivery.session_id = session.id
                trace.parallel_agents_peak = max(
                    trace.parallel_agents_peak,
                    len(active_trace_agent_ids | {agent.id}),
                )
                if command_type != "prompt":
                    worker.available_sessions = max(0, worker.available_sessions - 1)
                    worker.active_sessions += 1
                queue.pending_count = max(0, queue.pending_count - 1)
                payload = {
                    "delivery_id": str(delivery.id),
                    "generation": delivery.execution_generation,
                    "tenant_id": str(delivery.tenant_id),
                    "space_id": str(delivery.space_id),
                    "channel_id": str(delivery.channel_id),
                    "agent_id": str(agent.id),
                    "agent_actor_id": str(agent.actor_id),
                    "harness": binding.harness,
                    "model": binding.model,
                    "workspace_id": workspace.external_id if workspace else None,
                    "system_prompt": context.system_prompt,
                    "prompt": context.prompt,
                    "context": context.structured,
                }
                command = WorkerCommand(
                    tenant_id=delivery.tenant_id,
                    worker_id=worker.id,
                    trace_id=delivery.trace_id,
                    session_id=session.id,
                    command_type=command_type,
                    idempotency_key=(
                        f"{command_type}:delivery:{delivery.id}:generation:{delivery.execution_generation}"
                    ),
                    payload=payload,
                    status="pending",
                    available_at=now,
                )
                db.add(command)
                add_internal_event(
                    db,
                    event_type="delivery.claimed",
                    tenant_id=delivery.tenant_id,
                    space_id=delivery.space_id,
                    channel_id=delivery.channel_id,
                    actor_type="system",
                    actor_id=None,
                    trace_id=delivery.trace_id,
                    idempotency_key=f"delivery.claimed:{delivery.id}:{delivery.execution_generation}",
                    payload={
                        "delivery_id": str(delivery.id),
                        "worker_id": str(worker.id),
                        "session_id": str(session.id),
                    },
                )
        await self.flush_commands(worker_id=worker.id)
        return True

    async def flush_commands(self, worker_id: UUID | None = None) -> int:
        now = utcnow()
        async with self.session_factory() as db:
            statement = (
                select(WorkerCommand)
                .where(
                    WorkerCommand.status.in_(["pending", "sent"]),
                    WorkerCommand.available_at <= now,
                )
                .order_by(WorkerCommand.created_at)
                .limit(50)
            )
            if worker_id is not None:
                statement = statement.where(WorkerCommand.worker_id == worker_id)
            commands = list((await db.scalars(statement)).all())
            sent = 0
            for command in commands:
                connection = await self.hub.worker(command.worker_id)
                if connection is None:
                    continue
                try:
                    envelope = new_envelope(
                        message_type=ControlMessageType(command.command_type),
                        worker_id=command.worker_id,
                        command_id=command.id,
                        trace_id=command.trace_id,
                        session_id=command.session_id,
                        idempotency_key=command.idempotency_key,
                        payload=command.payload,
                    )
                    async with connection.send_lock:
                        await connection.websocket.send_text(envelope.model_dump_json())
                    command.status = "sent"
                    command.sent_at = now
                    command.attempt_count += 1
                    command.available_at = now + timedelta(
                        seconds=min(30, 2 ** min(command.attempt_count, 5))
                    )
                    if command.payload.get("delivery_id"):
                        delivery = await db.get(Delivery, UUID(command.payload["delivery_id"]))
                        if delivery is not None and delivery.worker_id == command.worker_id:
                            delivery.status = "dispatched"
                            delivery.dispatched_at = now
                    sent += 1
                except Exception as exc:
                    command.last_error = {"code": "send_failed", "message": type(exc).__name__}
                    command.available_at = now + timedelta(
                        seconds=random.uniform(1, min(30, 2 ** min(command.attempt_count + 1, 5)))
                    )
            await db.commit()
            return sent
