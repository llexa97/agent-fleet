"""Import central de toutes les métadonnées SQLAlchemy.

Alembic et les tests importent ce module avant d'utiliser ``Base.metadata``.
"""

from apps.api.agent_fleet_api.models_collaboration import (
    AgentChannelMembership,
    Channel,
    ChannelMember,
    Message,
    MessageMention,
    Task,
    TaskDependency,
    TaskEvent,
    Thread,
    Trace,
    TraceParticipant,
)
from apps.api.agent_fleet_api.models_execution import (
    AgentSession,
    Delivery,
    DeliveryQueue,
    PermissionDecision,
    PermissionRequest,
    SessionEvent,
)
from apps.api.agent_fleet_api.models_governance import (
    AuditEvent,
    IdempotencyRecord,
    InternalEvent,
    Workflow,
    WorkflowRun,
)
from apps.api.agent_fleet_api.models_identity import (
    Actor,
    Agent,
    AgentPermission,
    AgentRuntimeBinding,
    Space,
    Tenant,
    User,
    UserSession,
)
from apps.api.agent_fleet_api.models_infrastructure import (
    Worker,
    WorkerCommand,
    WorkerCredential,
    WorkerHarness,
    WorkerLog,
    WorkerMessageReceipt,
    Workspace,
)

__all__ = [
    "Actor",
    "Agent",
    "AgentChannelMembership",
    "AgentPermission",
    "AgentRuntimeBinding",
    "AgentSession",
    "AuditEvent",
    "Channel",
    "ChannelMember",
    "Delivery",
    "DeliveryQueue",
    "IdempotencyRecord",
    "InternalEvent",
    "Message",
    "MessageMention",
    "PermissionDecision",
    "PermissionRequest",
    "SessionEvent",
    "Space",
    "Task",
    "TaskDependency",
    "TaskEvent",
    "Tenant",
    "Thread",
    "Trace",
    "TraceParticipant",
    "User",
    "UserSession",
    "Worker",
    "WorkerCommand",
    "WorkerCredential",
    "WorkerHarness",
    "WorkerLog",
    "WorkerMessageReceipt",
    "Workflow",
    "WorkflowRun",
    "Workspace",
]
