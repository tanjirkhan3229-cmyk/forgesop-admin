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
  stripe_price_id?: string | null
}

export interface Invoice {
  id: string | null
  number: string | null
  status: string | null
  amount_due: number | null
  amount_paid: number | null
  currency: string | null
  created: number | null // unix seconds
  hosted_invoice_url: string | null
  invoice_pdf: string | null
}

export interface WorkspaceInvoices {
  customer_id: string | null
  invoices: Invoice[]
}

export interface WorkspacePatch {
  plan_key?: string
  flags?: Record<string, boolean>
  limits?: Record<string, number | null>
}

export interface AlertThresholds {
  signup_drop_pct: number
  signup_window_days: number
  signup_min_baseline: number
  over_seat_limit_enabled: boolean
  error_rate_pct: number
  alert_cooldown_hours: number
}

export interface DigestConfig {
  enabled: boolean
  frequency: 'daily' | 'weekly'
}

export interface PlatformSettings {
  alert_thresholds: AlertThresholds
  digest: DigestConfig
  recipients: string[]
}

export interface SettingsPatch {
  alert_thresholds?: Partial<AlertThresholds>
  digest?: Partial<DigestConfig>
  recipients?: string[]
}

export interface LoginResult {
  status: 'ok' | 'password_set_required'
  token?: string
  token_type?: string
  expires_in?: number
  email?: string
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
  invoices: (workspaceId: string) =>
    request<WorkspaceInvoices>(`/v1/billing/invoices${qs({ workspace_id: workspaceId })}`),

  settings: () => request<PlatformSettings>('/v1/settings'),
  updateSettings: (body: SettingsPatch) =>
    request<PlatformSettings>('/v1/settings', { method: 'PUT', body: JSON.stringify(body) }),

  // Local email+password auth (PLATFORM_LOCAL_AUTH mode). Unauthenticated.
  authLogin: (email: string, password: string) =>
    request<LoginResult>('/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  authSetPassword: (email: string, password: string) =>
    request<{ status: string }>('/v1/auth/set-password', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
}
