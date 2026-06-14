import { useState } from 'react'
import { Search } from 'lucide-react'
import { useUsers } from '../hooks/queries'
import { formatNumber, relativeTime } from '../lib/format'

export function Users() {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const { data, isLoading } = useUsers({ search, status: status || undefined, page, page_size: 25 })

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
          <input
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1) }}
            placeholder="Search by email or name…"
            className="w-full rounded-lg border border-slate-200 py-2 pl-8 pr-3 text-sm"
          />
        </div>
        <select
          value={status}
          onChange={(e) => { setStatus(e.target.value); setPage(1) }}
          className="rounded-lg border border-slate-200 py-2 px-3 text-sm"
        >
          <option value="">All statuses</option>
          <option value="ACTIVE">Active</option>
          <option value="PENDING">Pending</option>
          <option value="DEACTIVATED">Deactivated</option>
          <option value="SUSPENDED">Suspended</option>
        </select>
        {data && <span className="text-sm text-slate-500">{formatNumber(data.total)} total</span>}
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2 font-medium">Email</th>
              <th className="px-4 py-2 font-medium">Name</th>
              <th className="px-4 py-2 font-medium">Workspace</th>
              <th className="px-4 py-2 font-medium">Role</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Last active</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={6} className="px-4 py-6 text-center text-slate-400">Loading…</td></tr>
            ) : (
              data?.items.map((u) => (
                <tr key={u.id} className="border-t border-slate-100">
                  <td className="px-4 py-2 font-medium text-slate-900">{u.email}</td>
                  <td className="px-4 py-2 text-slate-700">{u.name ?? '—'}</td>
                  <td className="px-4 py-2 text-slate-700">{u.workspace_name ?? '—'}</td>
                  <td className="px-4 py-2 text-slate-700">{u.role ?? '—'}</td>
                  <td className="px-4 py-2 text-slate-700">{u.status ?? '—'}</td>
                  <td className="px-4 py-2 text-slate-700">{relativeTime(u.last_active_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
