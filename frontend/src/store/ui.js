import { create } from 'zustand'
import { persist } from 'zustand/middleware'

// UI chrome state: sidebar collapse + theme, both persisted to localStorage
// per the goal spec. Theme is applied to <html> by a subscriber in main.jsx.
export const useUI = create(
  persist(
    (set, get) => ({
      sidebarCollapsed: false,
      theme: 'light', // 'light' | 'dark'
      accountSwitcher: null, // admin: username currently being managed (for topbar switcher)
      paletteOpen: false, // command palette (Ctrl/Cmd+K) — not persisted
      mobileNavOpen: false,

      setPaletteOpen: (v) => set({ paletteOpen: v }),
      togglePalette: () => set({ paletteOpen: !get().paletteOpen }),
      setMobileNavOpen: (v) => set({ mobileNavOpen: v }),

      toggleSidebar: () => set({ sidebarCollapsed: !get().sidebarCollapsed }),
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),

      toggleTheme: () => {
        const next = get().theme === 'dark' ? 'light' : 'dark'
        set({ theme: next })
        applyTheme(next)
      },
      setTheme: (t) => {
        set({ theme: t })
        applyTheme(t)
      },

      setAccountSwitcher: (u) => set({ accountSwitcher: u }),
    }),
    {
      name: 'boron.ui',
      partialize: (s) => ({ sidebarCollapsed: s.sidebarCollapsed, theme: s.theme }),
    },
  ),
)

export function applyTheme(theme) {
  const root = document.documentElement
  if (theme === 'dark') root.classList.add('dark')
  else root.classList.remove('dark')
}
