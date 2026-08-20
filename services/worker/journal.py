"""Petit journal SQLite durable pour déduplication et reprise du worker."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

import aiosqlite

from packages.contracts.worker_protocol import WireEnvelope


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CommandClaim(StrEnum):
    NEW = "new"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StoredSession:
    logical_session_id: UUID
    acp_session_id: str
    harness_type: str
    workspace_id: str
    state: str
    metadata: dict[str, Any]


class WorkerJournal:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> WorkerJournal:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=FULL")
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS processed_commands (
                command_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                message_type TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('processing','completed','failed')),
                result_json TEXT,
                error_json TEXT,
                received_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox (
                message_id TEXT PRIMARY KEY,
                envelope_json TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                sent_at TEXT,
                acknowledged_at TEXT,
                rejected_at TEXT,
                rejection_json TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_outbox_pending
                ON outbox(acknowledged_at, created_at);
            CREATE TABLE IF NOT EXISTS sessions (
                logical_session_id TEXT PRIMARY KEY,
                acp_session_id TEXT NOT NULL,
                harness_type TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                state TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );
            """
        )
        await self._db.commit()
        return self

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _connection(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Le journal SQLite n'est pas ouvert")
        return self._db

    async def begin_command(self, envelope: WireEnvelope) -> CommandClaim:
        db = self._connection()
        now = _now()
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO processed_commands
                (command_id, idempotency_key, message_type, status, received_at, updated_at)
            VALUES (?, ?, ?, 'processing', ?, ?)
            """,
            (
                str(envelope.command_id),
                envelope.idempotency_key,
                str(envelope.message_type),
                now,
                now,
            ),
        )
        await db.commit()
        if cursor.rowcount == 1:
            return CommandClaim.NEW
        row = await (
            await db.execute(
                "SELECT status FROM processed_commands WHERE command_id = ? OR idempotency_key = ?",
                (str(envelope.command_id), envelope.idempotency_key),
            )
        ).fetchone()
        if row is None:
            raise RuntimeError("Déduplication SQLite incohérente")
        return CommandClaim(str(row["status"]))

    async def complete_command(
        self, command_id: UUID, result: dict[str, Any] | None = None
    ) -> None:
        await self._set_command_status(command_id, "completed", result_json=result)

    async def fail_command(self, command_id: UUID, error: dict[str, Any]) -> None:
        await self._set_command_status(command_id, "failed", error_json=error)

    async def _set_command_status(
        self,
        command_id: UUID,
        status: str,
        *,
        result_json: dict[str, Any] | None = None,
        error_json: dict[str, Any] | None = None,
    ) -> None:
        db = self._connection()
        await db.execute(
            """
            UPDATE processed_commands
            SET status = ?, result_json = ?, error_json = ?, updated_at = ?
            WHERE command_id = ?
            """,
            (
                status,
                json.dumps(result_json, separators=(",", ":")) if result_json is not None else None,
                json.dumps(error_json, separators=(",", ":")) if error_json is not None else None,
                _now(),
                str(command_id),
            ),
        )
        await db.commit()

    async def enqueue(self, envelope: WireEnvelope) -> None:
        db = self._connection()
        await db.execute(
            """
            INSERT OR IGNORE INTO outbox(message_id, envelope_json, created_at)
            VALUES (?, ?, ?)
            """,
            (
                str(envelope.message_id),
                envelope.model_dump_json(),
                _now(),
            ),
        )
        await db.commit()

    async def mark_sent(self, message_id: UUID) -> None:
        db = self._connection()
        await db.execute(
            """
            UPDATE outbox SET attempts = attempts + 1, sent_at = ? WHERE message_id = ?
            """,
            (_now(), str(message_id)),
        )
        await db.commit()

    async def acknowledge(self, message_id: UUID) -> None:
        db = self._connection()
        await db.execute(
            "UPDATE outbox SET acknowledged_at = ? WHERE message_id = ?",
            (_now(), str(message_id)),
        )
        await db.commit()

    async def reject(self, message_id: UUID, error: dict[str, Any] | None = None) -> None:
        """Conserve l'événement en dead-letter locale sans boucle de rejeu infinie."""

        db = self._connection()
        await db.execute(
            "UPDATE outbox SET rejected_at = ?, rejection_json = ? WHERE message_id = ?",
            (
                _now(),
                json.dumps(error or {}, separators=(",", ":")),
                str(message_id),
            ),
        )
        await db.commit()

    async def pending(self, limit: int = 1000) -> list[WireEnvelope]:
        db = self._connection()
        rows = await (
            await db.execute(
                """
                SELECT envelope_json FROM outbox
                WHERE acknowledged_at IS NULL AND rejected_at IS NULL
                ORDER BY created_at ASC LIMIT ?
                """,
                (limit,),
            )
        ).fetchall()
        return [WireEnvelope.model_validate_json(str(row["envelope_json"])) for row in rows]

    async def save_session(self, session: StoredSession) -> None:
        db = self._connection()
        await db.execute(
            """
            INSERT INTO sessions
                (logical_session_id, acp_session_id, harness_type, workspace_id, state,
                 metadata_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(logical_session_id) DO UPDATE SET
                acp_session_id = excluded.acp_session_id,
                harness_type = excluded.harness_type,
                workspace_id = excluded.workspace_id,
                state = excluded.state,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                str(session.logical_session_id),
                session.acp_session_id,
                session.harness_type,
                session.workspace_id,
                session.state,
                json.dumps(session.metadata, separators=(",", ":")),
                _now(),
            ),
        )
        await db.commit()

    async def load_session(self, logical_session_id: UUID) -> StoredSession | None:
        db = self._connection()
        row = await (
            await db.execute(
                """
                SELECT logical_session_id, acp_session_id, harness_type, workspace_id,
                       state, metadata_json
                FROM sessions WHERE logical_session_id = ?
                """,
                (str(logical_session_id),),
            )
        ).fetchone()
        if row is None:
            return None
        return StoredSession(
            logical_session_id=UUID(str(row["logical_session_id"])),
            acp_session_id=str(row["acp_session_id"]),
            harness_type=str(row["harness_type"]),
            workspace_id=str(row["workspace_id"]),
            state=str(row["state"]),
            metadata=json.loads(str(row["metadata_json"])),
        )

    async def list_sessions(self) -> list[StoredSession]:
        db = self._connection()
        rows = await (
            await db.execute(
                """
                SELECT logical_session_id, acp_session_id, harness_type, workspace_id,
                       state, metadata_json FROM sessions ORDER BY updated_at
                """
            )
        ).fetchall()
        return [
            StoredSession(
                logical_session_id=UUID(str(row["logical_session_id"])),
                acp_session_id=str(row["acp_session_id"]),
                harness_type=str(row["harness_type"]),
                workspace_id=str(row["workspace_id"]),
                state=str(row["state"]),
                metadata=json.loads(str(row["metadata_json"])),
            )
            for row in rows
        ]

    async def __aenter__(self) -> WorkerJournal:
        return await self.open()

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()
