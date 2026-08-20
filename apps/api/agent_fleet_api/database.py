from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from apps.api.agent_fleet_api.config import Settings, get_settings


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    config = settings or get_settings()
    connect_args = {"check_same_thread": False} if config.database_url.startswith("sqlite") else {}
    return create_async_engine(
        config.database_url,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


engine = create_engine()
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
