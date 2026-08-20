import asyncio
import os
import socket

from apps.api.agent_fleet_api.config import get_settings
from apps.api.agent_fleet_api.database import SessionFactory
from apps.api.agent_fleet_api.realtime import hub
from packages.shared.logging import configure_logging
from services.dispatcher.service import Dispatcher


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    dispatcher = Dispatcher(
        SessionFactory,
        settings,
        hub,
        dispatcher_id=os.getenv(
            "AGENT_FLEET_DISPATCHER_ID", f"{socket.gethostname()}:{os.getpid()}"
        ),
    )
    try:
        await dispatcher.run()
    finally:
        dispatcher.stop()


if __name__ == "__main__":
    asyncio.run(_main())
