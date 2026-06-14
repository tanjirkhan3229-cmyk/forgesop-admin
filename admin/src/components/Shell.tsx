import { useState, type ComponentType } from 'react'
import { Building2, CreditCard, Gauge, LineChart, LogOut, Users as UsersIcon } from 'lucide-react'
import type { Me } from '../lib/api'
import { logout } from '../lib/auth'
import { Overview } from '../pages/Overview'
import { Signups } from '../pages/Signups'
import { Workspaces } from '../pages/Workspaces'
import { Users } from '../pages/Users'
import { Plans } from '../pages/Plans'

type PageKey = 'overview' | 'signups' | 'workspaces' | 'users' | 'plans'

const NAV: { key: PageKey; label: string; icon: typeof Gauge }[] = [
  { key: 'overview', label: 'Overview', icon: Gauge },
  { key: 'signups', label: 'Signups', icon: LineChart },
  { key: 'workspaces', label: 'Workspaces', icon: Building2 },
  { key: 'users', label: 'Users', icon: UsersIcon },
  { key: 'plans', label: 'Plans', icon: CreditCard },
]

const PAGES: Record<PageKey, ComponentType> = {
  overview: Overview,
  signups: Signups,
  workspaces: Workspaces,
  users: Users,
  plans: Plans,
}

/** Authed layout — sidebar nav + header + the active read-only page. */
export function Shell({ me }: { me: Me }) {
  const [page, setPage] = useState<PageKey>('overview')
  const Page = PAGES[page]

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 flex-col border-r border-slate-200 bg-white">
        <div className="px-4 py-4 font-semibold text-brand-700">ForgeSOP Admin</div>
        <nav className="flex-1 space-y-1 px-2">
          {NAV.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setPage(key)}
              className={
                'flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium ' +
                (page === key ? 'bg-brand-600 text-white' : 'text-slate-600 hover:bg-slate-100')
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          ))}
        </nav>
      </aside>

      <div className="flex flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
          <span className="text-sm font-medium capitalize text-slate-700">{page}</span>
          <div className="flex items-center gap-4">
            <div className="text-right">
              <div className="text-sm font-medium">{me.email}</div>
              <div className="text-xs text-slate-500">{me.role}</div>
            </div>
            <button
              onClick={logout}
              className="flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
            >
              <LogOut className="h-4 w-4" />
              Sign out
            </button>
          </div>
        </header>
        <main className="flex-1 p-6">
          <Page />
        </main>
      </div>
    </div>
  )
}
