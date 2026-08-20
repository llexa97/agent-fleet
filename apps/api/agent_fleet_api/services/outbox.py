import asyncio
import json

import redis.asyncio as redis
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.agent_fleet_api.config import Settings
from apps.api.agent_fleet_api.models_governance import InternalEvent
from apps.api.agent_fleet_api.realtime import RealtimeHub
from packages.shared.time import utcnow

logger = structlog.get_logger(__name__)


def serialize_event(event: InternalEvent) -> dict[str, object]:
    return {
        "event_id": str(event.id),
        "event_type": event.event_type,
        "event_version": event.event_version,
        "tenant_id": str(event.tenant_id),
        "space_id": str(event.space_id) if event.space_id else None,
        "channel_id": str(event.channel_id) if event.channel_id else None,
        "actor_type": event.actor_type,
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "trace_id": str(event.trace_id) if event.trace_id else None,
        "correlation_id": str(event.correlation_id),
        "causation_id": str(event.causation_id) if event.causation_id else None,
        "idempotency_key": event.idempotency_key,
        "occurred_at": event.occurred_at.isoformat(),
        "payload": event.payload,
    }


class OutboxPublisher:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        hub: RealtimeHub,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.hub = hub
        self._stop = asyncio.Event()
        self._redis: redis.Redis | None = None

    async def _redis_client(self) -> redis.Redis | None:
        if not self.settings.redis_url:
            return None
        if self._redis is None:
            self._redis = redis.from_url(  # type: ignore[no-untyped-call]
                self.settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
        return self._redis

    def stop(self) -> None:
        self._stop.set()

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    async def publish_once(self) -> int:
        async with self.session_factory() as db:
            events = list(
                (
                    await db.scalars(
                        select(InternalEvent)
                        .where(InternalEvent.published_at.is_(None))
                        .order_by(InternalEvent.occurred_at, InternalEvent.id)
                        .with_for_update(skip_locked=True)
                        .limit(100)
                    )
                ).all()
            )
            if not events:
                return 0
            client = await self._redis_client()
            for event in events:
                payload = serialize_event(event)
                await self.hub.publish_browser(event.tenant_id, payload)
                if client is not None:
                    try:
                        await client.publish(
                            f"agent-fleet:events:{event.tenant_id}",
                            json.dumps(payload, separators=(",", ":")),
                        )
                        if event.event_type == "delivery.created":
                            await client.publish("agent-fleet:dispatcher:wake", str(event.id))
                    except Exception:
                        # Redis est un accélérateur; le polling PostgreSQL garantit la reprise.
                        logger.warning("outbox.redis_unavailable")
                        await client.aclose()
                        self._redis = None
                        client = None
                event.published_at = utcnow()
                event.publish_attempts += 1
            await db.commit()
            return len(events)

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                count = await self.publish_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("outbox.publish_failed")
                count = 0
            if count == 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=0.25)
                except TimeoutError:
                    pass
