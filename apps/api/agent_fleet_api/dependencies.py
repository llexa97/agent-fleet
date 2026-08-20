from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.config import Settings, get_settings
from apps.api.agent_fleet_api.database import get_session
from apps.api.agent_fleet_api.security import Principal, authenticate_request

Database = Annotated[AsyncSession, Depends(get_session)]
Config = Annotated[Settings, Depends(get_settings)]


async def get_principal(request: Request, db: Database, settings: Config) -> Principal:
    return await authenticate_request(request, db, settings)


async def get_principal_csrf(request: Request, db: Database, settings: Config) -> Principal:
    return await authenticate_request(request, db, settings, require_csrf=True)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
MutatingPrincipal = Annotated[Principal, Depends(get_principal_csrf)]
