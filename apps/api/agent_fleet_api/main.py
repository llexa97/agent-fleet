import asyncio
import os
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import make_asgi_app
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

import apps.api.agent_fleet_api.models  # noqa: F401
from apps.api.agent_fleet_api.config import get_settings
from apps.api.agent_fleet_api.database import SessionFactory, engine
from apps.api.agent_fleet_api.model_base import Base
from apps.api.agent_fleet_api.realtime import hub
from apps.api.agent_fleet_api.routes import (
    agents,
    auth,
    catalog,
    events,
    health,
    messages,
    operations,
    tasks_traces,
    threads,
    worker_socket,
    workers,
    workflows,
)
from apps.api.agent_fleet_api.services.outbox import OutboxPublisher
from apps.api.agent_fleet_api.services.workflow_service import WorkflowEngine
from packages.shared.errors import DomainError
from packages.shared.logging import configure_logging
from services.dispatcher.service import Dispatcher

settings = get_settings()
configure_logging(settings.log_level, json_output=settings.log_json)
logger = structlog.get_logger(__name__)


async def _command_pump(dispatcher: Dispatcher) -> None:
    while True:
        await dispatcher.flush_commands()
        await asyncio.sleep(0.2)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings.validate_runtime_security()
    if settings.database_url.startswith("sqlite"):
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    async with SessionFactory() as db:
        await db.execute(text("SELECT 1"))
    dispatcher = Dispatcher(
        SessionFactory,
        settings,
        hub,
        dispatcher_id=f"api:{socket.gethostname()}:{os.getpid()}",
    )
    outbox = OutboxPublisher(SessionFactory, settings, hub)
    workflow_engine = WorkflowEngine(SessionFactory)
    tasks = [
        asyncio.create_task(outbox.run(), name="outbox-publisher"),
        asyncio.create_task(_command_pump(dispatcher), name="worker-command-pump"),
        asyncio.create_task(workflow_engine.run(), name="workflow-engine"),
    ]
    if settings.embedded_dispatcher:
        tasks.append(asyncio.create_task(dispatcher.run(), name="embedded-dispatcher"))
    logger.info("control_plane.started", environment=settings.environment)
    try:
        yield
    finally:
        dispatcher.stop()
        outbox.stop()
        workflow_engine.stop()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await outbox.close()
        await engine.dispose()
        logger.info("control_plane.stopped")


app = FastAPI(
    title=settings.product_name,
    version="0.1.0",
    description="Control Plane multi-tenant pour une flotte d’agents ACP sur LXC.",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    lifespan=lifespan,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "X-Bootstrap-Token"],
)


@app.middleware("http")
async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("x-request-id", str(uuid4()))[:128]
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.clear_contextvars()
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.cookie_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": request.headers.get("x-request-id"),
            }
        },
    )


api_prefix = "/api/v1"
for api_router in (
    health.router,
    auth.router,
    catalog.router,
    agents.router,
    messages.router,
    tasks_traces.router,
    threads.router,
    workers.router,
    operations.router,
    workflows.router,
    events.router,
    worker_socket.router,
):
    app.include_router(api_router, prefix=api_prefix)

app.mount("/metrics", make_asgi_app())

web_dist = Path(__file__).parents[2] / "web" / "dist"
if web_dist.exists():
    app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="web-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def serve_spa(path: str) -> FileResponse:
        candidate = (web_dist / path).resolve()
        if candidate.is_relative_to(web_dist.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(web_dist / "index.html")
