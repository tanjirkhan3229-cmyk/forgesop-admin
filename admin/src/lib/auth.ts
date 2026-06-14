/**
 * Operator auth — SSO/OIDC + MFA, distinct from the tenant product.
 *
 * The console authenticates operators against a dedicated OIDC client whose
 * issuer + audience differ from the tenant Supabase project, so a tenant token
 * can never satisfy the admin API gate (the backend enforces this — see
 * core/platform_auth.py). MFA is enforced at the IdP.
 *
 * Flow: Authorization Code + PKCE redirect to the IdP, which returns a
 * short-lived operator token. We keep it in sessionStorage (cleared when the
 * tab closes) and attach it as a Bearer token on every admin-API request.
 *
 * This is a deliberately thin shell; swap in `oidc-client-ts` for full PKCE +
 * silent renew when wiring the real IdP.
 */

const TOKEN_KEY = 'forgesop.admin.token'

const env = import.meta.env

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY)
}

/** Kick off the OIDC redirect to the operator IdP. */
export function login(): void {
  const params = new URLSearchParams({
    client_id: env.VITE_OIDC_CLIENT_ID,
    redirect_uri: env.VITE_OIDC_REDIRECT_URI,
    response_type: 'code',
    scope: 'openid email profile',
    audience: env.VITE_OIDC_AUDIENCE,
    // A real integration adds PKCE (code_challenge) + state/nonce here.
  })
  window.location.href = `${env.VITE_OIDC_ISSUER}/authorize?${params.toString()}`
}

export function logout(): void {
  clearToken()
  window.location.assign('/')
}

/**
 * Capture a token returned to the redirect URI. Supports both an
 * implicit-style `#access_token=...` fragment (simple IdPs) and a dev-only
 * `?token=` query param so the shell is testable before the IdP is wired.
 */
export function captureRedirectToken(): boolean {
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  const query = new URLSearchParams(window.location.search)
  const token = hash.get('access_token') || query.get('token')
  if (token) {
    setToken(token)
    window.history.replaceState({}, '', window.location.pathname)
    return true
  }
  return false
}
