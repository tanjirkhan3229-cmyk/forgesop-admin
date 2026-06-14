import { useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useApiMetrics, useRateLimits } from '../hooks/queries'
import type { ApiMetricPoint } from '../lib/api'
import { formatNumber } from '../lib/format'

const RANGES = ['1h', '6h', '24h', '7d']

function pct(n: number): string {
  return `${(n * 100).toFixed(1)}%`
}

/** Aggregate the series into a per-bucket volume timeline for the chart. */
function volumeByBucket(series: ApiMetricPoint[]) {
  const byBucket = new Map<string, { bucket: string; count: number; errors: number }>()
  for (const p of series) {
    const key = p.bucket_start ?? '—'
    const row = byBucket.get(key) ?? { bucket: key.slice(11, 16), count: 0, errors: 0 }
    row.count += p.count
    row.errors += p.error_count
    byBucket.set(key, row)
  }
  return Array.from(byBucket.values())
}

export function ApiMetrics() {
  const [range, setRange] = useState('1h')
  const metrics = useApiMetrics({ range })
  const rateLimits = useRateLimits(range)

  const chartData = useMemo(
    () => (metrics.data ? volumeByBucket(metrics.data.series) : []),
    [metrics.data],
  )

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        {RANGES.map((r) => (
          <button
            key={r}
            onClick={() => setRange(r)}
            className={
              'rounded-lg px-3 py-1.5 text-sm font-medium ' +
              (r === range
                ? 'bg-brand-600 text-white'
                : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50')
            }
          >
            {r}
          </button>
        ))}
      </div>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-700">Request volume</h3>
        <div className="h-64 rounded-xl border border-slate-200 bg-white p-4">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="bucket" tick={{ fontSize: 11 }} minTickGap={20} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11 }} width={32} />
              <Tooltip />
              <Bar dataKey="count" name="Requests" fill="#4f46e5" />
              <Bar dataKey="errors" name="Errors" fill="#ef4444" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-700">By route</h3>
        <Table
          headers={['Route', 'Volume', 'Error rate', 'p50', 'p95', 'p99']}
          rows={
            metrics.data?.by_route.map((r) => [
              r.route,
              formatNumber(r.count),
              <span className={r.error_rate > 0.05 ? 'font-semibold text-red-600' : ''}>{pct(r.error_rate)}</span>,
              `${r.p50_ms}ms`,
              `${r.p95_ms}ms`,
              `${r.p99_ms}ms`,
            ]) ?? []
          }
          empty={metrics.isLoading ? 'Loading…' : 'No traffic in range.'}
        />
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-700">
          Rate-limit offenders{' '}
          <span className="font-normal text-slate-400">
            ({formatNumber(rateLimits.data?.total ?? 0)} 429s)
          </span>
        </h3>
        <Table
          headers={['Route', 'Workspace', '429s']}
          rows={
            rateLimits.data?.offenders.map((o) => [
              o.route ?? '—',
              o.workspace_id ?? '— (anon/IP)',
              formatNumber(o.count),
            ]) ?? []
          }
          empty={rateLimits.isLoading ? 'Loading…' : 'No rate-limit hits in range.'}
        />
      </section>
    </div>
  )
}

function Table({
  headers,
  rows,
  empty,
}: {
  headers: string[]
  rows: (string | number | React.ReactNode)[][]
  empty: string
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
          <tr>{headers.map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td colSpan={headers.length} className="px-4 py-6 text-center text-slate-400">{empty}</td></tr>
          ) : (
            rows.map((r, i) => (
              <tr key={i} className="border-t border-slate-100">
                {r.map((c, j) => <td key={j} className="px-4 py-2 text-slate-700">{c}</td>)}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}
