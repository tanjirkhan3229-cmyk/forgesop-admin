import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthError, api } from '../api'
import { setToken, getToken } from '../auth'

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: async () => body,
  } as Response)
}

describe('api client', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('attaches the operator bearer token and parses JSON', async () => {
    setToken('operator-token')
    const fetchMock = mockFetch(200, { total: 4, items: [], page: 1, page_size: 25 })
    vi.stubGlobal('fetch', fetchMock)

    const res = await api.users({ search: 'alice' })

    expect(res.total).toBe(4)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/v1/users')
    expect(url).toContain('search=alice')
    expect((init as RequestInit).headers).toMatchObject({
      Authorization: 'Bearer operator-token',
    })
  })

  it('throws AuthError and clears the token on 403', async () => {
    setToken('stale-token')
    vi.stubGlobal('fetch', mockFetch(403, { detail: 'Not a platform operator' }))

    await expect(api.overview()).rejects.toBeInstanceOf(AuthError)
    expect(getToken()).toBeNull()
  })

  it('builds the signups range query', async () => {
    setToken('t')
    const fetchMock = mockFetch(200, { range: '7d', series: [], totals: { users: 0, workspaces: 0 } })
    vi.stubGlobal('fetch', fetchMock)

    await api.signups('7d')
    expect(fetchMock.mock.calls[0][0]).toContain('/v1/signups?range=7d')
  })

  it('PATCHes a workspace with a plan_key (apply plan)', async () => {
    setToken('t')
    const fetchMock = mockFetch(200, { id: 'w1', name: 'Acme', feature_flags: {} })
    vi.stubGlobal('fetch', fetchMock)

    await api.patchWorkspace('w1', { plan_key: 'pro' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/v1/workspaces/w1')
    expect((init as RequestInit).method).toBe('PATCH')
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ plan_key: 'pro' })
  })

  it('POSTs a new plan', async () => {
    setToken('t')
    const fetchMock = mockFetch(201, { key: 'team' })
    vi.stubGlobal('fetch', fetchMock)

    await api.createPlan({ key: 'team', feature_flags: { feature_ehs_module: true } })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('/v1/plans')
    expect((init as RequestInit).method).toBe('POST')
  })

  it('builds the footprints filter query (over-limit + inactive)', async () => {
    setToken('t')
    const fetchMock = mockFetch(200, {
      items: [], total: 0, page: 1, page_size: 50, sort: 'engagement_score', order: 'desc',
    })
    vi.stubGlobal('fetch', fetchMock)

    await api.footprints({ over_seat_limit: true, inactive_days: 14, sort: 'seats_used' })
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/v1/footprints')
    expect(url).toContain('over_seat_limit=true')
    expect(url).toContain('inactive_days=14')
    expect(url).toContain('sort=seats_used')
  })

  it('omits over_seat_limit from the URL when the chip is off', async () => {
    setToken('t')
    const fetchMock = mockFetch(200, {
      items: [], total: 0, page: 1, page_size: 50, sort: 'engagement_score', order: 'desc',
    })
    vi.stubGlobal('fetch', fetchMock)

    await api.footprints({ over_seat_limit: false })
    expect(fetchMock.mock.calls[0][0] as string).not.toContain('over_seat_limit')
  })

  it('fetches a single tenant footprint detail', async () => {
    setToken('t')
    const fetchMock = mockFetch(200, { workspace_id: 'w1', name: 'Acme', trend: [], latest: null, seat_limit: 5 })
    vi.stubGlobal('fetch', fetchMock)

    const res = await api.footprint('w1')
    expect(res.workspace_id).toBe('w1')
    expect(fetchMock.mock.calls[0][0]).toContain('/v1/footprints/w1')
  })

  it('fetches composed platform health', async () => {
    setToken('t')
    const fetchMock = mockFetch(200, {
      status: 'ok',
      main_app: { status: 'ready', checks: { postgres: { status: 'ok' } } },
      rollup: { last_run: '2026-06-14T12:00:00Z', age_seconds: 12, stale: false },
    })
    vi.stubGlobal('fetch', fetchMock)
    const res = await api.health()
    expect(res.status).toBe('ok')
    expect(res.rollup.stale).toBe(false)
    expect(fetchMock.mock.calls[0][0]).toContain('/v1/health')
  })

  it('builds the api-metrics query (range/route/workspace)', async () => {
    setToken('t')
    const fetchMock = mockFetch(200, { range: '24h', series: [], by_route: [] })
    vi.stubGlobal('fetch', fetchMock)
    await api.apiMetrics({ range: '24h', route: '/api/v1/x', workspace: 'ws-1' })
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/v1/metrics/api')
    expect(url).toContain('range=24h')
    expect(url).toContain('workspace=ws-1')
  })

  it('fetches rate-limit offenders for a range', async () => {
    setToken('t')
    const fetchMock = mockFetch(200, { range: '1h', total: 0, offenders: [], by_route: [], by_workspace: [] })
    vi.stubGlobal('fetch', fetchMock)
    await api.rateLimits('1h')
    expect(fetchMock.mock.calls[0][0]).toContain('/v1/metrics/rate-limits?range=1h')
  })
})
