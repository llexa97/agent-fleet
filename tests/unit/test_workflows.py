import asyncio
import hmac
from hashlib import sha256
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.api.agent_fleet_api.models_governance import Workflow
from apps.api.agent_fleet_api.schemas import CallWebhookAction, WorkflowCreate
from apps.api.agent_fleet_api.services.workflow_service import (
    _validate_url_syntax,
    verify_incoming_webhook,
)
from packages.shared.errors import DomainError, ForbiddenError


def test_workflow_actions_are_strictly_discriminated() -> None:
    with pytest.raises(ValidationError):
        WorkflowCreate.model_validate(
            {
                "space_id": str(uuid4()),
                "name": "Action inconnue",
                "trigger_type": "manual",
                "trigger_config": {},
                "actions": [{"type": "pretend_success", "anything": True}],
            }
        )


def test_trigger_configuration_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        WorkflowCreate.model_validate(
            {
                "space_id": str(uuid4()),
                "name": "Trigger invalide",
                "trigger_type": "message_posted",
                "trigger_config": {"regex_magic": "@all"},
                "actions": [
                    {
                        "type": "post_message",
                        "channel_id": str(uuid4()),
                        "content": "ok",
                    }
                ],
            }
        )


def test_outgoing_webhook_requires_https_and_public_address() -> None:
    with pytest.raises(DomainError, match="HTTPS"):
        _validate_url_syntax(CallWebhookAction(type="call_webhook", url="http://example.com/hook"))
    with pytest.raises(DomainError, match="privées"):
        _validate_url_syntax(CallWebhookAction(type="call_webhook", url="https://127.0.0.1/hook"))


def test_incoming_webhook_signature_is_constant_time_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_WORKFLOW_WEBHOOK_SECRET", "un-secret-de-test-suffisamment-long")
    workflow = Workflow(
        tenant_id=uuid4(),
        space_id=uuid4(),
        actor_id=uuid4(),
        name="Entrant",
        status="active",
        trigger_type="webhook",
        trigger_config={"max_body_bytes": 4096},
        actions=[],
        budget_policy={},
        webhook_secret_ref="TEST_WORKFLOW_WEBHOOK_SECRET",
        created_by_actor_id=uuid4(),
    )
    body = b'{"event":"up"}'
    signature = (
        "sha256=" + hmac.new(b"un-secret-de-test-suffisamment-long", body, sha256).hexdigest()
    )
    verify_incoming_webhook(workflow, body, signature)
    with pytest.raises(DomainError, match="Signature"):
        verify_incoming_webhook(workflow, body, "sha256=invalid")


@pytest.mark.asyncio
async def test_dns_resolution_to_private_address_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.agent_fleet_api.services import workflow_service

    class FakeLoop:
        async def getaddrinfo(self, *_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
            return [(None, None, None, None, ("10.0.0.8", 443))]

    monkeypatch.setattr(asyncio, "get_running_loop", FakeLoop)
    action = CallWebhookAction(type="call_webhook", url="https://hooks.example.invalid/event")
    with pytest.raises(ForbiddenError, match="privée"):
        await workflow_service._validate_resolved_webhook(action)
