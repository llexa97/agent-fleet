import asyncio
from typing import cast

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.api.agent_fleet_api.database import SessionFactory
from apps.api.agent_fleet_api.models_execution import Delivery
from apps.api.agent_fleet_api.models_governance import InternalEvent


def _spaces(client: TestClient) -> dict[str, dict[str, object]]:
    response = client.get("/api/v1/spaces")
    assert response.status_code == 200, response.text
    return {item["slug"]: item for item in response.json()}


def _create_channel(
    client: TestClient,
    space_id: str,
    slug: str = "client-taxi",
) -> dict[str, object]:
    response = client.post(
        "/api/v1/channels",
        json={
            "space_id": space_id,
            "slug": slug,
            "name": "Client Taxi",
            "kind": "project",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


def _create_agent(
    client: TestClient,
    space_id: str,
    channel_id: str,
    handle: str,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/agents",
        json={
            "space_id": space_id,
            "handle": handle,
            "display_name": handle.replace("-", " ").title(),
            "role": "Agent de test",
            "instructions": "Répondre de manière déterministe.",
            "runtime": {"harness": "fake", "runner_labels": []},
            "delegation_policy": {"allowed_agents": ["backend-dev"]},
        },
    )
    assert response.status_code == 201, response.text
    agent = response.json()
    membership = client.post(
        f"/api/v1/agents/{agent['id']}/memberships",
        json={
            "channel_id": channel_id,
            "activation_modes": ["mention_only", "assigned_only"],
        },
    )
    assert membership.status_code == 201, membership.text
    return cast(dict[str, object], agent)


def test_structured_mention_creates_one_persistent_delivery(
    authenticated_client: TestClient,
) -> None:
    client = authenticated_client
    business = _spaces(client)["business"]
    channel = _create_channel(client, str(business["id"]))
    cto = _create_agent(client, str(business["id"]), str(channel["id"]), "cto")

    body = {
        "content": "@cto fais avancer l’authentification",
        "mentions": [
            {
                "target_type": "agent",
                "target_id": cto["id"],
                "handle_at_creation": "cto",
            }
        ],
        "expects_response": True,
    }
    first = client.post(
        f"/api/v1/channels/{channel['id']}/messages",
        headers={"Idempotency-Key": "human-message-0001"},
        json=body,
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"/api/v1/channels/{channel['id']}/messages",
        headers={"Idempotency-Key": "human-message-0001"},
        json=body,
    )
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]

    async def counts() -> tuple[int, int]:
        async with SessionFactory() as db:
            deliveries = int(await db.scalar(select(func.count(Delivery.id))) or 0)
            outbox = int(
                await db.scalar(
                    select(func.count(InternalEvent.id)).where(
                        InternalEvent.event_type == "delivery.created"
                    )
                )
                or 0
            )
            return deliveries, outbox

    assert asyncio.run(counts()) == (1, 1)


def test_plain_at_text_never_wakes_an_agent(authenticated_client: TestClient) -> None:
    client = authenticated_client
    business = _spaces(client)["business"]
    channel = _create_channel(client, str(business["id"]))
    _create_agent(client, str(business["id"]), str(channel["id"]), "cto")
    response = client.post(
        f"/api/v1/channels/{channel['id']}/messages",
        headers={"Idempotency-Key": "human-message-plain-at"},
        json={"content": "Je cite @cto sans mention structurée", "mentions": []},
    )
    assert response.status_code == 201, response.text

    async def count() -> int:
        async with SessionFactory() as db:
            return int(await db.scalar(select(func.count(Delivery.id))) or 0)

    assert asyncio.run(count()) == 0


def test_business_agent_cannot_join_personal_channel(authenticated_client: TestClient) -> None:
    client = authenticated_client
    spaces = _spaces(client)
    business_channel = _create_channel(client, str(spaces["business"]["id"]))
    agent = _create_agent(
        client,
        str(spaces["business"]["id"]),
        str(business_channel["id"]),
        "cto",
    )
    personal_channels = client.get(
        "/api/v1/channels", params={"space_id": spaces["personal"]["id"]}
    ).json()
    response = client.post(
        f"/api/v1/agents/{agent['id']}/memberships",
        json={"channel_id": personal_channels[0]["id"]},
    )
    assert response.status_code == 404
