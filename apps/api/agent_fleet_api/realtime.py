import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

import structlog
from fastapi import WebSocket

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class WorkerSocket:
    websocket: WebSocket
    boot_id: UUID
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RealtimeHub:
    def __init__(self) -> None:
        self._browsers: defaultdict[UUID, set[WebSocket]] = defaultdict(set)
        self._workers: dict[UUID, WorkerSocket] = {}
        self._lock = asyncio.Lock()

    async def add_browser(self, tenant_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            self._browsers[tenant_id].add(websocket)

    async def remove_browser(self, tenant_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            self._browsers[tenant_id].discard(websocket)
            if not self._browsers[tenant_id]:
                self._browsers.pop(tenant_id, None)

    async def publish_browser(self, tenant_id: UUID, payload: dict[str, object]) -> None:
        async with self._lock:
            targets = list(self._browsers.get(tenant_id, set()))
        stale: list[WebSocket] = []
        for websocket in targets:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        if stale:
            async with self._lock:
                for websocket in stale:
                    self._browsers[tenant_id].discard(websocket)

    async def add_worker(
        self, worker_id: UUID, websocket: WebSocket, boot_id: UUID
    ) -> WorkerSocket:
        connection = WorkerSocket(websocket=websocket, boot_id=boot_id)
        async with self._lock:
            previous = self._workers.get(worker_id)
            self._workers[worker_id] = connection
        if previous is not None and previous.websocket is not websocket:
            try:
                await previous.websocket.close(code=1012, reason="Connexion remplacée")
            except Exception as exc:
                logger.debug("worker.previous_socket_close_failed", error=type(exc).__name__)
        return connection

    async def remove_worker(self, worker_id: UUID, websocket: WebSocket) -> bool:
        async with self._lock:
            current = self._workers.get(worker_id)
            if current is None or current.websocket is not websocket:
                return False
            del self._workers[worker_id]
            return True

    async def worker(self, worker_id: UUID) -> WorkerSocket | None:
        async with self._lock:
            return self._workers.get(worker_id)

    async def connected_workers(self) -> set[UUID]:
        async with self._lock:
            return set(self._workers)


hub = RealtimeHub()
