from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from packages.contracts.enums import (
    ActivationMode,
    ChannelKind,
    HarnessType,
    PermissionDecisionKind,
    SpaceKind,
    TaskStatus,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ErrorBody(ApiModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class BootstrapRequest(ApiModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=12, max_length=256)
    tenant_name: str = Field(default="Agent Fleet", min_length=1, max_length=160)


class LoginRequest(ApiModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class AuthResponse(ApiModel):
    user_id: UUID
    actor_id: UUID
    tenant_id: UUID
    email: str
    display_name: str
    is_owner: bool
    csrf_token: str | None = None


class SpaceCreate(ApiModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    kind: SpaceKind = SpaceKind.CUSTOM
    description: str | None = Field(default=None, max_length=4000)


class SpaceResponse(ApiModel):
    id: UUID
    tenant_id: UUID
    name: str
    slug: str
    kind: str
    description: str | None
    created_at: datetime


class ChannelCreate(ApiModel):
    space_id: UUID
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*$")
    kind: ChannelKind = ChannelKind.DISCUSSION
    description: str | None = Field(default=None, max_length=4000)


class ChannelResponse(ApiModel):
    id: UUID
    tenant_id: UUID
    space_id: UUID
    name: str
    slug: str
    kind: str
    description: str | None
    is_archived: bool
    created_at: datetime


class MentionInput(ApiModel):
    target_type: Literal["human", "agent"]
    target_id: UUID
    handle_at_creation: str = Field(min_length=1, max_length=80)


class MentionResponse(ApiModel):
    id: UUID
    target_type: str
    target_id: UUID
    handle_at_creation: str


class MessageCreate(ApiModel):
    content: str = Field(min_length=1, max_length=100_000)
    thread_id: UUID | None = None
    reply_to_id: UUID | None = None
    mentions: list[MentionInput] = Field(default_factory=list, max_length=64)
    expects_response: bool = False

    @field_validator("content")
    @classmethod
    def non_whitespace_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Le message ne peut pas être vide")
        return value


class MessageResponse(ApiModel):
    id: UUID
    tenant_id: UUID
    space_id: UUID
    channel_id: UUID
    thread_id: UUID | None
    author_type: str
    author_id: UUID
    author_display_name: str | None = None
    author_handle: str | None = None
    content: str
    reply_to_id: UUID | None
    trace_id: UUID | None
    task_id: UUID | None
    expects_response: bool
    is_technical: bool
    mentions: list[MentionResponse] = Field(default_factory=list)
    created_at: datetime


class ThreadCreate(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    root_message_id: UUID | None = None


class ThreadUpdate(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    is_closed: bool | None = None


class ThreadResponse(ApiModel):
    id: UUID
    tenant_id: UUID
    space_id: UUID
    channel_id: UUID
    title: str | None
    root_message_id: UUID | None
    created_by_actor_id: UUID
    summary: str | None
    is_closed: bool
    created_at: datetime
    updated_at: datetime


class RuntimeBindingInput(ApiModel):
    harness: HarnessType = HarnessType.FAKE
    worker_id: UUID | None = None
    workspace_id: UUID | None = None
    model: str | None = Field(default=None, max_length=120)
    runner_labels: list[str] = Field(default_factory=list, max_length=64)


class AgentCreate(ApiModel):
    space_id: UUID
    handle: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    display_name: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=255)
    instructions: str = Field(default="", max_length=100_000)
    runtime: RuntimeBindingInput = Field(default_factory=RuntimeBindingInput)
    max_concurrency: int = Field(default=1, ge=1, le=32)
    budget_policy: dict[str, Any] = Field(default_factory=dict)
    delegation_policy: dict[str, Any] = Field(default_factory=dict)


class AgentUpdate(ApiModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    role: str | None = Field(default=None, min_length=1, max_length=255)
    instructions: str | None = Field(default=None, max_length=100_000)
    status: Literal["active", "suspended"] | None = None
    runtime: RuntimeBindingInput | None = None
    max_concurrency: int | None = Field(default=None, ge=1, le=32)
    budget_policy: dict[str, Any] | None = None
    delegation_policy: dict[str, Any] | None = None


class AgentMembershipCreate(ApiModel):
    channel_id: UUID
    activation_modes: list[ActivationMode] = Field(
        default_factory=lambda: [ActivationMode.MENTION_ONLY, ActivationMode.ASSIGNED_ONLY]
    )


class AgentResponse(ApiModel):
    id: UUID
    actor_id: UUID
    tenant_id: UUID
    space_id: UUID
    handle: str
    display_name: str
    role: str
    instructions: str
    status: str
    max_concurrency: int
    budget_policy: dict[str, Any]
    delegation_policy: dict[str, Any]
    harness: str | None = None
    worker_id: UUID | None = None
    workspace_id: UUID | None = None
    model: str | None = None
    channels: list[UUID] = Field(default_factory=list)
    created_at: datetime


class ChannelMemberResponse(ApiModel):
    actor_id: UUID
    actor_type: str
    display_name: str
    agent_id: UUID | None = None
    handle: str | None = None
    role: str


class TaskCreate(ApiModel):
    space_id: UUID
    channel_id: UUID | None = None
    thread_id: UUID | None = None
    trace_id: UUID | None = None
    parent_task_id: UUID | None = None
    assigned_agent_id: UUID | None = None
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)
    priority: int = Field(default=2, ge=0, le=4)
    workspace_id: UUID | None = None
    expected_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    deadline: datetime | None = None


class TaskUpdate(ApiModel):
    status: TaskStatus | None = None
    assigned_agent_id: UUID | None = None
    priority: int | None = Field(default=None, ge=0, le=4)
    result_summary: str | None = Field(default=None, max_length=100_000)
    result: dict[str, Any] | None = None


class TaskResponse(ApiModel):
    id: UUID
    tenant_id: UUID
    space_id: UUID
    channel_id: UUID | None
    thread_id: UUID | None
    trace_id: UUID | None
    parent_task_id: UUID | None
    created_by_actor_id: UUID
    requester_agent_id: UUID | None
    assigned_agent_id: UUID | None
    title: str
    description: str
    status: str
    priority: int
    workspace_id: UUID | None
    expected_artifacts: list[dict[str, Any]]
    result: dict[str, Any]
    result_summary: str | None
    error: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    deadline: datetime | None


class TraceResponse(ApiModel):
    id: UUID
    tenant_id: UUID
    space_id: UUID
    channel_id: UUID | None
    thread_id: UUID | None
    parent_trace_id: UUID | None
    trigger_message_id: UUID | None
    trigger_task_id: UUID | None
    initiator_actor_id: UUID
    status: str
    turn_count: int
    max_depth_seen: int
    delegation_count: int
    parallel_agents_peak: int
    token_count: int
    cost_eur: Decimal | float
    policy: dict[str, Any]
    stop_reason: str | None
    created_at: datetime
    completed_at: datetime | None


class WorkerRegister(ApiModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    labels: list[str] = Field(default_factory=list, max_length=128)


class WorkerRegisterResponse(ApiModel):
    id: UUID
    name: str
    token: str
    token_hint: str


class WorkerResponse(ApiModel):
    id: UUID
    tenant_id: UUID
    name: str
    hostname: str | None
    version: str | None
    protocol_version: str | None
    status: str
    labels: list[str]
    max_sessions: int
    available_sessions: int
    active_sessions: int
    last_heartbeat_at: datetime | None
    connected_at: datetime | None
    revoked_at: datetime | None
    harnesses: list[dict[str, Any]] = Field(default_factory=list)
    workspaces: list[dict[str, Any]] = Field(default_factory=list)


class WorkspaceResponse(ApiModel):
    id: UUID
    tenant_id: UUID
    space_id: UUID | None
    worker_id: UUID
    external_id: str
    display_name: str
    root: str
    read_only: bool
    status: str
    created_at: datetime


class WorkspaceAssign(ApiModel):
    space_id: UUID


class PermissionDecisionInput(ApiModel):
    decision: PermissionDecisionKind
    reason: str | None = Field(default=None, max_length=4000)


class PermissionRequestResponse(ApiModel):
    id: UUID
    agent_id: UUID
    session_id: UUID
    trace_id: UUID
    delivery_id: UUID | None
    capability: str
    action_summary: str
    action_details: dict[str, Any]
    workspace_id: UUID | None
    status: str
    created_at: datetime
    expires_at: datetime | None


class SessionResponse(ApiModel):
    id: UUID
    tenant_id: UUID
    space_id: UUID
    agent_id: UUID
    channel_id: UUID
    thread_id: UUID | None
    task_id: UUID | None
    workspace_id: UUID | None
    worker_id: UUID
    trace_id: UUID
    harness_type: str
    harness_session_id: str | None
    protocol_version: str
    negotiated_capabilities: dict[str, Any]
    status: str
    usage_tokens: int
    cost_eur: Decimal
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime


class SessionEventResponse(ApiModel):
    id: UUID
    session_id: UUID
    trace_id: UUID
    sequence: int
    event_type: str
    payload: dict[str, Any]
    visible_to_user: bool
    created_at: datetime


class MessagePostedTrigger(ApiModel):
    channel_ids: list[UUID] = Field(default_factory=list, max_length=64)
    actor_types: list[Literal["human", "agent", "system", "workflow"]] = Field(
        default_factory=list, max_length=4
    )


class AgentMentionedTrigger(ApiModel):
    channel_ids: list[UUID] = Field(default_factory=list, max_length=64)
    target_agent_ids: list[UUID] = Field(default_factory=list, max_length=64)


class TaskEventTrigger(ApiModel):
    channel_ids: list[UUID] = Field(default_factory=list, max_length=64)
    assigned_agent_ids: list[UUID] = Field(default_factory=list, max_length=64)


class ScheduleTrigger(ApiModel):
    interval_seconds: int = Field(ge=1, le=2_678_400)
    start_at: datetime | None = None


class WebhookTrigger(ApiModel):
    max_body_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)


class ManualTrigger(ApiModel):
    pass


class PostMessageAction(ApiModel):
    type: Literal["post_message"]
    channel_id: UUID
    content: str = Field(min_length=1, max_length=100_000)


class MentionAgentAction(ApiModel):
    type: Literal["mention_agent"]
    channel_id: UUID
    agent_id: UUID
    content: str = Field(min_length=1, max_length=100_000)


class CreateTaskAction(ApiModel):
    type: Literal["create_task"]
    channel_id: UUID | None = None
    assigned_agent_id: UUID | None = None
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)
    priority: int = Field(default=2, ge=0, le=4)
    workspace_id: UUID | None = None


class AssignTaskAction(ApiModel):
    type: Literal["assign_task"]
    task_id: UUID
    agent_id: UUID


class InvokeAgentAction(ApiModel):
    type: Literal["invoke_agent"]
    channel_id: UUID
    agent_id: UUID
    content: str = Field(min_length=1, max_length=100_000)
    task_id: UUID | None = None


class RequestApprovalAction(ApiModel):
    type: Literal["request_approval"]
    summary: str = Field(min_length=1, max_length=4_000)
    details: dict[str, Any] = Field(default_factory=dict)


class DelayAction(ApiModel):
    type: Literal["delay"]
    seconds: int = Field(ge=1, le=604_800)


class CallWebhookAction(ApiModel):
    type: Literal["call_webhook"]
    url: str = Field(min_length=9, max_length=2_048)
    payload: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=10.0, ge=0.5, le=30.0)
    max_response_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    allow_http: bool = False


WorkflowAction = Annotated[
    PostMessageAction
    | MentionAgentAction
    | CreateTaskAction
    | AssignTaskAction
    | InvokeAgentAction
    | RequestApprovalAction
    | DelayAction
    | CallWebhookAction,
    Field(discriminator="type"),
]


_TRIGGER_MODELS: dict[str, type[ApiModel]] = {
    "message_posted": MessagePostedTrigger,
    "agent_mentioned": AgentMentionedTrigger,
    "task_created": TaskEventTrigger,
    "task_completed": TaskEventTrigger,
    "schedule": ScheduleTrigger,
    "webhook": WebhookTrigger,
    "manual": ManualTrigger,
}


class WorkflowCreate(ApiModel):
    space_id: UUID
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    trigger_type: Literal[
        "message_posted",
        "agent_mentioned",
        "task_created",
        "task_completed",
        "schedule",
        "webhook",
        "manual",
    ]
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    actions: list[WorkflowAction] = Field(min_length=1, max_length=100)
    budget_policy: dict[str, Any] = Field(default_factory=dict)
    webhook_secret_ref: str | None = Field(
        default=None, min_length=3, max_length=128, pattern=r"^[A-Z][A-Z0-9_]+$"
    )

    @model_validator(mode="after")
    def validate_trigger(self) -> "WorkflowCreate":
        model = _TRIGGER_MODELS[self.trigger_type]
        self.trigger_config = model.model_validate(self.trigger_config).model_dump(mode="json")
        if self.trigger_type == "webhook" and self.webhook_secret_ref is None:
            raise ValueError("webhook_secret_ref est obligatoire pour un webhook entrant")
        if self.trigger_type != "webhook" and self.webhook_secret_ref is not None:
            raise ValueError("webhook_secret_ref n’est accepté que pour un trigger webhook")
        return self


class WorkflowUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    trigger_config: dict[str, Any] | None = None
    actions: list[WorkflowAction] | None = Field(default=None, min_length=1, max_length=100)
    budget_policy: dict[str, Any] | None = None


class WorkflowRunRequest(ApiModel):
    input: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunResume(ApiModel):
    decision: Literal["approve", "deny"]
    reason: str | None = Field(default=None, max_length=4_000)


class WorkflowResponse(ApiModel):
    id: UUID
    tenant_id: UUID
    space_id: UUID
    name: str
    description: str | None
    actor_id: UUID
    status: str
    trigger_type: str
    trigger_config: dict[str, Any]
    actions: list[dict[str, Any]]
    budget_policy: dict[str, Any]
    webhook_secret_ref: str | None
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkflowRunResponse(ApiModel):
    id: UUID
    tenant_id: UUID
    space_id: UUID
    workflow_id: UUID
    trace_id: UUID
    status: str
    trigger_type: str
    trigger_event_id: UUID | None
    trigger_payload: dict[str, Any]
    idempotency_key: str
    current_action: int
    state: dict[str, Any]
    available_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    error: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
