import { useState } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useSignups } from '../hooks/queries'
import { formatNumber } from '../lib/format'

const RANGES = ['24h', '7d', '30d', '90d']

export function Signups() {
  const [range, setRange] = useState('30d')
  const signups = useSignups(range)

  return (
    <div className="space-y-4">
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

      {signups.data && (
        <>
          <div className="flex gap-6 text-sm text-slate-600">
            <span>
              New users:{' '}
              <strong className="text-slate-900">{formatNumber(signups.data.totals.users)}</strong>
            </span>
            <span>
              New workspaces:{' '}
              <strong className="text-slate-900">
                {formatNumber(signups.data.totals.workspaces)}
              </strong>
            </span>
          </div>
          <div className="h-80 rounded-xl border border-slate-200 bg-white p-4">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={signups.data.series}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={24} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} width={28} />
                <Tooltip />
                <Line type="monotone" dataKey="users" name="Users" stroke="#4f46e5" dot={false} />
                <Line
                  type="monotone"
                  dataKey="workspaces"
                  name="Workspaces"
                  stroke="#94a3b8"
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  )
}
