import { useState } from 'react'
import { ArrowLeft, Search } from 'lucide-react'
import { ConfirmModal } from '../components/ConfirmModal'
import { usePatchWorkspace, usePlans, useWorkspace, useWorkspaces } from '../hooks/queries'
import { formatDate, formatNumber, relativeTime } from '../lib/format'

type PendingAction =
  | { kind: 'plan'; plan_key: string }
  | { kind: 'flag'; flag: string; value: boolean }

function WorkspaceDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const { data, isLoading, error } = useWorkspace(id)
  const plans = usePlans()
  const patch = usePatchWorkspace(id)
  const [pending, setPending] = useState<PendingAction | null>(null)

  if (isLoading) return <div className="text-sm text-slate-500">Loading…</div>
  if (error || !data) return <div className="text-sm text-red-600">Failed to load workspace.</div>

  const flagEntries = Object.entries(data.feature_flags)

  function confirmPending() {
    if (!pending) return
    const body =
      pending.kind === 'plan'
        ? { plan_key: pending.plan_key }
        : { flags: { [pending.flag]: pending.value } }
    patch.mutate(body, { onSuccess: () => setPending(null) })
  }

  return (
    <div className="space-y-6">
      <button onClick={onBack} className="flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900">
        <ArrowLeft className="h-4 w-4" /> Back to workspaces
      </button>

      <div className="flex items-center gap-3">
        <h2 className="text-xl font-semibold">{data.name}</h2>
        {data.is_suspended && (
          <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
            Suspended
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
        <div>
          <div className="text-slate-500">Plan</div>
          <select
            value={data.plan ?? ''}
            onChange={(e) => setPending({ kind: 'plan', plan_key: e.target.value })}
            className="mt-0.5 rounded border border-slate-200 px-2 py-1 text-sm font-medium"
          >
            <option value="" disabled>
              {data.plan ?? '— none —'}
            </option>
            {plans.data?.map((p) => (
              <option key={p.key} value={p.key}>
                {p.name ?? p.key}
              </option>
            ))}
          </select>
        </div>
        <div><div className="text-slate-500">Members</div><div className="font-medium">{formatNumber(data.member_count)}</div></div>
        <div><div className="text-slate-500">Created</div><div className="font-medium">{formatDate(data.created_at)}</div></div>
        <div><div className="text-slate-500">Slug</div><div className="font-medium">{data.slug ?? '—'}</div></div>
      </div>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-700">
          Feature flags <span className="font-normal text-slate-400">(toggle = one-off override)</span>
        </h3>
        <div className="space-y-1.5">
          {flagEntries.map(([k, v]) => (
            <div key={k} className="flex items-center justify-between rounded-lg border border-slate-100 px-3 py-1.5">
              <span className="font-mono text-xs text-slate-700">{k}</span>
              <button
                onClick={() => setPending({ kind: 'flag', flag: k, value: !v })}
                className={
                  'relative h-5 w-9 rounded-full transition ' + (v ? 'bg-brand-600' : 'bg-slate-300')
                }
              >
                <span className={'absolute top-0.5 h-4 w-4 rounded-full bg-white transition ' + (v ? 'left-4' : 'left-0.5')} />
              </button>
            </div>
          ))}
        </div>
      </section>

      {pending && (
        <ConfirmModal
          title={pending.kind === 'plan' ? 'Change plan' : 'Override feature flag'}
          body={
            pending.kind === 'plan' ? (
              <>
                Apply plan <strong>{pending.plan_key}</strong> to <strong>{data.name}</strong>? This
                reconciles the workspace's feature flags and is audited.
              </>
            ) : (
              <>
                {pending.value ? 'Enable' : 'Disable'} <strong>{pending.flag}</strong> for{' '}
                <strong>{data.name}</strong> as a one-off override (plan unchanged)?
              </>
            )
          }
          confirmLabel={pending.kind === 'plan' ? 'Apply plan' : 'Save override'}
          busy={patch.isPending}
          onConfirm={confirmPending}
          onCancel={() => setPending(null)}
        />
      )}

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-700">Members</h3>
        <Table
          headers={['Email', 'Name', 'Role', 'Status', 'Last active']}
          rows={data.members.map((m) => [
            m.email, m.name ?? '—', m.role ?? '—', m.status ?? '—', relativeTime(m.last_active_at),
          ])}
        />
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-700">Recent activity</h3>
        <Table
          headers={['When', 'Event', 'Action', 'Actor']}
          rows={data.recent_activity.map((a) => [
            relativeTime(a.timestamp), a.event_type ?? '—', a.action ?? '—', a.actor_email ?? '—',
          ])}
        />
      </section>
    </div>
  )
}

function Table({ headers, rows }: { headers: string[]; rows: (string | number)[][] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
          <tr>{headers.map((h) => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td colSpan={headers.length} className="px-4 py-6 text-center text-slate-400">No rows.</td></tr>
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

export function Workspaces() {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const { data, isLoading } = useWorkspaces({ search, page, page_size: 25 })

  if (selected) return <WorkspaceDetail id={selected} onBack={() => setSelected(null)} />

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
          <input
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1) }}
            placeholder="Search workspaces…"
            className="w-full rounded-lg border border-slate-200 py-2 pl-8 pr-3 text-sm"
          />
        </div>
        {data && <span className="text-sm text-slate-500">{formatNumber(data.total)} total</span>}
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2 font-medium">Name</th>
              <th className="px-4 py-2 font-medium">Members</th>
              <th className="px-4 py-2 font-medium">Plan</th>
              <th className="px-4 py-2 font-medium">Created</th>
              <th className="px-4 py-2 font-medium">Last activity</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={5} className="px-4 py-6 text-center text-slate-400">Loading…</td></tr>
            ) : (
              data?.items.map((w) => (
                <tr
                  key={w.id}
                  onClick={() => setSelected(w.id)}
                  className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                >
                  <td className="px-4 py-2 font-medium text-slate-900">
                    {w.name}
                    {w.is_suspended && <span className="ml-2 text-xs text-red-600">suspended</span>}
                  </td>
                  <td className="px-4 py-2 text-slate-700">{formatNumber(w.member_count)}</td>
                  <td className="px-4 py-2 text-slate-700">{w.plan ?? '—'}</td>
                  <td className="px-4 py-2 text-slate-700">{formatDate(w.created_at)}</td>
                  <td className="px-4 py-2 text-slate-700">{relativeTime(w.last_activity)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
