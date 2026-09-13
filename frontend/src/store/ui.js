import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import { normalizeSkin, normalizeMode } from '../config/themes'

// Keep the existing light/dark key for backwards compatibility. A skin changes
// layout, while theme changes color mode. Neither touches session state.
export const useUI = create(
  persist(
    (set, get) => ({
      sidebarCollapsed: false,
      theme: 'light',
      skin: 'evolution',
      closedToolGroups: {},
      accountSwitcher: null,
      paletteOpen: false,
      mobileNavOpen: false,
      setPaletteOpen: (v) => set({ paletteOpen: v }),
      togglePalette: () => set({ paletteOpen: !get().paletteOpen }),
      setMobileNavOpen: (v) => set({ mobileNavOpen: v }),
      toggleSidebar: () => set({ sidebarCollapsed: !get().sidebarCollapsed }),
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
      toggleTheme: () => get().setTheme(get().theme === 'dark' ? 'light' : 'dark'),
      setTheme: (value) => {
        const theme = normalizeMode(value)
        set({ theme })
        applyTheme(theme, get().skin)
      },
      setSkin: (value) => {
        const skin = normalizeSkin(value)
        set({ skin })
        applyTheme(get().theme, skin)
      },
      toggleToolGroup: (key) => set({ closedToolGroups: { ...get().closedToolGroups, [key]: !get().closedToolGroups[key] } }),
      setAccountSwitcher: (u) => set({ accountSwitcher: u }),
    }),
    {
      name: 'boron.ui',
      storage: createJSONStorage(() => ({
        getItem: (key) => { try { return localStorage.getItem(key) } catch { return null } },
        setItem: (key, value) => { try { localStorage.setItem(key, value) } catch { /* Session-only preferences when storage is blocked. */ } },
        removeItem: (key) => { try { localStorage.removeItem(key) } catch { /* Storage may be unavailable. */ } },
      })),
      partialize: (s) => ({ sidebarCollapsed: s.sidebarCollapsed, theme: s.theme, skin: s.skin, closedToolGroups: s.closedToolGroups }),
      merge: (saved, current) => ({ ...current,
        sidebarCollapsed: Boolean(saved?.sidebarCollapsed),
        theme: normalizeMode(saved?.theme), skin: normalizeSkin(saved?.skin),
        closedToolGroups: saved?.closedToolGroups && typeof saved.closedToolGroups === 'object' && !Array.isArray(saved.closedToolGroups) ? saved.closedToolGroups : {},
      }),
    },
  ),
)

export function applyTheme(theme, skin = 'evolution') {
  const root = document.documentElement
  root.classList.toggle('dark', normalizeMode(theme) === 'dark')
  root.dataset.skin = normalizeSkin(skin)
  root.style.colorScheme = normalizeMode(theme)
}

// Rehydrate only preferences, never auth, when another tab changes its skin.
export function syncUIPreferences(event) {
  if (event.key !== 'boron.ui') return
  try {
    const saved = JSON.parse(event.newValue || '{}').state || {}
    const theme = normalizeMode(saved.theme)
    const skin = normalizeSkin(saved.skin)
    if (useUI.getState().theme !== theme || useUI.getState().skin !== skin) useUI.setState({ theme, skin })
    applyTheme(theme, skin)
  } catch { /* Ignore invalid storage from extensions or old clients. */ }
}
