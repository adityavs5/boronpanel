import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import { currentThemeId, normalizeMode, themeInfo } from '../themes/registry'

// Applying another tab’s preferences must not persist them back and echo a storage event.
let syncingPreferences = false

// `theme` keeps its old storage key but is the personal light/dark choice. The
// panel theme itself is server-wide (themes/registry.js) and is not stored here.
export const useUI = create(
  persist(
    (set, get) => ({
      sidebarCollapsed: false,
      theme: 'light',
      appearance: window.BoronAppearance.normalize(),
      setAppearance: (patch) => {
        const appearance = window.BoronAppearance.normalize({ ...get().appearance, ...patch })
        set({ appearance })
        applyTheme(get().theme)
      },
      resetAppearance: () => {
        set({ appearance: window.BoronAppearance.normalize() })
        applyTheme(get().theme)
      },
      closedToolGroups: {},
      navigationGroups: {},
      domainContexts: {},
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
        applyTheme(theme)
      },
      toggleToolGroup: (key) => set({ closedToolGroups: { ...get().closedToolGroups, [key]: !get().closedToolGroups[key] } }),
      setNavigationGroup: (key, closed) => set({ navigationGroups: { ...get().navigationGroups, [key]: closed } }),
      setDomainContext: (username, domain) => {
        if (!username || !domain) return
        set({ domainContexts: { ...get().domainContexts, [username]: domain } })
      },
      setAccountSwitcher: (u) => set({ accountSwitcher: u }),
    }),
    {
      name: 'boron.ui',
      storage: createJSONStorage(() => ({
        getItem: (key) => { try { return localStorage.getItem(key) } catch { return null } },
        setItem: (key, value) => { try { if (!syncingPreferences) localStorage.setItem(key, value) } catch { /* Session-only preferences when storage is blocked. */ } },
        removeItem: (key) => { try { localStorage.removeItem(key) } catch { /* Storage may be unavailable. */ } },
      })),
      partialize: (s) => ({ appearance: s.appearance, sidebarCollapsed: s.sidebarCollapsed, theme: s.theme, closedToolGroups: s.closedToolGroups, navigationGroups: s.navigationGroups, domainContexts: s.domainContexts }),
      merge: (saved, current) => ({ ...current,
        appearance: window.BoronAppearance.normalize(saved?.appearance),
        sidebarCollapsed: Boolean(saved?.sidebarCollapsed),
        theme: normalizeMode(saved?.theme),
        closedToolGroups: saved?.closedToolGroups && typeof saved.closedToolGroups === 'object' && !Array.isArray(saved.closedToolGroups) ? saved.closedToolGroups : {},
        navigationGroups: saved?.navigationGroups && typeof saved.navigationGroups === 'object' && !Array.isArray(saved.navigationGroups) ? saved.navigationGroups : {},
        domainContexts: saved?.domainContexts && typeof saved.domainContexts === 'object' && !Array.isArray(saved.domainContexts) ? saved.domainContexts : {},
      }),
    },
  ),
)

export function applyTheme(mode) {
  const root = document.documentElement
  const id = currentThemeId()
  // Only a theme with a dark palette can follow the personal dark-mode choice.
  const dark = themeInfo(id).darkMode && normalizeMode(mode) === 'dark'
  root.classList.toggle('dark', dark)
  root.style.colorScheme = dark ? 'dark' : 'light'
  window.BoronAppearance.apply(useUI.getState().appearance, id, dark)
}

// Rehydrate only preferences, never auth, when another tab changes them.
export function syncUIPreferences(event) {
  if (event.key !== 'boron.ui' || event.storageArea !== localStorage) return
  try {
    // Storage events can be queued behind newer edits. Read the current value,
    // not a stale event payload, so an older colour cannot replace a newer one.
    const saved = JSON.parse(localStorage.getItem('boron.ui') || '{}').state || {}
    const theme = normalizeMode(saved.theme)
    const domainContexts = saved.domainContexts && typeof saved.domainContexts === 'object' && !Array.isArray(saved.domainContexts) ? saved.domainContexts : {}
    syncingPreferences = true
    useUI.setState({ theme, domainContexts, appearance: window.BoronAppearance.normalize(saved.appearance) })
    applyTheme(theme)
  } catch { /* Ignore invalid storage from extensions or old clients. */ }
  finally { syncingPreferences = false }
}
