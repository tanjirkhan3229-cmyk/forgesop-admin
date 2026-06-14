import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useOverview, useSignups } from '../hooks/queries'
import { formatNumber } from '../lib/format'

function KpiCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  )
}

export function Overview() {
  const overview = useOverview()
  const signups = useSignups('30d')

  if (overview.isLoading) return <div className="text-sm text-slate-500">Loading…</div>
  if (overview.error || !overview.data)
    return <div className="text-sm text-red-600">Failed to load overview.</div>

  const o = overview.data
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard label="Signups · 24h" value={formatNumber(o.signups.last_24h)} />
        <KpiCard label="Signups · 7d" value={formatNumber(o.signups.last_7d)} />
        <KpiCard label="Signups · 30d" value={formatNumber(o.signups.last_30d)} />
        <KpiCard
          label="Active workspaces"
          value={formatNumber(o.active_workspaces)}
          sub={`${formatNumber(o.total_workspaces)} total`}
        />
      </div>
      <KpiCard label="Total users" value={formatNumber(o.total_users)} />

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <h2 className="mb-4 text-sm font-semibold text-slate-700">Signups · last 30 days</h2>
        <div className="h-72">
          {signups.data && (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={signups.data.series}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={24} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} width={28} />
                <Tooltip />
                <Bar dataKey="users" name="Users" fill="#4f46e5" radius={[2, 2, 0, 0]} />
                <Bar dataKey="workspaces" name="Workspaces" fill="#94a3b8" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  )
}
