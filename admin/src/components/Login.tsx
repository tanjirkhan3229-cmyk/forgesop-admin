import { LogIn, ShieldCheck } from 'lucide-react'
import { login } from '../lib/auth'

export function Login() {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="mb-6 flex items-center gap-2 text-brand-700">
          <ShieldCheck className="h-6 w-6" />
          <span className="text-lg font-semibold">ForgeSOP Platform Admin</span>
        </div>
        <p className="mb-6 text-sm text-slate-600">
          Operators only. Sign in with your ForgeSOP staff identity (SSO + MFA).
          This console is unreachable by tenants.
        </p>
        <button
          onClick={login}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 py-2.5 font-medium text-white transition hover:bg-brand-700"
        >
          <LogIn className="h-4 w-4" />
          Sign in with SSO
        </button>
      </div>
    </div>
  )
}
