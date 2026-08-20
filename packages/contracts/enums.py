from enum import StrEnum


class ActorType(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"
    WORKFLOW = "workflow"


class SpaceKind(StrEnum):
    BUSINESS = "business"
    PERSONAL = "personal"
    CUSTOM = "custom"


class ChannelKind(StrEnum):
    DISCUSSION = "discussion"
    PROJECT = "project"
    PRIVATE = "private"


class ActivationMode(StrEnum):
    MENTION_ONLY = "mention_only"
    ASSIGNED_ONLY = "assigned_only"
    WATCH = "watch"
    ALL_MESSAGES = "all_messages"
    SILENT_CONTEXT = "silent_context"


class AgentStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    OFFLINE = "offline"
    BUSY = "busy"
    ERROR = "error"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DISPATCHED = "dispatched"
    PROCESSING = "processing"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


ACTIVE_DELIVERY_STATUSES = {
    DeliveryStatus.CLAIMED,
    DeliveryStatus.DISPATCHED,
    DeliveryStatus.PROCESSING,
    DeliveryStatus.WAITING_APPROVAL,
}


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    REVIEW = "review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TraceStatus(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LIMIT_REACHED = "limit_reached"


class SessionStatus(StrEnum):
    STARTING = "starting"
    ACTIVE = "active"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CLOSED = "closed"


class PermissionDecisionKind(StrEnum):
    DENY = "deny"
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    ALLOW_AGENT = "allow_agent"


class PermissionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class HarnessType(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"
    FAKE = "fake"
    HERMES = "hermes"
    GOOSE = "goose"
    GEMINI = "gemini"
    OPENCODE = "opencode"


class IsolationMode(StrEnum):
    PER_SESSION = "per_session"
    PER_AGENT = "per_agent"
    POOLED = "pooled"


class WorkflowStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class WorkflowRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
