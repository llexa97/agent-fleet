"""Serveur MCP stdio fournisseur-neutre exposé aux harness."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from .relay import UnixRelayClient

mcp = FastMCP(
    "Agent Fleet",
    instructions=(
        "Utilisez les outils fleet.* pour communiquer et déléguer. "
        "L'identité de l'agent est liée à la session et ne doit pas être fournie."
    ),
    log_level="ERROR",
)


def _client() -> UnixRelayClient:
    return UnixRelayClient.from_environment()


@mcp.tool(name="fleet.list_agents")
async def list_agents(channel_id: str | None = None) -> Any:
    """Lister les agents visibles dans le contexte autorisé de la session."""
    return await _client().call("fleet.list_agents", {"channel_id": channel_id})


@mcp.tool(name="fleet.get_agent")
async def get_agent(agent_id: str) -> Any:
    """Lire la fiche d'un agent visible sans exposer ses secrets."""
    return await _client().call("fleet.get_agent", {"agent_id": agent_id})


@mcp.tool(name="fleet.list_channel_members")
async def list_channel_members(channel_id: str) -> Any:
    """Lister les humains et agents membres d'un channel autorisé."""
    return await _client().call("fleet.list_channel_members", {"channel_id": channel_id})


@mcp.tool(name="fleet.read_channel_history")
async def read_channel_history(
    channel_id: str, limit: int = 50, before_message_id: str | None = None
) -> Any:
    """Lire une page bornée de l'historique d'un channel autorisé."""
    return await _client().call(
        "fleet.read_channel_history",
        {
            "channel_id": channel_id,
            "limit": min(max(limit, 1), 200),
            "before_message_id": before_message_id,
        },
    )


@mcp.tool(name="fleet.get_thread")
async def get_thread(thread_id: str) -> Any:
    """Lire un thread appartenant au channel courant ou à un channel autorisé."""
    return await _client().call("fleet.get_thread", {"thread_id": thread_id})


@mcp.tool(name="fleet.post_message")
async def post_message(
    channel_id: str,
    content: str,
    idempotency_key: str,
    thread_id: str | None = None,
    mentions: list[dict[str, str]] | None = None,
) -> Any:
    """Publier un message comme l'agent courant avec mentions structurées optionnelles."""
    return await _client().call(
        "fleet.post_message",
        {
            "channel_id": channel_id,
            "content": content,
            "idempotency_key": idempotency_key,
            "thread_id": thread_id,
            "mentions": mentions or [],
        },
    )


@mcp.tool(name="fleet.reply_message")
async def reply_message(
    channel_id: str,
    reply_to_id: str,
    content: str,
    idempotency_key: str,
    mentions: list[dict[str, str]] | None = None,
) -> Any:
    """Répondre à un message existant comme l'agent lié à la session."""
    return await _client().call(
        "fleet.reply_message",
        {
            "channel_id": channel_id,
            "reply_to_id": reply_to_id,
            "content": content,
            "idempotency_key": idempotency_key,
            "mentions": mentions or [],
        },
    )


@mcp.tool(name="fleet.mention_agent")
async def mention_agent(
    agent_id: str,
    content: str,
    idempotency_key: str,
    channel_id: str | None = None,
    thread_id: str | None = None,
) -> Any:
    """Mentionner structurellement un agent autorisé ; le texte seul ne le réveille pas."""
    return await _client().call(
        "fleet.mention_agent",
        {
            "agent_id": agent_id,
            "content": content,
            "idempotency_key": idempotency_key,
            "channel_id": channel_id,
            "thread_id": thread_id,
        },
    )


@mcp.tool(name="fleet.create_task")
async def create_task(
    channel_id: str,
    title: str,
    description: str,
    idempotency_key: str,
    assigned_agent_id: str | None = None,
    workspace_id: str | None = None,
    parent_task_id: str | None = None,
) -> Any:
    """Créer une tâche persistante dans le channel autorisé."""
    return await _client().call(
        "fleet.create_task",
        {
            "channel_id": channel_id,
            "title": title,
            "description": description,
            "idempotency_key": idempotency_key,
            "assigned_agent_id": assigned_agent_id,
            "workspace_id": workspace_id,
            "parent_task_id": parent_task_id,
        },
    )


@mcp.tool(name="fleet.delegate_task")
async def delegate_task(
    agent_id: str,
    title: str,
    description: str,
    idempotency_key: str,
    channel_id: str | None = None,
    parent_task_id: str | None = None,
    priority: int = 2,
) -> Any:
    """Déléguer une tâche selon la politique de délégation de l'agent."""
    return await _client().call(
        "fleet.delegate_task",
        {
            "agent_id": agent_id,
            "title": title,
            "description": description,
            "idempotency_key": idempotency_key,
            "channel_id": channel_id,
            "parent_task_id": parent_task_id,
            "priority": min(max(priority, 0), 4),
        },
    )


@mcp.tool(name="fleet.update_task")
async def update_task(
    task_id: str,
    idempotency_key: str,
    changes: dict[str, Any],
) -> Any:
    """Mettre à jour une tâche autorisée sans changer l'identité de l'auteur."""
    return await _client().call(
        "fleet.update_task",
        {
            "task_id": task_id,
            "idempotency_key": idempotency_key,
            "changes": changes,
        },
    )


@mcp.tool(name="fleet.complete_task")
async def complete_task(
    result_summary: str,
    idempotency_key: str,
    task_id: str | None = None,
    artifacts: list[dict[str, str]] | None = None,
) -> Any:
    """Terminer une tâche avec un résultat structuré et des artefacts optionnels."""
    return await _client().call(
        "fleet.complete_task",
        {
            "task_id": task_id,
            "result_summary": result_summary,
            "idempotency_key": idempotency_key,
            "artifacts": artifacts or [],
        },
    )


@mcp.tool(name="fleet.fail_task")
async def fail_task(task_id: str, error: str, idempotency_key: str) -> Any:
    """Marquer explicitement une tâche en échec avec une erreur non sensible."""
    return await _client().call(
        "fleet.fail_task",
        {"task_id": task_id, "error": error, "idempotency_key": idempotency_key},
    )


@mcp.tool(name="fleet.request_human_approval")
async def request_human_approval(
    action_type: str,
    summary: str,
    idempotency_key: str,
    channel_id: str | None = None,
    task_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> Any:
    """Suspendre l'action jusqu'à une décision humaine explicite."""
    return await _client().call(
        "fleet.request_human_approval",
        {
            "capability": action_type,
            "summary": summary,
            "idempotency_key": idempotency_key,
            "channel_id": channel_id,
            "task_id": task_id,
            "details": details or {},
        },
    )


@mcp.tool(name="fleet.get_trace")
async def get_trace(trace_id: str) -> Any:
    """Lire l'état observable de la trace courante ou d'une trace autorisée."""
    return await _client().call("fleet.get_trace", {"trace_id": trace_id})


@mcp.tool(name="fleet.cancel_trace")
async def cancel_trace(
    trace_id: str,
    idempotency_key: str,
    reason: str,
    mode: Literal["cancel"] = "cancel",
) -> Any:
    """Demander l'annulation d'une trace ; le Control Plane conserve l'autorité finale."""
    return await _client().call(
        "fleet.cancel_trace",
        {
            "trace_id": trace_id,
            "idempotency_key": idempotency_key,
            "reason": reason,
            "mode": mode,
        },
    )


def run() -> None:
    mcp.run(transport="stdio")
