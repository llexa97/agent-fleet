import type {
  Agent,
  AuthUser,
  Channel,
  ChannelMember,
  FleetTask,
  ListResponse,
  Message,
  MessageMentionInput,
  PermissionRequest,
  Space,
  Trace,
  UUID,
  Worker,
  WorkerRegistration,
  Workflow,
  Workspace,
} from '../types/api'

const configuredBase = import.meta.env.VITE_API_BASE ?? '/api/v1'
export const API_BASE = configuredBase.replace(/\/$/, '')

let csrfFromAuth: string | null = null

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown

  constructor(message: string, status: number, code = 'api_error', details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

interface ApiRequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  query?: Record<string, string | number | boolean | null | undefined>
  idempotent?: boolean
}

function cookieValue(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`
  const cookie = document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : null
}

function csrfToken(): string | null {
  return csrfFromAuth ?? cookieValue('agent_fleet_csrf')
}

export function rememberCsrf(token?: string | null): void {
  csrfFromAuth = token ?? null
}

function requestId(): string {
  return globalThis.crypto.randomUUID()
}

function makeUrl(path: string, query?: ApiRequestOptions['query']): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  const baseIsAbsolute = /^https?:\/\//.test(API_BASE)
  const url = new URL(`${API_BASE}${normalizedPath}`, baseIsAbsolute ? undefined : window.location.origin)
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null) {
      url.searchParams.set(key, String(value))
    }
  }
  return baseIsAbsolute ? url.toString() : `${url.pathname}${url.search}`
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  let body: unknown
  try {
    body = await response.json()
  } catch {
    body = null
  }

  if (typeof body === 'object' && body !== null) {
    const record = body as Record<string, unknown>
    const detail = record.detail
    if (typeof detail === 'string') {
      return new ApiError(detail, response.status)
    }
    if (typeof detail === 'object' && detail !== null) {
      const detailRecord = detail as Record<string, unknown>
      return new ApiError(
        typeof detailRecord.message === 'string' ? detailRecord.message : response.statusText,
        response.status,
        typeof detailRecord.code === 'string' ? detailRecord.code : 'api_error',
        detailRecord.details,
      )
    }
    return new ApiError(
      typeof record.message === 'string' ? record.message : response.statusText,
      response.status,
      typeof record.code === 'string' ? record.code : 'api_error',
      record.details,
    )
  }
  return new ApiError(response.statusText || 'La requête a échoué', response.status)
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const { body, query, idempotent, headers: providedHeaders, ...init } = options
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(providedHeaders)
  headers.set('Accept', 'application/json')
  headers.set('X-Request-ID', requestId())
  if (body !== undefined) headers.set('Content-Type', 'application/json')
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const token = csrfToken()
    if (token) headers.set('X-CSRF-Token', token)
    if (idempotent) headers.set('Idempotency-Key', requestId())
  }

  const response = await fetch(makeUrl(path, query), {
    ...init,
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
    headers,
    credentials: 'include',
  })
  if (!response.ok) throw await errorFromResponse(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function itemsFrom<T>(response: ListResponse<T>): T[] {
  return Array.isArray(response) ? response : response.items
}

export const api = {
  auth: {
    me: () => apiRequest<AuthUser>('/auth/me'),
    login: async (email: string, password: string) => {
      const result = await apiRequest<AuthUser>('/auth/login', {
        method: 'POST',
        body: { email, password },
      })
      rememberCsrf(result.csrf_token)
      return result
    },
    bootstrap: async (input: {
      email: string
      display_name: string
      password: string
      tenant_name: string
      bootstrap_token: string
    }) => {
      const { bootstrap_token, ...body } = input
      const result = await apiRequest<AuthUser>('/auth/bootstrap', {
        method: 'POST',
        body,
        headers: { 'X-Bootstrap-Token': bootstrap_token },
      })
      rememberCsrf(result.csrf_token)
      return result
    },
    logout: async () => {
      await apiRequest<undefined>('/auth/logout', { method: 'POST' })
      rememberCsrf(null)
    },
  },
  spaces: {
    list: async () => itemsFrom(await apiRequest<ListResponse<Space>>('/spaces')),
    create: (body: Pick<Space, 'name' | 'slug' | 'kind'> & { description?: string }) =>
      apiRequest<Space>('/spaces', { method: 'POST', body, idempotent: true }),
  },
  channels: {
    list: async (spaceId?: UUID) =>
      itemsFrom(
        await apiRequest<ListResponse<Channel>>('/channels', {
          query: { space_id: spaceId },
        }),
      ),
    create: (body: {
      space_id: UUID
      name: string
      slug: string
      kind: string
      description?: string
    }) => apiRequest<Channel>('/channels', { method: 'POST', body, idempotent: true }),
    messages: async (channelId: UUID) =>
      itemsFrom(
        await apiRequest<ListResponse<Message>>(`/channels/${channelId}/messages`, {
          query: { limit: 100 },
        }),
      ),
    members: async (channelId: UUID) =>
      itemsFrom(
        await apiRequest<ListResponse<ChannelMember>>(`/channels/${channelId}/members`),
      ),
    postMessage: (
      channelId: UUID,
      body: {
        content: string
        mentions: MessageMentionInput[]
        expects_response: boolean
        reply_to_id?: UUID
        thread_id?: UUID
      },
    ) =>
      apiRequest<Message>(`/channels/${channelId}/messages`, {
        method: 'POST',
        body,
        idempotent: true,
      }),
  },
  agents: {
    list: async (spaceId?: UUID) =>
      itemsFrom(
        await apiRequest<ListResponse<Agent>>('/agents', { query: { space_id: spaceId } }),
      ),
    create: (body: {
      space_id: UUID
      handle: string
      display_name: string
      role: string
      instructions: string
      runtime: {
        harness: string
        worker_id?: UUID
        workspace_id?: UUID
        model?: string
        runner_labels: string[]
      }
      max_concurrency: number
      budget_policy: Record<string, unknown>
      delegation_policy: Record<string, unknown>
    }) => apiRequest<Agent>('/agents', { method: 'POST', body, idempotent: true }),
    addMembership: (agentId: UUID, channelId: UUID) =>
      apiRequest<{ id: UUID; status: string }>(`/agents/${agentId}/memberships`, {
        method: 'POST',
        body: {
          channel_id: channelId,
          activation_modes: ['mention_only', 'assigned_only'],
        },
        idempotent: true,
      }),
  },
  tasks: {
    list: async () => itemsFrom(await apiRequest<ListResponse<FleetTask>>('/tasks')),
    create: (body: {
      space_id: UUID
      channel_id?: UUID
      assigned_agent_id?: UUID
      title: string
      description: string
      priority: number
    }) => apiRequest<FleetTask>('/tasks', { method: 'POST', body, idempotent: true }),
  },
  traces: {
    list: async () => itemsFrom(await apiRequest<ListResponse<Trace>>('/traces')),
    action: (traceId: UUID, action: 'pause' | 'resume' | 'cancel') =>
      apiRequest<Trace>(`/traces/${traceId}/${action}`, {
        method: 'POST',
        body: {},
        idempotent: true,
      }),
  },
  workers: {
    list: async () => itemsFrom(await apiRequest<ListResponse<Worker>>('/workers')),
    register: (name: string, labels: string[]) =>
      apiRequest<WorkerRegistration>('/workers', {
        method: 'POST',
        body: { name, labels },
        idempotent: true,
      }),
  },
  workspaces: {
    list: async (workerId?: UUID, spaceId?: UUID) =>
      itemsFrom(
        await apiRequest<ListResponse<Workspace>>('/workspaces', {
          query: { worker_id: workerId, space_id: spaceId },
        }),
      ),
  },
  permissions: {
    list: async (status = 'pending') =>
      itemsFrom(
        await apiRequest<ListResponse<PermissionRequest>>('/permissions', { query: { status } }),
      ),
    decide: (id: UUID, decision: 'deny' | 'allow_once' | 'allow_session' | 'allow_agent') =>
      apiRequest<PermissionRequest>(`/permissions/${id}/decide`, {
        method: 'POST',
        body: { decision },
        idempotent: true,
      }),
  },
  workflows: {
    list: async () => itemsFrom(await apiRequest<ListResponse<Workflow>>('/workflows')),
    create: (body: {
      space_id: UUID
      name: string
      description?: string
      trigger_type: string
      trigger_config: Record<string, unknown>
      actions: Array<Record<string, unknown>>
    }) => apiRequest<Workflow>('/workflows', { method: 'POST', body, idempotent: true }),
  },
}

export function websocketUrl(): string {
  const base = /^https?:\/\//.test(API_BASE)
    ? new URL(API_BASE)
    : new URL(API_BASE, window.location.origin)
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
  base.pathname = `${base.pathname.replace(/\/$/, '')}/events/ws`
  base.search = ''
  return base.toString()
}
