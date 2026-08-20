export type UUID = string

export interface AuthUser {
  user_id: UUID
  actor_id: UUID
  tenant_id: UUID
  email: string
  display_name: string
  is_owner: boolean
  csrf_token?: string | null
}

export interface Space {
  id: UUID
  tenant_id: UUID
  name: string
  slug: string
  kind: 'business' | 'personal' | 'custom' | string
  description: string | null
  created_at: string
}

export interface Channel {
  id: UUID
  tenant_id: UUID
  space_id: UUID
  name: string
  slug: string
  kind: 'discussion' | 'project' | 'private' | string
  description: string | null
  is_archived: boolean
  created_at: string
}

export interface MessageMention {
  id: UUID
  target_type: 'human' | 'agent' | string
  target_id: UUID
  handle_at_creation: string
}

export interface MessageMentionInput {
  target_type: 'human' | 'agent'
  target_id: UUID
  handle_at_creation: string
}

export interface Message {
  id: UUID
  tenant_id: UUID
  space_id: UUID
  channel_id: UUID
  thread_id: UUID | null
  author_type: 'human' | 'agent' | 'system' | 'workflow' | string
  author_id: UUID
  author_display_name: string | null
  author_handle: string | null
  content: string
  reply_to_id: UUID | null
  trace_id: UUID | null
  task_id: UUID | null
  expects_response: boolean
  is_technical: boolean
  mentions: MessageMention[]
  created_at: string
}

export interface ChannelMember {
  actor_id: UUID
  actor_type: 'human' | 'agent' | 'system' | 'workflow' | string
  display_name: string
  agent_id: UUID | null
  handle: string | null
  role: string
}

export interface Agent {
  id: UUID
  actor_id: UUID
  tenant_id: UUID
  space_id: UUID
  handle: string
  display_name: string
  role: string
  instructions: string
  status: string
  max_concurrency: number
  budget_policy: Record<string, unknown>
  delegation_policy: Record<string, unknown>
  harness: string | null
  worker_id: UUID | null
  workspace_id: UUID | null
  model: string | null
  channels: UUID[]
  created_at: string
}

export interface FleetTask {
  id: UUID
  tenant_id: UUID
  space_id: UUID
  channel_id: UUID | null
  thread_id: UUID | null
  trace_id: UUID | null
  parent_task_id: UUID | null
  created_by_actor_id: UUID
  requester_agent_id: UUID | null
  assigned_agent_id: UUID | null
  title: string
  description: string
  status: string
  priority: number
  workspace_id: UUID | null
  expected_artifacts: Record<string, unknown>[]
  result: Record<string, unknown>
  result_summary: string | null
  error: Record<string, unknown> | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  deadline: string | null
}

export interface Trace {
  id: UUID
  tenant_id: UUID
  space_id: UUID
  channel_id: UUID | null
  thread_id: UUID | null
  parent_trace_id: UUID | null
  trigger_message_id: UUID | null
  trigger_task_id: UUID | null
  initiator_actor_id: UUID
  status: string
  turn_count: number
  max_depth_seen: number
  delegation_count: number
  parallel_agents_peak: number
  token_count: number
  cost_eur: number | string
  policy: Record<string, unknown>
  stop_reason: string | null
  created_at: string
  completed_at: string | null
}

export interface Worker {
  id: UUID
  tenant_id: UUID
  name: string
  hostname: string | null
  version: string | null
  protocol_version: string | null
  status: string
  labels: string[]
  max_sessions: number
  available_sessions: number
  active_sessions: number
  last_heartbeat_at: string | null
  connected_at: string | null
  revoked_at: string | null
  harnesses: Array<Record<string, unknown>>
  workspaces: Array<Record<string, unknown>>
}

export interface WorkerRegistration {
  id: UUID
  name: string
  token: string
  token_hint: string
}

export interface Workspace {
  id: UUID
  tenant_id: UUID
  space_id: UUID | null
  worker_id: UUID
  external_id: string
  display_name: string
  root: string
  read_only: boolean
  status: string
  created_at: string
}

export interface PermissionRequest {
  id: UUID
  agent_id: UUID
  session_id: UUID
  trace_id: UUID
  delivery_id: UUID | null
  capability: string
  action_summary: string
  action_details: Record<string, unknown>
  workspace_id: UUID | null
  status: string
  created_at: string
  expires_at: string | null
}

export interface Workflow {
  id: UUID
  tenant_id: UUID
  space_id: UUID
  name: string
  description: string | null
  status: string
  trigger_type: string
  trigger_config: Record<string, unknown>
  actions: Array<Record<string, unknown>>
  created_at: string
}

export interface EventEnvelope {
  event_id: UUID
  event_type: string
  event_version: number
  tenant_id?: UUID
  space_id?: UUID | null
  channel_id?: UUID | null
  actor_type?: string | null
  actor_id?: UUID | null
  trace_id?: UUID | null
  correlation_id?: UUID | null
  causation_id?: UUID | null
  idempotency_key?: string | null
  occurred_at: string
  payload: Record<string, unknown>
}

export interface Page<T> {
  items: T[]
  total?: number
  next_cursor?: string | null
}

export type ListResponse<T> = T[] | Page<T>
