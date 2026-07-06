import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import * as apiAuth from '@/lib/api'

// Identity is derived at login time from the redirect URL (see lib/api.login)
// and persisted so a page refresh keeps the user in place. The httponly session
// cookie is the real source of truth — if it has expired, the first API call
// 401s and the interceptor bounces to /login, where we clear this store.
export const useAuth = create(
  persist(
    (set, get) => ({
      role: null, // 'admin' | 'customer' | null
      username: null, // customer's own account username; admins have their login name
      pending2fa: null, // { pendingToken } while awaiting a TOTP code
      impersonating: false, // Phase 8 f1: admin is acting as a customer
      impersonator: null, // the admin's username while impersonating

      isAuthenticated: () => !!get().role,
      isAdmin: () => get().role === 'admin',

      // Reconcile local identity with the server's session (source of truth).
      // Called on app boot so a hard refresh restores the correct role and the
      // impersonation banner, and after impersonate/return which swap the cookie.
      async syncIdentity() {
        try {
          const me = await apiAuth.whoami()
          set({
            role: me.role,
            username: me.username,
            impersonating: !!me.impersonating,
            impersonator: me.impersonator || null,
          })
          return me
        } catch {
          return null
        }
      },

      async returnToAdmin() {
        try {
          await apiAuth.returnToAdmin()
        } finally {
          // Full reload so the restored admin cookie is picked up cleanly.
          window.location.assign(`${apiAuth.BASE_PATH}`)
        }
      },

      async login(username, password) {
        const result = await apiAuth.login(username, password)
        if (result.needs2fa) {
          set({ pending2fa: { pendingToken: result.pendingToken } })
          return { needs2fa: true }
        }
        set({ role: result.role, username: result.username, pending2fa: null })
        return { needs2fa: false, role: result.role }
      },

      async verify2fa(code) {
        const { pending2fa } = get()
        if (!pending2fa) throw new Error('No pending 2FA session.')
        const result = await apiAuth.loginVerify2fa(pending2fa.pendingToken, code)
        set({ role: result.role, username: result.username, pending2fa: null })
        return { role: result.role }
      },

      async logout() {
        await apiAuth.logout()
        set({ role: null, username: null, pending2fa: null })
      },

      // Clear local identity without an API round-trip (used by the 401 handler).
      clear() {
        set({ role: null, username: null, pending2fa: null })
      },
    }),
    {
      name: 'forgehost.auth',
      partialize: (s) => ({ role: s.role, username: s.username }),
    },
  ),
)
