import { useState } from 'react'
import { Save } from 'lucide-react'
import { usePlans, useUpdatePlan } from '../hooks/queries'
import type { Plan } from '../lib/api'

function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!on)}
      className={
        'relative h-5 w-9 rounded-full transition ' + (on ? 'bg-brand-600' : 'bg-slate-300')
      }
    >
      <span
        className={
          'absolute top-0.5 h-4 w-4 rounded-full bg-white transition ' +
          (on ? 'left-4' : 'left-0.5')
        }
      />
    </button>
  )
}

function PlanCard({ plan }: { plan: Plan }) {
  const update = useUpdatePlan()
  const [flags, setFlags] = useState<Record<string, boolean>>(plan.feature_flags)
  const [limits, setLimits] = useState<Record<string, number | null>>(plan.limits)

  const dirty =
    JSON.stringify(flags) !== JSON.stringify(plan.feature_flags) ||
    JSON.stringify(limits) !== JSON.stringify(plan.limits)

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="mb-3 flex items-baseline justify-between">
        <div>
          <h3 className="text-base font-semibold text-slate-900">{plan.name ?? plan.key}</h3>
          <p className="text-xs text-slate-500">{plan.description}</p>
        </div>
        <span className="text-xs font-mono text-slate-400">{plan.key}</span>
      </div>

      <div className="mb-4">
        <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
          Feature flags
        </div>
        <div className="space-y-1.5">
          {Object.entries(flags).map(([k, v]) => (
            <div key={k} className="flex items-center justify-between">
              <span className="font-mono text-xs text-slate-700">{k}</span>
              <Toggle on={v} onChange={(nv) => setFlags({ ...flags, [k]: nv })} />
            </div>
          ))}
        </div>
      </div>

      <div className="mb-4">
        <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
          Limits
        </div>
        <div className="grid grid-cols-2 gap-2">
          {Object.entries(limits).map(([k, v]) => (
            <label key={k} className="text-xs text-slate-600">
              {k}
              <input
                type="number"
                value={v ?? ''}
                onChange={(e) =>
                  setLimits({ ...limits, [k]: e.target.value === '' ? null : Number(e.target.value) })
                }
                className="mt-0.5 w-full rounded border border-slate-200 px-2 py-1 text-sm"
              />
            </label>
          ))}
        </div>
      </div>

      <button
        disabled={!dirty || update.isPending}
        onClick={() => update.mutate({ key: plan.key, body: { feature_flags: flags, limits } })}
        className="flex items-center gap-1 rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-40"
      >
        <Save className="h-4 w-4" />
        {update.isPending ? 'Saving…' : 'Save'}
      </button>
    </div>
  )
}

export function Plans() {
  const { data, isLoading, error } = usePlans()
  if (isLoading) return <div className="text-sm text-slate-500">Loading…</div>
  if (error || !data) return <div className="text-sm text-red-600">Failed to load plans.</div>

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {data.map((p) => (
        <PlanCard key={p.key} plan={p} />
      ))}
    </div>
  )
}
