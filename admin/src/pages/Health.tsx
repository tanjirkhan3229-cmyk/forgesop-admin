import { Activity, CheckCircle2, AlertTriangle, XCircle, Clock } from 'lucide-react'
import { useHealth } from '../hooks/queries'
import type { DepCheck } from '../lib/api'

function Light({ status }: { status: string }) {
  const ok = status === 'ok' || status === 'ready'
  const skipped = status === 'skipped'
  const degraded = status === 'degraded'
  const Icon = ok ? CheckCircle2 : degraded || skipped ? AlertTriangle : XCircle
  const tone = ok
    ? 'text-emerald-600'
    : degraded || skipped
      ? 'text-amber-500'
      : 'text-red-600'
  return <Icon className={`h-5 w-5 ${tone}`} />
}

function DepRow({ name, check }: { name: string; check: DepCheck }) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-slate-100 px-3 py-2">
      <span className="flex items-center gap-2">
        <Light status={check.status} />
        <span className="font-medium capitalize text-slate-800">{name}</span>
      </span>
      <span className="text-xs text-slate-500">
        {check.status}
        {check.detail ? ` — ${check.detail}` : ''}
      </span>
    </div>
  )
}

/** Health panel — dependency lights (main app /ready) + rollup freshness. */
export function Health() {
  const { data, isLoading, error } = useHealth()

  if (isLoading) return <div className="text-sm text-slate-500">Loading health…</div>
  if (error || !data) return <div className="text-sm text-red-600">Failed to load health.</div>

  const checks = data.main_app.checks ?? {}
  const overallOk = data.status === 'ok'

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Activity className="h-6 w-6 text-brand-600" />
        <h2 className="text-xl font-semibold">Platform health</h2>
        <span
          className={
            'rounded-full px-2.5 py-0.5 text-xs font-semibold ' +
            (overallOk ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700')
          }
        >
          {data.status}
        </span>
      </div>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-slate-700">
          Main app{' '}
          <span className="font-normal text-slate-400">
            (/ready — {data.main_app.status})
          </span>
        </h3>
        {Object.keys(checks).length === 0 ? (
          <div className="rounded-lg border border-slate-100 px-3 py-2 text-sm text-slate-500">
            No dependency detail{data.main_app.detail ? ` — ${data.main_app.detail}` : ''}.
          </div>
        ) : (
          <div className="space-y-1.5">
            {Object.entries(checks).map(([name, check]) => (
              <DepRow key={name} name={name} check={check} />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-slate-700">Telemetry rollup</h3>
        <div className="flex items-center justify-between rounded-lg border border-slate-100 px-3 py-2">
          <span className="flex items-center gap-2">
            <Clock className={`h-5 w-5 ${data.rollup.stale ? 'text-amber-500' : 'text-emerald-600'}`} />
            <span className="font-medium text-slate-800">Last rollup</span>
          </span>
          <span className="text-xs text-slate-500">
            {data.rollup.last_run
              ? `${data.rollup.age_seconds}s ago${data.rollup.stale ? ' · STALE' : ''}`
              : 'never run'}
          </span>
        </div>
      </section>
    </div>
  )
}
