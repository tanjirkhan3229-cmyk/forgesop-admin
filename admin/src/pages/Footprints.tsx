import { useState } from 'react'
import { AlertTriangle, ArrowLeft, MoonStar, Search } from 'lucide-react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useFootprint, useFootprints } from '../hooks/queries'
import type { FootprintRow } from '../lib/api'
import { formatBytes, formatNumber, relativeTime } from '../lib/format'

const INACTIVE_THRESHOLD_DAYS = 14

/** Operator-facing usage & engagement directory (Phase 3). */
export function Footprints() {
  const [search, setSearch] = useState('')
  const [overLimit, setOverLimit] = useState(false)
  const [inactiveOnly, setInactiveOnly] = useState(false)
  const [sort, setSort] = useState('engagement_score')
  const [selected, setSelected] = useState<string | null>(null)

  const { data, isLoading } = useFootprints({
    search,
    over_seat_limit: overLimit,
    inactive_days: inactiveOnly ? INACTIVE_THRESHOLD_DAYS : undefined,
    sort,
    order: 'desc',
    page: 1,
    page_size: 50,
  })

  if (selected) return <FootprintDetail id={selected} onBack={() => setSelected(null)} />

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search tenants…"
            className="w-full rounded-lg border border-slate-200 py-2 pl-8 pr-3 text-sm"
          />
        </div>

        <Chip active={overLimit} onClick={() => setOverLimit((v) => !v)} icon={AlertTriangle}>
          Over seat limit
        </Chip>
        <Chip active={inactiveOnly} onClick={() => setInactiveOnly((v) => !v)} icon={MoonStar}>
          Inactive ≥ {INACTIVE_THRESHOLD_DAYS}d
        </Chip>

        <select
          value={sort}
          onChange={(e) => setSort(e.target.value)}
          className="ml-auto rounded-lg border border-slate-200 px-2 py-1.5 text-sm text-slate-600"
        >
          <option value="engagement_score">Sort: Engagement</option>
          <option value="seats_used">Sort: Seats used</option>
          <option value="active_users_7d">Sort: Active users (7d)</option>
          <option value="storage_bytes">Sort: Storage</option>
          <option value="last_active_at">Sort: Last active</option>
        </select>
        {data && <span className="text-sm text-slate-500">{formatNumber(data.total)} shown</span>}
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2 font-medium">Tenant</th>
              <th className="px-4 py-2 font-medium">Engagement</th>
              <th className="px-4 py-2 font-medium">Seats</th>
              <th className="px-4 py-2 font-medium">Active 7d</th>
              <th className="px-4 py-2 font-medium">Objects</th>
              <th className="px-4 py-2 font-medium">Storage</th>
              <th className="px-4 py-2 font-medium">Last active</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={7} className="px-4 py-6 text-center text-slate-400">Loading…</td></tr>
            ) : data && data.items.length === 0 ? (
              <tr><td colSpan={7} className="px-4 py-6 text-center text-slate-400">No tenants match.</td></tr>
            ) : (
              data?.items.map((f) => (
                <tr
                  key={f.workspace_id}
                  onClick={() => setSelected(f.workspace_id)}
                  className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                >
                  <td className="px-4 py-2 font-medium text-slate-900">{f.name ?? f.workspace_id}</td>
                  <td className="px-4 py-2"><EngagementBadge score={f.engagement_score} /></td>
                  <td className="px-4 py-2 text-slate-700">
                    {formatNumber(f.seats_used)}
                    {f.seat_limit != null && <span className="text-slate-400"> / {formatNumber(f.seat_limit)}</span>}
                    {f.over_seat_limit && (
                      <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-700">
                        over
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-slate-700">{formatNumber(f.active_users_7d)}</td>
                  <td className="px-4 py-2 text-slate-700">{formatNumber(objectTotal(f))}</td>
                  <td className="px-4 py-2 text-slate-700">{formatBytes(f.storage_bytes)}</td>
                  <td className="px-4 py-2 text-slate-700">{relativeTime(f.last_active_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function objectTotal(f: FootprintRow): number {
  return f.sops_count + f.incidents_count + f.capas_count + f.risks_count
}

function Chip({
  active,
  onClick,
  icon: Icon,
  children,
}: {
  active: boolean
  onClick: () => void
  icon: typeof AlertTriangle
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={
        'flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium ' +
        (active
          ? 'bg-brand-600 text-white'
          : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50')
      }
    >
      <Icon className="h-3.5 w-3.5" />
      {children}
    </button>
  )
}

function EngagementBadge({ score }: { score: number }) {
  const tone =
    score >= 66 ? 'bg-emerald-100 text-emerald-700'
    : score >= 33 ? 'bg-amber-100 text-amber-700'
    : 'bg-slate-100 text-slate-600'
  return (
    <span className={'inline-block rounded px-2 py-0.5 text-xs font-semibold ' + tone}>
      {score.toFixed(1)}
    </span>
  )
}

function FootprintDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const { data, isLoading, error } = useFootprint(id)

  if (isLoading) return <div className="text-sm text-slate-500">Loading…</div>
  if (error || !data) return <div className="text-sm text-red-600">Failed to load footprint.</div>

  const latest = data.latest

  return (
    <div className="space-y-6">
      <button onClick={onBack} className="flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900">
        <ArrowLeft className="h-4 w-4" /> Back to footprints
      </button>

      <div className="flex items-center gap-3">
        <h2 className="text-xl font-semibold">{data.name ?? data.workspace_id}</h2>
        {latest?.over_seat_limit && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">
            Over seat limit
          </span>
        )}
      </div>

      {latest && (
        <div className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4 lg:grid-cols-6">
          <Stat label="Engagement"><EngagementBadge score={latest.engagement_score} /></Stat>
          <Stat label="Seats used">
            {formatNumber(latest.seats_used)}
            {data.seat_limit != null && <span className="text-slate-400"> / {formatNumber(data.seat_limit)}</span>}
          </Stat>
          <Stat label="Active (1d/7d/30d)">
            {latest.active_users_1d}/{latest.active_users_7d}/{latest.active_users_30d}
          </Stat>
          <Stat label="Objects">{formatNumber(objectTotal(latest))}</Stat>
          <Stat label="Storage">{formatBytes(latest.storage_bytes)}</Stat>
          <Stat label="Last active">{relativeTime(latest.last_active_at)}</Stat>
        </div>
      )}

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-700">Usage trend</h3>
        {data.trend.length === 0 ? (
          <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-400">
            No snapshots yet — the daily rollup has not run for this tenant.
          </div>
        ) : (
          <div className="h-80 rounded-xl border border-slate-200 bg-white p-4">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.trend}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="day" tick={{ fontSize: 11 }} minTickGap={24} />
                <YAxis yAxisId="left" allowDecimals={false} tick={{ fontSize: 11 }} width={32} />
                <YAxis yAxisId="right" orientation="right" domain={[0, 100]} tick={{ fontSize: 11 }} width={32} />
                <Tooltip />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="engagement_score"
                  name="Engagement"
                  stroke="#4f46e5"
                  dot={false}
                />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="active_users_7d"
                  name="Active users (7d)"
                  stroke="#10b981"
                  dot={false}
                />
                <Line
                  yAxisId="left"
                  type="monotone"
                  dataKey="seats_used"
                  name="Seats used"
                  stroke="#94a3b8"
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>
    </div>
  )
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-slate-500">{label}</div>
      <div className="mt-0.5 font-medium text-slate-900">{children}</div>
    </div>
  )
}
