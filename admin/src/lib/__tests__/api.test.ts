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
})
