import { useState } from 'react'
import { LogIn, ShieldCheck } from 'lucide-react'
import { api } from '../lib/api'
import { login as ssoLogin, setToken } from '../lib/auth'

const LOCAL_AUTH = import.meta.env.VITE_LOCAL_AUTH === 'true'

type Mode = 'login' | 'setpw' | 'setpw-done'

/**
 * Operator sign-in. With VITE_LOCAL_AUTH the console handles email+password
 * itself: first login (no password yet) routes to a set-password screen, then
 * the operator signs in with email+password. Otherwise it kicks off SSO/OIDC.
 */
export function Login({ onAuthenticated }: { onAuthenticated?: () => void }) {
  if (!LOCAL_AUTH) return <SsoLogin />
  return <PasswordLogin onAuthenticated={onAuthenticated} />
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="mb-6 flex items-center gap-2 text-brand-700">
          <ShieldCheck className="h-6 w-6" />
          <span className="text-lg font-semibold">ForgeSOP Platform Admin</span>
        </div>
        {children}
      </div>
    </div>
  )
}

function SsoLogin() {
  return (
    <Card>
      <p className="mb-6 text-sm text-slate-600">
        Operators only. Sign in with your ForgeSOP staff identity (SSO + MFA). This console is
        unreachable by tenants.
      </p>
      <button
        onClick={ssoLogin}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 py-2.5 font-medium text-white transition hover:bg-brand-700"
      >
        <LogIn className="h-4 w-4" />
        Sign in with SSO
      </button>
    </Card>
  )
}

function PasswordLogin({ onAuthenticated }: { onAuthenticated?: () => void }) {
  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submitLogin(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const res = await api.authLogin(email.trim(), password)
      if (res.status === 'password_set_required') {
        setPassword('')
        setMode('setpw')
      } else if (res.status === 'ok' && res.token) {
        setToken(res.token)
        onAuthenticated?.()
      }
    } catch {
      setError('Invalid email or password.')
    } finally {
      setBusy(false)
    }
  }

  async function submitSetPassword(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (password.length < 12) return setError('Password must be at least 12 characters.')
    if (password !== confirm) return setError('Passwords do not match.')
    setBusy(true)
    try {
      await api.authSetPassword(email.trim(), password)
      setPassword('')
      setConfirm('')
      setMode('setpw-done')
    } catch {
      setError('Could not set password. It may already be set — try signing in.')
    } finally {
      setBusy(false)
    }
  }

  const input =
    'w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none'
  const primaryBtn =
    'flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 py-2.5 font-medium text-white transition hover:bg-brand-700 disabled:opacity-40'

  if (mode === 'setpw-done') {
    return (
      <Card>
        <p className="mb-6 text-sm text-slate-600">
          Password set. Please sign in with your email and new password.
        </p>
        <button className={primaryBtn} onClick={() => setMode('login')}>
          Go to sign in
        </button>
      </Card>
    )
  }

  if (mode === 'setpw') {
    return (
      <Card>
        <p className="mb-1 text-sm font-medium text-slate-700">Set your password</p>
        <p className="mb-5 text-xs text-slate-500">
          First sign-in for <span className="font-medium">{email}</span>. Choose a password (min 12
          chars). You'll then sign in with it.
        </p>
        <form onSubmit={submitSetPassword} className="space-y-3">
          <input
            type="password" autoComplete="new-password" placeholder="New password"
            value={password} onChange={(e) => setPassword(e.target.value)} className={input} required
          />
          <input
            type="password" autoComplete="new-password" placeholder="Confirm password"
            value={confirm} onChange={(e) => setConfirm(e.target.value)} className={input} required
          />
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button type="submit" disabled={busy} className={primaryBtn}>
            {busy ? 'Saving…' : 'Set password'}
          </button>
        </form>
      </Card>
    )
  }

  return (
    <Card>
      <p className="mb-5 text-sm text-slate-600">Operators only. Sign in with your email.</p>
      <form onSubmit={submitLogin} className="space-y-3">
        <input
          type="email" autoComplete="username" placeholder="you@forgesop.com"
          value={email} onChange={(e) => setEmail(e.target.value)} className={input} required
        />
        <input
          type="password" autoComplete="current-password" placeholder="Password"
          value={password} onChange={(e) => setPassword(e.target.value)} className={input}
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button type="submit" disabled={busy} className={primaryBtn}>
          <LogIn className="h-4 w-4" />
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </Card>
  )
}
