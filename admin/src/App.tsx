import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Shell } from './components/Shell'
import { Login } from './components/Login'
import { AuthError, api } from './lib/api'
import { captureRedirectToken, getToken } from './lib/auth'

export default function App() {
  // Capture an operator token returned by the IdP redirect before first render.
  const [hasToken, setHasToken] = useState(() => {
    const captured = captureRedirectToken()
    return captured || !!getToken()
  })

  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: api.me,
    enabled: hasToken,
    retry: false,
  })

  // A 403 means the token is not (or no longer) a valid operator — drop to login.
  useEffect(() => {
    if (meQuery.error instanceof AuthError) setHasToken(false)
  }, [meQuery.error])

  if (!hasToken) return <Login />
  if (meQuery.isLoading) {
    return <div className="p-6 text-sm text-slate-500">Loading operator session…</div>
  }
  if (meQuery.error || !meQuery.data) return <Login />

  return <Shell me={meQuery.data} />
}
