import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.config import Settings
from apps.api.agent_fleet_api.metrics import failed_deliveries, prompt_duration, tool_calls
from apps.api.agent_fleet_api.models_collaboration import Task, Trace
from apps.api.agent_fleet_api.models_execution import (
    AgentSession,
    Delivery,
    DeliveryQueue,
    PermissionRequest,
    SessionEvent,
)
from apps.api.agent_fleet_api.models_identity import Agent
from apps.api.agent_fleet_api.models_infrastructure import (
    Worker,
    WorkerCommand,
    WorkerLog,
    WorkerMessageReceipt,
)
from apps.api.agent_fleet_api.schemas import MessageCreate, TaskUpdate
from apps.api.agent_fleet_api.services.audit import add_internal_event
from apps.api.agent_fleet_api.services.fleet_tool_service import FleetToolService
from apps.api.agent_fleet_api.services.message_service import post_message
from apps.api.agent_fleet_api.services.orchestration_policy import trace_policy
from apps.api.agent_fleet_api.services.task_service import update_task
from packages.contracts.worker_protocol import WireEnvelope, WorkerMessageType
from packages.shared.errors import DomainError
from packages.shared.logging import redact
from packages.shared.time import as_utc, utcnow

_KNOWN_TOOL_METRIC_LABELS = {
    "fleet.list_agents",
    "fleet.get_agent",
    "fleet.list_channel_members",
    "fleet.read_channel_history",
    "fleet.get_thread",
    "fleet.post_message",
    "fleet.reply_message",
    "fleet.mention_agent",
    "fleet.create_task",
    "fleet.delegate_task",
    "fleet.update_task",
    "fleet.complete_task",
    "fleet.fail_task",
    "fleet.request_human_approval",
    "fleet.get_trace",
    "fleet.cancel_trace",
}


class WorkerEventService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.fleet_tools = FleetToolService()

    async def handle(
        self,
        db: AsyncSession,
        worker: Worker,
        envelope: WireEnvelope,
    ) -> str:
        payload_json = json.dumps(envelope.payload, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
        existing = await db.scalar(
            select(WorkerMessageReceipt).where(
                WorkerMessageReceipt.worker_id == worker.id,
                WorkerMessageReceipt.message_id == envelope.message_id,
            )
        )
        if existing is not None:
            if existing.payload_hash != payload_hash:
                return "rejected"
            return "duplicate"
        db.add(
            WorkerMessageReceipt(
                tenant_id=worker.tenant_id,
                worker_id=worker.id,
                message_id=envelope.message_id,
                message_type=str(envelope.message_type),
                payload_hash=payload_hash,
                created_at=utcnow(),
            )
        )
        message_type = WorkerMessageType(envelope.message_type)
        if message_type == WorkerMessageType.ACK:
            await self._handle_command_ack(db, worker, envelope)
        elif message_type in {WorkerMessageType.HEARTBEAT, WorkerMessageType.PONG}:
            await self._handle_heartbeat(db, worker, envelope)
        elif message_type in {
            WorkerMessageType.SESSION_STARTED,
            WorkerMessageType.SESSION_RESUMED,
        }:
            await self._handle_session_started(
                db,
                worker,
                envelope,
                resumed=message_type == WorkerMessageType.SESSION_RESUMED,
            )
        elif message_type == WorkerMessageType.SESSION_UPDATE:
            await self._handle_session_update(db, worker, envelope)
        elif message_type == WorkerMessageType.PERMISSION_REQUEST:
            await self._handle_permission_request(db, worker, envelope)
        elif message_type == WorkerMessageType.USAGE_UPDATE:
            await self._handle_usage(db, worker, envelope)
        elif message_type == WorkerMessageType.TOOL_CALL:
            await self._handle_tool_call(db, worker, envelope)
        elif message_type == WorkerMessageType.SESSION_COMPLETED:
            await self._handle_completed(db, worker, envelope)
        elif message_type == WorkerMessageType.SESSION_FAILED:
            await self._handle_failed(db, worker, envelope)
        elif message_type == WorkerMessageType.LOG:
            await self._handle_log(db, worker, envelope)
        worker.last_heartbeat_at = utcnow()
        await db.commit()
        return "accepted"

    async def _session(
        self, db: AsyncSession, worker: Worker, envelope: WireEnvelope
    ) -> AgentSession:
        if envelope.session_id is None:
            raise DomainError("session_id_required", "session_id est requis", status_code=422)
        session = await db.scalar(
            select(AgentSession).where(
                AgentSession.id == envelope.session_id,
                AgentSession.worker_id == worker.id,
                AgentSession.tenant_id == worker.tenant_id,
            )
        )
        if session is None:
            raise DomainError("unknown_session", "Session worker inconnue", status_code=404)
        return session

    async def _delivery(
        self,
        db: AsyncSession,
        worker: Worker,
        envelope: WireEnvelope,
    ) -> Delivery:
        raw_id = envelope.payload.get("delivery_id")
        if not raw_id:
            raise DomainError("delivery_id_required", "delivery_id est requis", status_code=422)
        delivery = await db.scalar(
            select(Delivery).where(
                Delivery.id == UUID(str(raw_id)),
                Delivery.worker_id == worker.id,
                Delivery.tenant_id == worker.tenant_id,
            )
        )
        if delivery is None:
            raise DomainError("unknown_delivery", "Livraison worker inconnue", status_code=404)
        generation = envelope.payload.get("generation")
        if generation is not None and int(generation) != delivery.execution_generation:
            raise DomainError("stale_generation", "Génération d’exécution périmée", status_code=409)
        return delivery

    async def _handle_command_ack(
        self, db: AsyncSession, worker: Worker, envelope: WireEnvelope
    ) -> None:
        command = await db.scalar(
            select(WorkerCommand).where(
                WorkerCommand.id == envelope.command_id,
                WorkerCommand.worker_id == worker.id,
            )
        )
        if command is None:
            return
        ack_status = str(envelope.payload.get("status", "accepted"))
        command.status = "acked" if ack_status in {"accepted", "duplicate"} else "rejected"
        command.acknowledged_at = utcnow()
        if command.status == "rejected":
            command.last_error = {
                "code": "worker_rejected",
                "message": str(envelope.payload.get("reason", "Commande rejetée")),
            }
            raw_delivery_id = command.payload.get("delivery_id")
            if raw_delivery_id:
                delivery = await db.get(Delivery, UUID(str(raw_delivery_id)))
                if delivery is not None and delivery.status in {
                    "claimed",
                    "dispatched",
                    "processing",
                }:
                    delivery.active_slot = False
                    delivery.lease_owner = None
                    delivery.lease_expires_at = None
                    delivery.error = command.last_error
                    queue = await db.scalar(
                        select(DeliveryQueue).where(DeliveryQueue.queue_key == delivery.queue_key)
                    )
                    if delivery.attempt_count < delivery.max_attempts:
                        delivery.status = "retry_scheduled"
                        delivery.available_at = utcnow() + timedelta(
                            seconds=2 ** min(delivery.attempt_count, 8)
                        )
                        if queue is not None:
                            queue.pending_count += 1
                            queue.next_wake_at = delivery.available_at
                    else:
                        delivery.status = "failed"
                        delivery.completed_at = utcnow()
                    if queue is not None:
                        queue.lease_owner = None
                        queue.lease_expires_at = None
                    session = (
                        await db.get(AgentSession, delivery.session_id)
                        if delivery.session_id
                        else None
                    )
                    if session is not None:
                        session.status = "failed"
                        session.error = command.last_error
                        session.ended_at = utcnow()
                    if command.command_type != "prompt":
                        worker.available_sessions = min(
                            worker.max_sessions, worker.available_sessions + 1
                        )
                        worker.active_sessions = max(0, worker.active_sessions - 1)

    async def _handle_heartbeat(
        self, db: AsyncSession, worker: Worker, envelope: WireEnvelope
    ) -> None:
        capacity = envelope.payload.get("capacity", {})
        if "max_sessions" in capacity:
            worker.max_sessions = max(1, int(capacity["max_sessions"]))
        if "available_sessions" in capacity:
            worker.available_sessions = max(
                0, min(worker.max_sessions, int(capacity["available_sessions"]))
            )
            worker.active_sessions = worker.max_sessions - worker.available_sessions
        worker.status = "online"

    async def _handle_session_started(
        self,
        db: AsyncSession,
        worker: Worker,
        envelope: WireEnvelope,
        *,
        resumed: bool,
    ) -> None:
        session = await self._session(db, worker, envelope)
        delivery = await self._delivery(db, worker, envelope)
        session.status = "active"
        session.harness_session_id = str(
            envelope.payload.get("harness_session_id") or session.harness_session_id or session.id
        )
        session.protocol_version = str(envelope.payload.get("acp_protocol_version", "1"))
        session.negotiated_capabilities = envelope.payload.get("capabilities", {})
        session.started_at = session.started_at or utcnow()
        delivery.status = "processing"
        delivery.started_at = delivery.started_at or utcnow()
        delivery.lease_expires_at = utcnow() + timedelta(
            seconds=self.settings.delivery_lease_seconds
        )
        if delivery.task_id is not None:
            agent = await db.get(Agent, session.agent_id)
            task = await db.get(Task, delivery.task_id)
            if agent is not None and task is not None and task.status == "queued":
                await update_task(
                    db,
                    tenant_id=session.tenant_id,
                    actor_type="agent",
                    actor_id=agent.actor_id,
                    task_id=task.id,
                    data=TaskUpdate(status="running"),
                    commit=False,
                )
        add_internal_event(
            db,
            event_type="session.resumed" if resumed else "session.started",
            tenant_id=session.tenant_id,
            space_id=session.space_id,
            channel_id=session.channel_id,
            actor_type="agent",
            actor_id=None,
            trace_id=session.trace_id,
            idempotency_key=f"session.{'resumed' if resumed else 'started'}:{envelope.message_id}",
            payload={"session_id": str(session.id), "delivery_id": str(delivery.id)},
        )

    async def _handle_session_update(
        self, db: AsyncSession, worker: Worker, envelope: WireEnvelope
    ) -> None:
        session = await self._session(db, worker, envelope)
        delivery = await self._delivery(db, worker, envelope)
        sequence = int(envelope.payload.get("sequence", session.last_event_sequence + 1))
        if (
            await db.scalar(
                select(SessionEvent.id).where(
                    SessionEvent.session_id == session.id,
                    SessionEvent.sequence == sequence,
                )
            )
            is not None
        ):
            return
        event_type = str(envelope.payload.get("update_type", "status"))
        db.add(
            SessionEvent(
                tenant_id=session.tenant_id,
                session_id=session.id,
                worker_id=worker.id,
                worker_event_id=envelope.message_id,
                trace_id=session.trace_id,
                sequence=sequence,
                event_type=event_type,
                payload=redact(envelope.payload.get("content", {})),
                visible_to_user=event_type != "raw_protocol",
                created_at=envelope.timestamp,
            )
        )
        session.last_event_sequence = max(session.last_event_sequence, sequence)
        delivery.lease_expires_at = utcnow() + timedelta(
            seconds=self.settings.delivery_lease_seconds
        )
        add_internal_event(
            db,
            event_type="session.updated",
            tenant_id=session.tenant_id,
            space_id=session.space_id,
            channel_id=session.channel_id,
            actor_type="agent",
            actor_id=None,
            trace_id=session.trace_id,
            idempotency_key=f"session.updated:{envelope.message_id}",
            payload={
                "session_id": str(session.id),
                "sequence": sequence,
                "update_type": event_type,
            },
        )

    async def _handle_permission_request(
        self, db: AsyncSession, worker: Worker, envelope: WireEnvelope
    ) -> None:
        session = await self._session(db, worker, envelope)
        delivery = await self._delivery(db, worker, envelope)
        external_id = str(envelope.payload.get("request_id") or envelope.message_id)
        if (
            await db.scalar(
                select(PermissionRequest.id).where(
                    PermissionRequest.session_id == session.id,
                    PermissionRequest.external_request_id == external_id,
                )
            )
            is not None
        ):
            return
        request = PermissionRequest(
            tenant_id=session.tenant_id,
            space_id=session.space_id,
            agent_id=session.agent_id,
            session_id=session.id,
            trace_id=session.trace_id,
            delivery_id=delivery.id,
            external_request_id=external_id,
            capability=str(envelope.payload.get("capability", "unknown")),
            action_summary=str(envelope.payload.get("summary", "Action demandée")),
            action_details=redact(envelope.payload.get("details", {})),
            workspace_id=session.workspace_id,
            status="pending",
        )
        db.add(request)
        await db.flush()
        session.status = "waiting_approval"
        delivery.status = "waiting_approval"
        delivery.lease_expires_at = None
        trace = await db.get(Trace, session.trace_id)
        if trace is not None:
            trace.status = "waiting_approval"
        add_internal_event(
            db,
            event_type="permission.requested",
            tenant_id=session.tenant_id,
            space_id=session.space_id,
            channel_id=session.channel_id,
            actor_type="agent",
            actor_id=None,
            trace_id=session.trace_id,
            idempotency_key=f"permission.requested:{request.id}",
            payload={"permission_request_id": str(request.id), "session_id": str(session.id)},
        )

    async def _handle_usage(self, db: AsyncSession, worker: Worker, envelope: WireEnvelope) -> None:
        session = await self._session(db, worker, envelope)
        tokens = max(0, int(envelope.payload.get("tokens", 0)))
        cost = max(Decimal("0"), Decimal(str(envelope.payload.get("cost_eur", "0"))))
        await self._apply_usage(db, session, tokens=tokens, cost=cost)

    async def _apply_usage(
        self,
        db: AsyncSession,
        session: AgentSession,
        *,
        tokens: int,
        cost: Decimal,
    ) -> None:
        previous_tokens = session.usage_tokens
        previous_cost = Decimal(str(session.cost_eur))
        session.usage_tokens = max(previous_tokens, tokens)
        session.cost_eur = max(previous_cost, cost)
        trace = await db.get(Trace, session.trace_id)
        if trace is None:
            return
        trace.token_count += session.usage_tokens - previous_tokens
        trace.cost_eur = Decimal(str(trace.cost_eur)) + (session.cost_eur - previous_cost)
        policy = trace_policy(trace)
        limit_reason: str | None = None
        if trace.token_count >= int(policy["max_tokens"]):
            limit_reason = "max_tokens"
        if float(trace.cost_eur) >= float(policy["max_cost_eur"]):
            limit_reason = "max_cost"
        if limit_reason is None or trace.status not in {"running", "waiting_approval"}:
            return
        trace.status = "limit_reached"
        trace.stop_reason = limit_reason
        trace.completed_at = utcnow()
        db.add(
            WorkerCommand(
                tenant_id=session.tenant_id,
                worker_id=session.worker_id,
                trace_id=session.trace_id,
                session_id=session.id,
                command_type="cancel_prompt",
                idempotency_key=f"budget-cancel:{session.id}:{limit_reason}",
                payload={"reason": limit_reason},
                available_at=utcnow(),
            )
        )
        add_internal_event(
            db,
            event_type="trace.limit_reached",
            tenant_id=trace.tenant_id,
            space_id=trace.space_id,
            channel_id=trace.channel_id,
            actor_type="system",
            actor_id=None,
            trace_id=trace.id,
            idempotency_key=f"trace.limit_reached:{trace.id}:{limit_reason}",
            payload={"reason": limit_reason, "session_id": str(session.id)},
        )

    async def _handle_tool_call(
        self, db: AsyncSession, worker: Worker, envelope: WireEnvelope
    ) -> None:
        session = await self._session(db, worker, envelope)
        call_id = str(envelope.payload.get("call_id") or envelope.message_id)
        tool_name = str(envelope.payload.get("tool_name", ""))
        tool_calls.labels(
            tool=tool_name if tool_name in _KNOWN_TOOL_METRIC_LABELS else "unknown"
        ).inc()
        arguments = envelope.payload.get("arguments", {})
        try:
            result = await self.fleet_tools.execute(
                db,
                session_id=session.id,
                worker_id=worker.id,
                tool_name=tool_name,
                arguments=arguments,
                call_id=call_id,
            )
            response_payload = {"call_id": call_id, "ok": True, "result": result}
        except DomainError as exc:
            response_payload = {
                "call_id": call_id,
                "ok": False,
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            }
        except Exception as exc:
            response_payload = {
                "call_id": call_id,
                "ok": False,
                "error": {"code": "tool_internal_error", "message": type(exc).__name__},
            }
        db.add(
            WorkerCommand(
                tenant_id=session.tenant_id,
                worker_id=worker.id,
                trace_id=session.trace_id,
                session_id=session.id,
                command_type="tool_result",
                idempotency_key=f"tool_result:{session.id}:{call_id}",
                payload=response_payload,
                available_at=utcnow(),
            )
        )

    async def _handle_completed(
        self, db: AsyncSession, worker: Worker, envelope: WireEnvelope
    ) -> None:
        session = await self._session(db, worker, envelope)
        delivery = await self._delivery(db, worker, envelope)
        trace = await db.get(Trace, session.trace_id)
        was_cancelled = delivery.status == "cancelled" or (
            trace is not None and trace.status == "cancelled"
        )
        agent = await db.get(Agent, session.agent_id)
        if agent is None:
            raise DomainError("agent_missing", "Agent de session introuvable", status_code=409)
        final_text = str(envelope.payload.get("final_text", "")).strip()
        published_via_tool = bool(envelope.payload.get("published_via_tool")) or (
            session.publication_count_current_turn > 0
        )
        if final_text and not published_via_tool and not was_cancelled:
            await post_message(
                db,
                tenant_id=session.tenant_id,
                author_actor_id=agent.actor_id,
                author_type="agent",
                channel_id=session.channel_id,
                data=MessageCreate(
                    content=final_text,
                    thread_id=session.thread_id,
                    reply_to_id=delivery.message_id,
                    mentions=[],
                    expects_response=False,
                ),
                idempotency_key=f"agent-final:{session.id}:{delivery.id}:{delivery.execution_generation}",
                source_agent_id=agent.id,
                trace_id=session.trace_id,
                parent_delivery_id=delivery.id,
                depth=delivery.depth + 1,
                commit=False,
            )
        now = utcnow()
        await self._apply_usage(
            db,
            session,
            tokens=max(0, int(envelope.payload.get("tokens", 0))),
            cost=max(
                Decimal("0"),
                Decimal(str(envelope.payload.get("cost_eur", "0"))),
            ),
        )
        delivery.status = "cancelled" if was_cancelled else "completed"
        delivery.active_slot = False
        delivery.lease_owner = None
        delivery.lease_expires_at = None
        delivery.completed_at = now
        session.status = "cancelled" if was_cancelled else "completed"
        session.ended_at = now
        if session.started_at is not None:
            prompt_duration.observe(max(0.0, (now - as_utc(session.started_at)).total_seconds()))
        session.publication_count_current_turn = 0
        worker.available_sessions = min(worker.max_sessions, worker.available_sessions + 1)
        worker.active_sessions = max(0, worker.active_sessions - 1)
        queue = await db.scalar(
            select(DeliveryQueue).where(DeliveryQueue.queue_key == delivery.queue_key)
        )
        if queue is not None:
            queue.lease_owner = None
            queue.lease_expires_at = None
            queue.next_wake_at = now
        if trace is not None and not was_cancelled:
            trace.turn_count += 1
            remaining = await db.scalar(
                select(func.count(Delivery.id)).where(
                    Delivery.trace_id == trace.id,
                    Delivery.id != delivery.id,
                    Delivery.status.in_(
                        [
                            "pending",
                            "claimed",
                            "dispatched",
                            "processing",
                            "waiting_approval",
                            "retry_scheduled",
                        ]
                    ),
                )
            )
            if trace.status == "running" and int(remaining or 0) == 0:
                trace.status = "completed"
                trace.completed_at = now
                trace.stop_reason = "all_deliveries_completed"
        event_type = "agent.cancelled" if was_cancelled else "agent.completed"
        add_internal_event(
            db,
            event_type=event_type,
            tenant_id=session.tenant_id,
            space_id=session.space_id,
            channel_id=session.channel_id,
            actor_type="agent",
            actor_id=agent.actor_id,
            trace_id=session.trace_id,
            idempotency_key=f"{event_type}:{delivery.id}:{delivery.execution_generation}",
            payload={"delivery_id": str(delivery.id), "session_id": str(session.id)},
        )

    async def _handle_failed(
        self, db: AsyncSession, worker: Worker, envelope: WireEnvelope
    ) -> None:
        session = await self._session(db, worker, envelope)
        delivery = await self._delivery(db, worker, envelope)
        now = utcnow()
        failed_deliveries.inc()
        session.status = "failed"
        session.ended_at = now
        if session.started_at is not None:
            prompt_duration.observe(max(0.0, (now - as_utc(session.started_at)).total_seconds()))
        session.error = redact(envelope.payload.get("error", {"code": "harness_failed"}))
        delivery.active_slot = False
        delivery.lease_owner = None
        delivery.lease_expires_at = None
        worker.available_sessions = min(worker.max_sessions, worker.available_sessions + 1)
        worker.active_sessions = max(0, worker.active_sessions - 1)
        queue = await db.scalar(
            select(DeliveryQueue).where(DeliveryQueue.queue_key == delivery.queue_key)
        )
        if delivery.attempt_count < delivery.max_attempts:
            delivery.status = "retry_scheduled"
            delivery.available_at = now + timedelta(seconds=2 ** min(delivery.attempt_count, 8))
            delivery.error = session.error
            if queue is not None:
                queue.pending_count += 1
                queue.next_wake_at = delivery.available_at
        else:
            delivery.status = "failed"
            delivery.completed_at = now
            delivery.error = session.error
        if queue is not None:
            queue.lease_owner = None
            queue.lease_expires_at = None
        add_internal_event(
            db,
            event_type="session.failed",
            tenant_id=session.tenant_id,
            space_id=session.space_id,
            channel_id=session.channel_id,
            actor_type="system",
            actor_id=None,
            trace_id=session.trace_id,
            idempotency_key=f"session.failed:{envelope.message_id}",
            payload={
                "delivery_id": str(delivery.id),
                "will_retry": delivery.status == "retry_scheduled",
            },
        )

    async def _handle_log(self, db: AsyncSession, worker: Worker, envelope: WireEnvelope) -> None:
        db.add(
            WorkerLog(
                tenant_id=worker.tenant_id,
                worker_id=worker.id,
                trace_id=envelope.trace_id,
                session_id=envelope.session_id,
                level=str(envelope.payload.get("level", "info"))[:16],
                message=str(envelope.payload.get("message", ""))[:10_000],
                fields=redact(envelope.payload.get("fields", {})),
                created_at=envelope.timestamp,
            )
        )
