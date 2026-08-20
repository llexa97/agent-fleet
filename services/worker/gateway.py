"""Connexion WebSocket sortante, durable et reconnectable vers le Control Plane."""

from __future__ import annotations

import asyncio
import contextlib
import ssl
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import ValidationError
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from packages.contracts.worker_protocol import (
    ControlMessageType,
    HelloPayload,
    WireEnvelope,
    WorkerInventory,
    WorkerMessageType,
    new_envelope,
)

from . import __version__
from .backoff import ExponentialBackoff
from .config import WorkerConfig
from .errors import ProtocolError
from .journal import CommandClaim, WorkerJournal

EnvelopeHandler = Callable[[WireEnvelope], Awaitable[list[WireEnvelope]]]
InventoryProvider = Callable[[], Awaitable[WorkerInventory]]

_logger = structlog.get_logger(__name__)
_CONTROL_COMMANDS = {item.value for item in ControlMessageType}


class WorkerGateway:
    def __init__(
        self,
        config: WorkerConfig,
        journal: WorkerJournal,
        handler: EnvelopeHandler,
        inventory_provider: InventoryProvider,
    ) -> None:
        self.config = config
        self.journal = journal
        self._handler = handler
        self._inventory_provider = inventory_provider
        self._boot_id = uuid4()
        self._outgoing: asyncio.Queue[tuple[WireEnvelope, bool]] = asyncio.Queue(maxsize=10_000)
        self._connected = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def emit(self, envelope: WireEnvelope, *, durable: bool = True) -> None:
        if envelope.worker_id != self.config.worker.id:
            raise ProtocolError("un message sortant porte l'identité d'un autre worker")
        if durable:
            await self.journal.enqueue(envelope)
        await self._outgoing.put((envelope, durable))

    async def run(self, stop: asyncio.Event) -> None:
        plane = self.config.control_plane
        backoff = ExponentialBackoff(
            plane.backoff_initial_seconds,
            plane.backoff_max_seconds,
            plane.backoff_jitter_ratio,
        )
        while not stop.is_set():
            try:
                token = plane.resolve_token().get_secret_value()
                ssl_context = self._ssl_context()
                async with connect(
                    self._connection_url(),
                    additional_headers={
                        "Authorization": f"Bearer {token}",
                        "X-Agent-Fleet-Worker-ID": str(self.config.worker.id),
                    },
                    open_timeout=plane.connect_timeout_seconds,
                    close_timeout=5,
                    max_size=plane.max_message_bytes,
                    ping_interval=None,
                    ssl=ssl_context,
                ) as websocket:
                    backoff.reset()
                    self._connected.set()
                    await self._run_connection(websocket, stop)
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, OSError, TimeoutError, ProtocolError) as exc:
                await _logger.awarning(
                    "worker_gateway_disconnected",
                    worker_id=str(self.config.worker.id),
                    error_type=type(exc).__name__,
                )
            finally:
                self._connected.clear()
            if not stop.is_set():
                await self._wait_or_stop(stop, backoff.next_delay())

    async def _run_connection(self, websocket: Any, stop: asyncio.Event) -> None:
        self._discard_stale_queue_entries()
        await self._send_direct(websocket, await self._hello())
        for envelope in await self.journal.pending():
            await self._send_direct(websocket, envelope, durable=True)

        tasks = {
            asyncio.create_task(self._send_loop(websocket), name="worker-ws-send"),
            asyncio.create_task(self._receive_loop(websocket), name="worker-ws-receive"),
            asyncio.create_task(self._heartbeat_loop(websocket), name="worker-ws-heartbeat"),
            asyncio.create_task(stop.wait(), name="worker-stop-wait"),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in done:
            if task.cancelled():
                continue
            exception = task.exception()
            if exception is not None:
                raise exception

    async def _send_loop(self, websocket: Any) -> None:
        while True:
            envelope, durable = await self._outgoing.get()
            try:
                await self._send_direct(websocket, envelope, durable=durable)
            finally:
                self._outgoing.task_done()

    async def _receive_loop(self, websocket: Any) -> None:
        async for raw in websocket:
            if isinstance(raw, bytes):
                if len(raw) > self.config.control_plane.max_message_bytes:
                    raise ProtocolError("message WebSocket trop volumineux")
                raw = raw.decode("utf-8")
            if (
                not isinstance(raw, str)
                or len(raw.encode()) > self.config.control_plane.max_message_bytes
            ):
                raise ProtocolError("message WebSocket invalide ou trop volumineux")
            try:
                envelope = WireEnvelope.model_validate_json(raw)
            except ValidationError as exc:
                raise ProtocolError("enveloppe worker invalide") from exc
            await self._process_incoming(envelope)

    async def _process_incoming(self, envelope: WireEnvelope) -> None:
        if envelope.worker_id != self.config.worker.id:
            raise ProtocolError("commande destinée à un autre worker")
        message_type = str(envelope.message_type)
        if message_type not in _CONTROL_COMMANDS:
            raise ProtocolError(f"type de commande Control Plane interdit: {message_type}")
        if message_type == ControlMessageType.EVENT_ACK.value:
            raw_id = envelope.payload.get("received_message_id")
            try:
                message_id = UUID(str(raw_id))
            except (TypeError, ValueError) as exc:
                raise ProtocolError("event_ack sans received_message_id valide") from exc
            status = str(envelope.payload.get("status", "rejected"))
            if status in {"accepted", "duplicate"}:
                await self.journal.acknowledge(message_id)
            else:
                error = envelope.payload.get("error")
                await self.journal.reject(
                    message_id,
                    error if isinstance(error, dict) else {"code": "event_rejected"},
                )
            return
        if message_type == ControlMessageType.PING.value:
            await self.emit(
                new_envelope(
                    message_type=WorkerMessageType.PONG,
                    worker_id=self.config.worker.id,
                    command_id=envelope.command_id,
                    trace_id=envelope.trace_id,
                    session_id=envelope.session_id,
                    idempotency_key=f"pong:{envelope.command_id}",
                    payload={"ping_message_id": str(envelope.message_id)},
                ),
                durable=False,
            )
            return
        if message_type == ControlMessageType.WELCOME.value:
            return

        claim = await self.journal.begin_command(envelope)
        if claim is not CommandClaim.NEW:
            await self._emit_ack(envelope, status="duplicate")
            return
        try:
            responses = await self._handler(envelope)
            for response in responses:
                await self.emit(response)
            await self.journal.complete_command(
                envelope.command_id,
                {"response_message_ids": [str(item.message_id) for item in responses]},
            )
            await self._emit_ack(envelope, status="accepted")
        except Exception as exc:
            error = {"code": "worker_command_failed", "type": type(exc).__name__}
            await self.journal.fail_command(envelope.command_id, error)
            await self._emit_ack(envelope, status="rejected", error=error)

    async def _heartbeat_loop(self, websocket: Any) -> None:
        while True:
            await asyncio.sleep(self.config.control_plane.heartbeat_seconds)
            inventory = await self._inventory_provider()
            envelope = new_envelope(
                message_type=WorkerMessageType.HEARTBEAT,
                worker_id=self.config.worker.id,
                idempotency_key=f"heartbeat:{self._boot_id}:{uuid4()}",
                payload={
                    "boot_id": str(self._boot_id),
                    "capacity": inventory.capacity.model_dump(mode="json"),
                },
            )
            await self._send_direct(websocket, envelope)

    async def _hello(self) -> WireEnvelope:
        inventory = await self._inventory_provider()
        payload = HelloPayload(
            worker_version=__version__,
            supported_protocol_versions=inventory.protocol_versions,
            boot_id=self._boot_id,
            inventory=inventory,
        )
        return new_envelope(
            message_type=WorkerMessageType.HELLO,
            worker_id=self.config.worker.id,
            idempotency_key=f"hello:{self._boot_id}",
            payload=payload.model_dump(mode="json"),
        )

    async def _emit_ack(
        self,
        command: WireEnvelope,
        *,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> None:
        await self.emit(
            new_envelope(
                message_type=WorkerMessageType.ACK,
                worker_id=self.config.worker.id,
                command_id=command.command_id,
                trace_id=command.trace_id,
                session_id=command.session_id,
                idempotency_key=f"ack:{command.command_id}:{status}",
                payload={
                    "acked_message_id": str(command.message_id),
                    "status": status,
                    "error": error,
                    "reason": error["code"] if error is not None else None,
                },
            ),
            durable=False,
        )

    async def _send_direct(
        self, websocket: Any, envelope: WireEnvelope, *, durable: bool = False
    ) -> None:
        body = envelope.model_dump_json()
        if len(body.encode()) > self.config.control_plane.max_message_bytes:
            raise ProtocolError("message worker sortant trop volumineux")
        await websocket.send(body)
        if durable:
            await self.journal.mark_sent(envelope.message_id)

    def _discard_stale_queue_entries(self) -> None:
        # Les messages durables sont rejoués depuis SQLite. Les messages éphémères
        # (heartbeat/pong) perdent volontairement leur utilité après reconnexion.
        while True:
            try:
                self._outgoing.get_nowait()
            except asyncio.QueueEmpty:
                return
            else:
                self._outgoing.task_done()

    def _ssl_context(self) -> ssl.SSLContext | None:
        if self.config.control_plane.url.startswith("ws://"):
            return None
        context = ssl.create_default_context()
        if self.config.control_plane.ca_file is not None:
            context.load_verify_locations(cafile=self.config.control_plane.ca_file)
        return context

    def _connection_url(self) -> str:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        parsed = urlsplit(self.config.control_plane.url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.setdefault("worker_id", str(self.config.worker.id))
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
        )

    @staticmethod
    async def _wait_or_stop(stop: asyncio.Event, delay: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=delay)
