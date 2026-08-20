import asyncio
import os
from uuid import UUID

from sqlalchemy import select

from apps.api.agent_fleet_api.config import get_settings
from apps.api.agent_fleet_api.database import SessionFactory
from apps.api.agent_fleet_api.models_collaboration import (
    AgentChannelMembership,
    Channel,
    ChannelMember,
)
from apps.api.agent_fleet_api.models_identity import (
    Actor,
    Agent,
    AgentPermission,
    AgentRuntimeBinding,
    Space,
    Tenant,
    User,
)
from apps.api.agent_fleet_api.models_infrastructure import (
    Worker,
    WorkerCredential,
    WorkerHarness,
    Workspace,
)
from apps.api.agent_fleet_api.security import hash_password, hash_secret

TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
USER_ID = UUID("10000000-0000-4000-8000-000000000002")
AXEL_ACTOR_ID = UUID("10000000-0000-4000-8000-000000000003")
BUSINESS_ID = UUID("10000000-0000-4000-8000-000000000010")
PERSONAL_ID = UUID("10000000-0000-4000-8000-000000000011")
DIRECTION_ID = UUID("10000000-0000-4000-8000-000000000020")
CLIENT_TAXI_ID = UUID("10000000-0000-4000-8000-000000000021")

WORKER_A_ID = UUID("a1000000-0000-4000-8000-000000000001")
WORKER_B_ID = UUID("b2000000-0000-4000-8000-000000000002")
WORKER_A_TOKEN = "demo-worker-a-token-change-me-at-least-32-characters"  # noqa: S105
WORKER_B_TOKEN = "demo-worker-b-token-change-me-at-least-32-characters"  # noqa: S105

AGENTS = [
    {
        "id": UUID("c7000000-0000-4000-8000-000000000001"),
        "actor_id": UUID("c7000000-0000-4000-8000-000000000011"),
        "handle": "cto",
        "display_name": "CTO",
        "role": "Responsable technique et orchestrateur",
        "worker_id": WORKER_A_ID,
        "workspace_id": UUID("f1000000-0000-4000-8000-000000000001"),
        "allowed_agents": ["backend-dev", "code-reviewer"],
    },
    {
        "id": UUID("b7000000-0000-4000-8000-000000000002"),
        "actor_id": UUID("b7000000-0000-4000-8000-000000000012"),
        "handle": "backend-dev",
        "display_name": "Backend Dev",
        "role": "Développeur backend",
        "worker_id": WORKER_B_ID,
        "workspace_id": UUID("f2000000-0000-4000-8000-000000000002"),
        "allowed_agents": ["code-reviewer", "cto"],
    },
    {
        "id": UUID("d7000000-0000-4000-8000-000000000003"),
        "actor_id": UUID("d7000000-0000-4000-8000-000000000013"),
        "handle": "code-reviewer",
        "display_name": "Code Reviewer",
        "role": "Relecteur de code indépendant",
        "worker_id": WORKER_A_ID,
        "workspace_id": UUID("f1000000-0000-4000-8000-000000000001"),
        "allowed_agents": ["cto"],
    },
]

DEMO_INSTRUCTIONS = {
    "cto": (
        "Coordonne la démonstration. "
        "[[delegate-task-if-requester:Axel:b7000000-0000-4000-8000-000000000002]]"
    ),
    "backend-dev": (
        "Implémente puis fais relire le résultat. "
        "[[delegate-task-if-requester:CTO:d7000000-0000-4000-8000-000000000003]] "
        "[[complete-task-if-requester:Code Reviewer]]"
    ),
    "code-reviewer": "Relis le résultat puis clôture la sous-tâche. [[complete-task]]",
}


async def seed() -> None:
    settings = get_settings()
    if settings.environment == "production":
        raise RuntimeError("Le seed de démonstration est interdit en production")
    async with SessionFactory() as db:
        if await db.scalar(select(Tenant.id).where(Tenant.id == TENANT_ID)):
            print("Données de démonstration déjà présentes.")
            return
        tenant = Tenant(id=TENANT_ID, slug="demo", name="Agent Fleet Demo")
        db.add(tenant)
        await db.flush()
        user = User(
            id=USER_ID,
            tenant_id=TENANT_ID,
            email=os.getenv("AGENT_FLEET_DEMO_EMAIL", "axel@example.com"),
            display_name="Axel",
            password_hash=hash_password(
                os.getenv("AGENT_FLEET_DEMO_PASSWORD", "agent-fleet-demo-password")
            ),
            is_owner=True,
            status="active",
        )
        db.add(user)
        await db.flush()
        actor = Actor(
            id=AXEL_ACTOR_ID,
            tenant_id=TENANT_ID,
            actor_type="human",
            user_id=USER_ID,
            display_name="Axel",
        )
        db.add(actor)
        business = Space(
            id=BUSINESS_ID,
            tenant_id=TENANT_ID,
            slug="business",
            name="Business",
            kind="business",
        )
        personal = Space(
            id=PERSONAL_ID,
            tenant_id=TENANT_ID,
            slug="personal",
            name="Personnel",
            kind="personal",
        )
        db.add_all([business, personal])
        await db.flush()
        direction = Channel(
            id=DIRECTION_ID,
            tenant_id=TENANT_ID,
            space_id=BUSINESS_ID,
            slug="direction",
            name="Direction",
            kind="discussion",
        )
        client_taxi = Channel(
            id=CLIENT_TAXI_ID,
            tenant_id=TENANT_ID,
            space_id=BUSINESS_ID,
            slug="client-taxi",
            name="Client Taxi",
            kind="project",
        )
        db.add_all([direction, client_taxi])
        await db.flush()
        for channel in (direction, client_taxi):
            db.add(
                ChannelMember(
                    tenant_id=TENANT_ID,
                    space_id=BUSINESS_ID,
                    channel_id=channel.id,
                    actor_id=AXEL_ACTOR_ID,
                    role="owner",
                )
            )
        personal_channel_slugs = [
            "assistant-perso",
            "maison",
            "homelab",
            "finance-perso",
            "projets-perso",
        ]
        for index, slug in enumerate(personal_channel_slugs, start=1):
            channel = Channel(
                id=UUID(f"10000000-0000-4000-8000-{100 + index:012d}"),
                tenant_id=TENANT_ID,
                space_id=PERSONAL_ID,
                slug=slug,
                name=slug.replace("-", " ").title(),
                kind="discussion",
            )
            db.add(channel)
            await db.flush()
            db.add(
                ChannelMember(
                    tenant_id=TENANT_ID,
                    space_id=PERSONAL_ID,
                    channel_id=channel.id,
                    actor_id=AXEL_ACTOR_ID,
                    role="owner",
                )
            )
        for index, slug in enumerate(
            ["finance", "administratif", "marketing", "infrastructure", "clients"],
            start=1,
        ):
            channel = Channel(
                id=UUID(f"10000000-0000-4000-8001-{index:012d}"),
                tenant_id=TENANT_ID,
                space_id=BUSINESS_ID,
                slug=slug,
                name=slug.replace("-", " ").title(),
                kind="discussion",
            )
            db.add(channel)
            await db.flush()
            db.add(
                ChannelMember(
                    tenant_id=TENANT_ID,
                    space_id=BUSINESS_ID,
                    channel_id=channel.id,
                    actor_id=AXEL_ACTOR_ID,
                    role="owner",
                )
            )
        for worker_id, name, token in (
            (WORKER_A_ID, "Worker A", WORKER_A_TOKEN),
            (WORKER_B_ID, "Worker B", WORKER_B_TOKEN),
        ):
            worker = Worker(
                id=worker_id,
                tenant_id=TENANT_ID,
                name=name,
                status="registered",
                labels=["development", "fake-acp"],
                max_sessions=4,
            )
            db.add(worker)
            await db.flush()
            db.add(
                WorkerCredential(
                    tenant_id=TENANT_ID,
                    worker_id=worker_id,
                    token_hash=hash_secret(token, settings.session_secret),
                    token_hint=token[-8:],
                )
            )
            db.add(
                WorkerHarness(
                    tenant_id=TENANT_ID,
                    worker_id=worker_id,
                    harness_type="fake",
                    adapter="fake-acp",
                    version="0.1.0",
                    available=True,
                    capabilities=["loadSession", "resume", "list", "close"],
                )
            )
        workspaces = [
            Workspace(
                id=UUID("f1000000-0000-4000-8000-000000000001"),
                tenant_id=TENANT_ID,
                space_id=BUSINESS_ID,
                worker_id=WORKER_A_ID,
                external_id="fleetbase-ui-a",
                display_name="Fleetbase UI — Worker A",
                root="/workspaces/fleetbase-ui",
                canonical_root="/workspaces/fleetbase-ui",
                status="available",
            ),
            Workspace(
                id=UUID("f2000000-0000-4000-8000-000000000002"),
                tenant_id=TENANT_ID,
                space_id=BUSINESS_ID,
                worker_id=WORKER_B_ID,
                external_id="fleetbase-ui-b",
                display_name="Fleetbase UI — Worker B",
                root="/workspaces/fleetbase-ui",
                canonical_root="/workspaces/fleetbase-ui",
                status="available",
            ),
        ]
        db.add_all(workspaces)
        await db.flush()
        for item in AGENTS:
            agent_actor = Actor(
                id=item["actor_id"],
                tenant_id=TENANT_ID,
                space_id=BUSINESS_ID,
                actor_type="agent",
                display_name=item["display_name"],
            )
            agent = Agent(
                id=item["id"],
                tenant_id=TENANT_ID,
                space_id=BUSINESS_ID,
                actor_id=item["actor_id"],
                handle=item["handle"],
                display_name=item["display_name"],
                role=item["role"],
                instructions=(
                    "Utilise fleet.* pour communiquer. " + DEMO_INSTRUCTIONS[str(item["handle"])]
                ),
                status="active",
                tools=[
                    "fleet.list_agents",
                    "fleet.get_agent",
                    "fleet.list_channel_members",
                    "fleet.read_channel_history",
                    "fleet.get_thread",
                    "fleet.post_message",
                    "fleet.reply_message",
                    "fleet.mention_agent",
                    "fleet.create_task",
                    "fleet.delegate_task",
                    "fleet.update_task",
                    "fleet.complete_task",
                    "fleet.fail_task",
                    "fleet.request_human_approval",
                    "fleet.get_trace",
                    "fleet.cancel_trace",
                ],
                budget_policy={"max_cost_per_trace": 5.0, "max_turns_per_trace": 30},
                delegation_policy={"allowed_agents": item["allowed_agents"]},
            )
            db.add(agent_actor)
            await db.flush()
            db.add(agent)
            await db.flush()
            db.add(
                AgentRuntimeBinding(
                    tenant_id=TENANT_ID,
                    agent_id=item["id"],
                    harness="fake",
                    worker_id=item["worker_id"],
                    workspace_id=item["workspace_id"],
                    runner_selector={"labels": ["development", "fake-acp"]},
                    enabled=True,
                )
            )
            for capability, policy in {
                "shell": "ask",
                "filesystem_write": "ask",
                "git_commit": "allow",
                "git_push": "deny",
                "production": "deny",
            }.items():
                db.add(
                    AgentPermission(
                        tenant_id=TENANT_ID,
                        agent_id=item["id"],
                        capability=capability,
                        policy=policy,
                    )
                )
            for channel in (direction, client_taxi):
                db.add(
                    ChannelMember(
                        tenant_id=TENANT_ID,
                        space_id=BUSINESS_ID,
                        channel_id=channel.id,
                        actor_id=item["actor_id"],
                        role="member",
                    )
                )
                db.add(
                    AgentChannelMembership(
                        tenant_id=TENANT_ID,
                        space_id=BUSINESS_ID,
                        channel_id=channel.id,
                        agent_id=item["id"],
                        activation_modes=["mention_only", "assigned_only"],
                    )
                )
        await db.commit()
        print("Démonstration créée: Axel, @cto, @backend-dev, @code-reviewer, Worker A/B.")


if __name__ == "__main__":
    asyncio.run(seed())
