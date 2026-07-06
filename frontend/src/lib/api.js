import axios from 'axios'

// Client-router base path. The SPA mounts under /app during rollout so it never
// collides with the live FastAPI routes (GET /login, /ui/*, /api/*, /static/*).
export const BASE_PATH = '/app'

// The backend authenticates via a signed, httponly `fh_session` cookie (set by
// the form POST /login) — NOT a JWT. So every request must send credentials;
// there is no Authorization header for session auth. (Bearer API tokens exist
// too, but the interactive panel uses the cookie session.)
export const api = axios.create({
  baseURL: '',
  withCredentials: true,
  headers: { Accept: 'application/json' },
})

// --- global error handling ------------------------------------------------
// 401 -> session expired/absent, bounce to login. 503 -> backend in
// maintenance. Handlers are registered by the app (router) so we can do a
// client-side navigation instead of a full reload where possible.
let onUnauthorized = () => {
  window.location.assign(`${BASE_PATH}/login`)
}
let onMaintenance = () => {}

export function registerAuthHandlers({ unauthorized, maintenance }) {
  if (unauthorized) onUnauthorized = unauthorized
  if (maintenance) onMaintenance = maintenance
}

api.interceptors.response.use(
  (res) => res,
  (error) => {
    const status = error.response?.status
    if (status === 401) {
      onUnauthorized()
    } else if (status === 503) {
      onMaintenance()
    }
    return Promise.reject(normalizeError(error))
  },
)

// Turn an axios error into a plain {status, message, fields} object the UI can
// render. FastAPI validation errors arrive as {detail: [...]}; app errors as
// {detail: "message"} or {error: "message"}.
export function normalizeError(error) {
  const status = error.response?.status
  const data = error.response?.data
  let message = 'Something went wrong. Please try again.'
  let fields = null
  if (typeof data === 'string' && data) {
    message = data
  } else if (data?.detail) {
    if (Array.isArray(data.detail)) {
      fields = {}
      for (const d of data.detail) {
        const key = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : 'form'
        fields[key] = d.msg
      }
      message = data.detail.map((d) => d.msg).join('; ')
    } else {
      message = String(data.detail)
    }
  } else if (data?.error) {
    message = String(data.error)
  } else if (error.message && !error.response) {
    message = 'Cannot reach the server. Check your connection.'
  }
  const err = new Error(message)
  err.status = status
  err.fields = fields
  return err
}

// --- thin verb helpers ----------------------------------------------------
export const get = (url, config) => api.get(url, config).then((r) => r.data)
export const post = (url, body, config) => api.post(url, body, config).then((r) => r.data)
export const put = (url, body, config) => api.put(url, body, config).then((r) => r.data)
export const patch = (url, body, config) => api.patch(url, body, config).then((r) => r.data)
export const del = (url, config) => api.delete(url, config).then((r) => r.data)

// --- auth -----------------------------------------------------------------
// The login endpoints return JSON now (no server-rendered pages):
//   401/429           -> rejected / locked out ({detail})
//   {needs_2fa, ...}  -> a TOTP step is required
//   303 -> /app + Set-Cookie -> success; we then read identity from whoami().
export async function whoami() {
  return api.get('/api/v1/whoami').then((r) => r.data)
}

export async function login(username, password) {
  const form = new URLSearchParams({ username, password })
  const res = await api.post('/login', form, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    validateStatus: () => true,
  })

  if (res.status === 429 || res.status === 401) {
    throw Object.assign(new Error(res.data?.detail || 'Invalid username or password.'), { status: res.status })
  }
  if (res.data && res.data.needs_2fa) {
    return { needs2fa: true, pendingToken: res.data.pending_token }
  }
  // Success: cookie is set (via the 303 the browser followed). Resolve identity.
  const me = await whoami()
  return { role: me.role, username: me.username }
}

export async function loginVerify2fa(pendingToken, code) {
  const form = new URLSearchParams({ pending_token: pendingToken, code })
  const res = await api.post('/login/2fa', form, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    validateStatus: () => true,
  })
  if (res.status >= 400) {
    throw Object.assign(new Error(res.data?.detail || 'Invalid or expired code.'), { status: res.status })
  }
  const me = await whoami()
  return { role: me.role, username: me.username }
}

export async function logout() {
  try {
    await api.post('/logout', null, { validateStatus: () => true })
  } catch {
    /* ignore — clearing local state is what matters */
  }
}

// --- impersonation (Phase 8 feature 1) ------------------------------------
// Admin "Login as user": mint a single-use token, then immediately redeem it
// for a customer-scoped session cookie. The two calls are kept separate on the
// server (mint is authorized on the account path; redeem captures the admin's
// current session to restore) but the UI always chains them.
export async function impersonate(username) {
  const { token } = await post(`/api/v1/admin/accounts/${username}/impersonate`)
  return post('/api/v1/impersonate/redeem', { token })
}

export async function returnToAdmin() {
  return post('/api/v1/impersonate/return')
}

export async function changePassword(current_password, new_password, confirm_password) {
  const form = new URLSearchParams({ current_password, new_password, confirm_password })
  const res = await api.post('/change-password', form, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    validateStatus: () => true,
  })
  if (res.status >= 400) {
    throw Object.assign(new Error(res.data?.detail || 'Could not change password.'), { status: res.status })
  }
  return res.data
}
