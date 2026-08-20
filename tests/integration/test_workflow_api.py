import asyncio
import hmac
import json
import time
from hashlib import sha256
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.api.agent_fleet_api.database import SessionFactory
from apps.api.agent_fleet_api.models_governance import WorkflowRun


def _business_and_channel(
    client: TestClient, slug: str
) -> tuple[dict[str, object], dict[str, object]]:
    spaces = client.get("/api/v1/spaces")
    assert spaces.status_code == 200, spaces.text
    business = next(item for item in spaces.json() if item["slug"] == "business")
    channel_response = client.post(
        "/api/v1/channels",
        json={
            "space_id": business["id"],
            "slug": slug,
            "name": slug.replace("-", " ").title(),
            "kind": "project",
        },
    )
    assert channel_response.status_code == 201, channel_response.text
    return business, channel_response.json()


def _create_and_activate(
    client: TestClient,
    *,
    space_id: object,
    name: str,
    trigger_type: str,
    trigger_config: dict[str, object],
    actions: list[dict[str, object]],
    webhook_secret_ref: str | None = None,
) -> dict[str, object]:
    response = client.post(
        "/api/v1/workflows",
        json={
            "space_id": space_id,
            "name": name,
            "trigger_type": trigger_type,
            "trigger_config": trigger_config,
            "actions": actions,
            "webhook_secret_ref": webhook_secret_ref,
        },
    )
    assert response.status_code == 201, response.text
    workflow = response.json()
    activated = client.post(f"/api/v1/workflows/{workflow['id']}/activate")
    assert activated.status_code == 200, activated.text
    return cast(dict[str, object], activated.json())


def test_manual_workflow_waits_for_approval_and_resumes(
    authenticated_client: TestClient,
) -> None:
    client = authenticated_client
    business, channel = _business_and_channel(client, "workflow-approval")
    workflow = _create_and_activate(
        client,
        space_id=business["id"],
        name="Validation humaine",
        trigger_type="manual",
        trigger_config={},
        actions=[
            {
                "type": "post_message",
                "channel_id": channel["id"],
                "content": "Préparation terminée",
            },
            {
                "type": "request_approval",
                "summary": "Publier le résultat final",
                "details": {"risk": "low"},
            },
            {
                "type": "post_message",
                "channel_id": channel["id"],
                "content": "Résultat approuvé",
            },
        ],
    )
    headers = {"Idempotency-Key": "manual-approval-run-0001"}
    first = client.post(
        f"/api/v1/workflows/{workflow['id']}/run",
        headers=headers,
        json={"input": {"ticket": "INC-42"}},
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "waiting"
    assert first.json()["state"]["approval"]["status"] == "pending"

    duplicate = client.post(
        f"/api/v1/workflows/{workflow['id']}/run",
        headers=headers,
        json={"input": {"ticket": "INC-42"}},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["id"] == first.json()["id"]

    messages_before = client.get(f"/api/v1/channels/{channel['id']}/messages")
    assert [item["content"] for item in messages_before.json()] == ["Préparation terminée"]

    resumed = client.post(
        f"/api/v1/workflow-runs/{first.json()['id']}/resume",
        json={"decision": "approve", "reason": "Validé par Axel"},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "completed"
    messages_after = client.get(f"/api/v1/channels/{channel['id']}/messages")
    assert [item["content"] for item in messages_after.json()] == [
        "Préparation terminée",
        "Résultat approuvé",
    ]
    trace = client.get(f"/api/v1/traces/{resumed.json()['trace_id']}")
    assert trace.status_code == 200, trace.text
    assert trace.json()["status"] == "completed"


def test_waiting_workflow_can_be_cancelled(authenticated_client: TestClient) -> None:
    client = authenticated_client
    business, channel = _business_and_channel(client, "workflow-cancel")
    workflow = _create_and_activate(
        client,
        space_id=business["id"],
        name="Annulation",
        trigger_type="manual",
        trigger_config={},
        actions=[
            {"type": "delay", "seconds": 600},
            {"type": "post_message", "channel_id": channel["id"], "content": "Trop tard"},
        ],
    )
    run = client.post(
        f"/api/v1/workflows/{workflow['id']}/run",
        headers={"Idempotency-Key": "manual-cancel-run-0001"},
        json={"input": {}},
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "waiting"
    cancelled = client.post(f"/api/v1/workflow-runs/{run.json()['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"


def test_incoming_webhook_is_signed_and_idempotent(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_AGENT_FLEET_WEBHOOK", "webhook-secret-for-tests")
    client = authenticated_client
    business, channel = _business_and_channel(client, "workflow-webhook")
    workflow = _create_and_activate(
        client,
        space_id=business["id"],
        name="Monitoring",
        trigger_type="webhook",
        trigger_config={"max_body_bytes": 4096},
        webhook_secret_ref="TEST_AGENT_FLEET_WEBHOOK",
        actions=[
            {
                "type": "post_message",
                "channel_id": channel["id"],
                "content": "Alerte reçue",
            }
        ],
    )
    raw = json.dumps({"alert": "database_down"}, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(b"webhook-secret-for-tests", raw, sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": "monitoring-event-0001",
        "X-Agent-Fleet-Signature": signature,
    }
    rejected = client.post(
        f"/api/v1/workflows/{workflow['id']}/webhook",
        content=raw,
        headers={**headers, "X-Agent-Fleet-Signature": "sha256=bad"},
    )
    assert rejected.status_code == 401
    first = client.post(f"/api/v1/workflows/{workflow['id']}/webhook", content=raw, headers=headers)
    second = client.post(
        f"/api/v1/workflows/{workflow['id']}/webhook", content=raw, headers=headers
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    messages = client.get(f"/api/v1/channels/{channel['id']}/messages").json()
    assert [item["content"] for item in messages] == ["Alerte reçue"]


def test_message_event_creates_exactly_one_workflow_run(
    authenticated_client: TestClient,
) -> None:
    client = authenticated_client
    business, channel = _business_and_channel(client, "workflow-event")
    workflow = _create_and_activate(
        client,
        space_id=business["id"],
        name="Message vers tâche",
        trigger_type="message_posted",
        trigger_config={"channel_ids": [channel["id"]], "actor_types": ["human"]},
        actions=[
            {
                "type": "create_task",
                "channel_id": channel["id"],
                "title": "Traiter le nouveau message",
                "description": "Créée automatiquement",
            }
        ],
    )
    body = {"content": "Nouvelle demande", "mentions": []}
    headers = {"Idempotency-Key": "workflow-source-message-0001"}
    assert (
        client.post(
            f"/api/v1/channels/{channel['id']}/messages", headers=headers, json=body
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/v1/channels/{channel['id']}/messages", headers=headers, json=body
        ).status_code
        == 200
    )

    runs: list[dict[str, object]] = []
    for _ in range(30):
        response = client.get("/api/v1/workflow-runs", params={"workflow_id": workflow["id"]})
        assert response.status_code == 200, response.text
        runs = response.json()
        if runs and runs[0]["status"] == "completed":
            break
        time.sleep(0.05)
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    tasks = client.get("/api/v1/tasks", params={"space_id": business["id"]}).json()
    assert [item["title"] for item in tasks] == ["Traiter le nouveau message"]

    async def run_count() -> int:
        async with SessionFactory() as db:
            return int(
                await db.scalar(
                    select(func.count(WorkflowRun.id)).where(
                        WorkflowRun.workflow_id == UUID(str(workflow["id"]))
                    )
                )
                or 0
            )

    assert asyncio.run(run_count()) == 1
