// The panel themes. The server administrator chooses one for the whole server
// (Panel theme page); nobody else can. Each theme is a separate build chunk with
// its own layout, home screen, icon set and stylesheet, and the browser loads
// only the active one. Keep these ids in sync with shared/panel_themes.py.
export const THEMES = [
  {
    id: 'classic', name: 'Classic', layout: 'Dark side menu, tool lists',
    description: 'The familiar hosting layout: a dark side menu, tools listed in groups, and account details down the right. Two-tone icons.',
    darkMode: false,
  },
  {
    id: 'grid', name: 'Grid', layout: 'Blue header, large icon tiles',
    description: 'Tool groups under blue title bars with bright tile icons, and a Menu button that reaches every tool from any page. No side menu.',
    darkMode: false,
  },
  {
    id: 'modern', name: 'Modern', layout: 'Light side menu, cards',
    description: 'A calm, spacious workspace with a clean side menu, status at a glance and a dark mode. Line icons.',
    darkMode: true,
  },
]
export const DEFAULT_THEME = 'modern'
export const normalizeTheme = (value) => THEMES.some((theme) => theme.id === value) ? value : DEFAULT_THEME
export const normalizeMode = (value) => value === 'dark' ? 'dark' : 'light'
export const themeInfo = (id) => THEMES.find((theme) => theme.id === id) || THEMES.find((theme) => theme.id === DEFAULT_THEME)
// theme-init.js has already settled the id before any module runs.
export const currentThemeId = () => normalizeTheme(document.documentElement.dataset.theme)

const loaders = {
  classic: () => import('./classic'),
  grid: () => import('./grid'),
  modern: () => import('./modern'),
}
export const loadTheme = (id) => loaders[normalizeTheme(id)]().then((module) => module.default)
