export const SKINS = [
  { id: 'evolution', name: 'Evo', layout: 'Icon grid', description: 'A clear, spacious workspace with tool tiles and an at-a-glance resource overview.' },
  { id: 'paper-lantern', name: 'Paper', layout: 'Compact list', description: 'The familiar hosting workspace: grouped tools, compact navigation, and account details always close by.' },
]
export const normalizeSkin = (value) => SKINS.some((skin) => skin.id === value) ? value : 'evolution'
export const normalizeMode = (value) => value === 'dark' ? 'dark' : 'light'
