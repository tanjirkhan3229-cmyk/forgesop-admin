/**
 * Admin API client. Attaches the operator Bearer token to every request and
 * treats a 403 as "not (or no longer) an operator" — clear the token and bounce
 * to login. The backend returns 403 (never 404) for non-operators.
 */

import { clearToken, getToken } from './auth'

const BASE = import.meta.env.VITE_ADMIN_API_BASE || ''

export interface Me {
  id: string
  email: string
  role: 'PLATFORM_SUPPORT' | 'PLATFORM_OPS' | 'PLATFORM_ADMIN'
  capabilities: string[]
}

export interface Overview {
  signups: { last_24h: number; last_7d: number; last_30d: number }
  active_workspaces: number
  total_workspaces: number
  total_users: number
}

export interface SignupPoint {
  date: string
  users: number
  workspaces: number
}

export interface SignupSeries {
  range: string
  series: SignupPoint[]
  totals: { users: number; workspaces: number }
}

export interface Paginated<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface WorkspaceRow {
  id: string
  name: string
  slug: string | null
  is_suspended: boolean
  created_at: string | null
  member_count: number
  last_activity: string | null
  plan: string | null
}

export interface WorkspaceMember {
  id: string
  email: string
  name: string | null
  role: string | null
  status: string | null
  last_active_at: string | null
}

export interface AuditEvent {
  audit_id: string
  timestamp: string | null
  event_type: string | null
  action: string | null
  actor_email: string | null
  actor_name: string | null
}

export interface WorkspaceDetail {
  id: string
  name: string
  slug: string | null
  is_suspended: boolean
  created_at: string | null
  plan: string | null
  feature_flags: Record<string, boolean>
  member_count: number
  members: WorkspaceMember[]
  recent_activity: AuditEvent[]
}

export interface UserRow {
  id: string
  email: string
  name: string | null
  role: string | null
  status: string | null
  workspace_id: string | null
  workspace_name: string | null
  last_active_at: string | null
  created_at: string | null
}

export interface Plan {
  id: string
  key: string
  name: string | null
  description: string | null
  feature_flags: Record<string, boolean>
  limits: Record<string, number | null>
  is_public: boolean
  sort_order: number
  stripe_price_id: string | null
  monthly_price_cents: number | null
}

export interface PlanInput {
  key?: string
  name?: string
  description?: string
  feature_flags?: Record<string, boolean>
  limits?: Record<string, number | null>
  is_public?: boolean
  sort_order?: number
  monthly_price_cents?: number | null
}

export interface WorkspacePatch {
  plan_key?: string
  flags?: Record<string, boolean>
  limits?: Record<string, number | null>
}

export class AuthError extends Error {}

type QueryParams = Record<string, string | number | undefined | null>

function qs(params: QueryParams): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken()
  const resp = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers || {}),
    },
  })

  if (resp.status === 403) {
    clearToken()
    throw new AuthError('Not a platform operator')
  }
  if (!resp.ok) {
    throw new Error(`${resp.status} ${resp.statusText}`)
  }
  return (await resp.json()) as T
}

export interface WorkspaceQuery {
  search?: string
  page?: number
  page_size?: number
}

export interface UserQuery {
  search?: string
  workspace_id?: string
  role?: string
  status?: string
  page?: number
  page_size?: number
}

export const api = {
  me: () => request<Me>('/v1/me'),
  overview: () => request<Overview>('/v1/overview'),
  signups: (range: string) => request<SignupSeries>(`/v1/signups${qs({ range })}`),
  workspaces: (q: WorkspaceQuery = {}) =>
    request<Paginated<WorkspaceRow>>(`/v1/workspaces${qs(q as QueryParams)}`),
  workspace: (id: string) => request<WorkspaceDetail>(`/v1/workspaces/${id}`),
  users: (q: UserQuery = {}) => request<Paginated<UserRow>>(`/v1/users${qs(q as QueryParams)}`),

  plans: () => request<Plan[]>('/v1/plans'),
  createPlan: (body: PlanInput) =>
    request<Plan>('/v1/plans', { method: 'POST', body: JSON.stringify(body) }),
  updatePlan: (key: string, body: PlanInput) =>
    request<Plan>(`/v1/plans/${key}`, { method: 'PATCH', body: JSON.stringify(body) }),
  patchWorkspace: (id: string, body: WorkspacePatch) =>
    request<WorkspaceDetail>(`/v1/workspaces/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
}
