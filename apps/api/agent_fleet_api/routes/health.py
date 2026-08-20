from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select, text

from apps.api.agent_fleet_api.database import SessionFactory
from apps.api.agent_fleet_api.metrics import (
    active_sessions,
    pending_deliveries,
    pending_permissions,
    workers_connected,
)
from apps.api.agent_fleet_api.models_execution import AgentSession, Delivery, PermissionRequest
from apps.api.agent_fleet_api.models_infrastructure import Worker

router = APIRouter(tags=["health"])


@router.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readiness", include_in_schema=False)
async def readiness() -> dict[str, object]:
    try:
        async with SessionFactory() as db:
            await db.execute(text("SELECT 1"))
            worker_count = int(
                await db.scalar(select(func.count(Worker.id)).where(Worker.status == "online")) or 0
            )
            session_count = int(
                await db.scalar(
                    select(func.count(AgentSession.id)).where(
                        AgentSession.status.in_(["starting", "active", "waiting_approval"])
                    )
                )
                or 0
            )
            delivery_count = int(
                await db.scalar(
                    select(func.count(Delivery.id)).where(
                        Delivery.status.in_(["pending", "retry_scheduled"])
                    )
                )
                or 0
            )
            permission_count = int(
                await db.scalar(
                    select(func.count(PermissionRequest.id)).where(
                        PermissionRequest.status == "pending"
                    )
                )
                or 0
            )
        workers_connected.set(worker_count)
        active_sessions.set(session_count)
        pending_deliveries.set(delivery_count)
        pending_permissions.set(permission_count)
        return {
            "status": "ready",
            "database": "ok",
            "workers_connected": worker_count,
            "pending_deliveries": delivery_count,
        }
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "not_ready", "component": "database"},
        ) from exc
