import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AGENT_FLEET_ENVIRONMENT", "test")
os.environ.setdefault("AGENT_FLEET_DATABASE_URL", "sqlite+aiosqlite:///./.agent-fleet/pytest.db")
os.environ.setdefault("AGENT_FLEET_REDIS_URL", "")
os.environ.setdefault("AGENT_FLEET_EMBEDDED_DISPATCHER", "false")
os.environ.setdefault("AGENT_FLEET_TRUSTED_HOSTS", "testserver,localhost,127.0.0.1")
os.environ.setdefault("AGENT_FLEET_SESSION_SECRET", "test-session-secret-at-least-thirty-two-bytes")
os.environ.setdefault("AGENT_FLEET_BOOTSTRAP_TOKEN", "test-bootstrap-token-long-enough")

from apps.api.agent_fleet_api.database import engine
from apps.api.agent_fleet_api.main import app
from apps.api.agent_fleet_api.model_base import Base


@pytest.fixture(autouse=True)
async def clean_database() -> AsyncIterator[None]:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client


@pytest.fixture
def authenticated_client(client: TestClient) -> TestClient:
    response = client.post(
        "/api/v1/auth/bootstrap",
        headers={"X-Bootstrap-Token": "test-bootstrap-token-long-enough"},
        json={
            "email": "axel@example.com",
            "display_name": "Axel",
            "password": "correct-horse-battery-staple",
            "tenant_name": "Agent Fleet Test",
        },
    )
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client
