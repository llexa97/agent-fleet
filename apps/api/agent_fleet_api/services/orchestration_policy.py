from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.models_collaboration import Trace
from apps.api.agent_fleet_api.models_execution import Delivery
from packages.shared.time import as_utc, utcnow

DEFAULT_TRACE_POLICY: dict[str, int | float | bool] = {
    "max_hops": 10,
    "max_agent_turns": 30,
    "max_delegations": 10,
    "max_parallel_agents": 5,
    "max_trace_duration_minutes": 120,
    "max_cost_eur": 5.0,
    "max_tokens": 500_000,
    "max_repeated_pair_exchanges": 4,
    "max_identical_messages": 2,
    "max_no_progress_turns": 3,
    "allow_self_mention": False,
    "allow_agent_all": False,
    "require_channel_membership": True,
}


@dataclass(frozen=True, slots=True)
class PolicyCheck:
    allowed: bool
    reason: str | None = None


def trace_policy(trace: Trace) -> dict[str, int | float | bool]:
    return {**DEFAULT_TRACE_POLICY, **(trace.policy or {})}


def current_trace_limit(trace: Trace) -> str | None:
    """Réévalue les limites centrales juste avant une exécution.

    Une livraison peut avoir attendu pendant qu'une autre consommait le budget ;
    cette vérification évite donc d'exécuter un travail devenu hors politique.
    """

    policy = trace_policy(trace)
    if trace.turn_count >= int(policy["max_agent_turns"]):
        return "max_agent_turns"
    if trace.delegation_count > int(policy["max_delegations"]):
        return "max_delegations"
    if trace.token_count >= int(policy["max_tokens"]):
        return "max_tokens"
    if float(trace.cost_eur) >= float(policy["max_cost_eur"]):
        return "max_cost"
    if utcnow() - as_utc(trace.created_at) > timedelta(
        minutes=int(policy["max_trace_duration_minutes"])
    ):
        return "max_duration"
    return None


async def check_delivery_allowed(
    db: AsyncSession,
    *,
    trace: Trace,
    source_agent_id: UUID | None,
    target_agent_id: UUID,
    depth: int,
    message_hash: str,
) -> PolicyCheck:
    policy = trace_policy(trace)
    if trace.status != "running":
        return PolicyCheck(False, f"trace_{trace.status}")
    if source_agent_id == target_agent_id and not bool(policy["allow_self_mention"]):
        return PolicyCheck(False, "self_mention")
    if depth > int(policy["max_hops"]):
        return PolicyCheck(False, "max_hops")
    if trace.turn_count >= int(policy["max_agent_turns"]):
        return PolicyCheck(False, "max_agent_turns")
    if trace.delegation_count >= int(policy["max_delegations"]):
        return PolicyCheck(False, "max_delegations")
    if trace.token_count >= int(policy["max_tokens"]):
        return PolicyCheck(False, "max_tokens")
    if float(trace.cost_eur) >= float(policy["max_cost_eur"]):
        return PolicyCheck(False, "max_cost")
    if utcnow() - as_utc(trace.created_at) > timedelta(
        minutes=int(policy["max_trace_duration_minutes"])
    ):
        return PolicyCheck(False, "max_duration")
    identical = await db.scalar(
        select(func.count(Delivery.id)).where(
            Delivery.trace_id == trace.id,
            Delivery.normalized_message_hash == message_hash,
        )
    )
    if int(identical or 0) >= int(policy["max_identical_messages"]):
        return PolicyCheck(False, "identical_message_loop")
    if source_agent_id is not None:
        pair_count = await db.scalar(
            select(func.count(Delivery.id)).where(
                Delivery.trace_id == trace.id,
                Delivery.source_agent_id == source_agent_id,
                Delivery.target_agent_id == target_agent_id,
            )
        )
        if int(pair_count or 0) >= int(policy["max_repeated_pair_exchanges"]):
            return PolicyCheck(False, "repeated_pair_loop")
    return PolicyCheck(True)
