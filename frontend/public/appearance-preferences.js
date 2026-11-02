/* Shared by the pre-paint bootstrap and React. No remote fonts or arbitrary CSS.
   Writes the --pref-* RGB triplets that src/styles/tokens.css builds the accent
   tokens from, so a user accent always stays readable in both colour modes. */
(function () {
  const fonts = {
    inter: { label: 'Inter', family: "'Inter', Arial, sans-serif" },
    'open-sans': { label: 'Open Sans', family: "'Open Sans', Arial, sans-serif" },
    roboto: { label: 'Roboto', family: "'Roboto', Arial, sans-serif" },
    lato: { label: 'Lato', family: "'Lato', Arial, sans-serif" },
    system: { label: 'System font', family: 'system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif' },
  }
  const defaults = { evolution: '#2563eb', 'paper-lantern': '#2563eb' }
  const presets = [
    ['Blue', '#2563eb'], ['Navy', '#1e3a8a'], ['Teal', '#0f766e'], ['Violet', '#6d28d9'],
    ['Emerald', '#047857'], ['Amber', '#b45309'], ['Rose', '#be123c'], ['Slate', '#334155'],
  ]
  const validColor = value => typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)
  function normalize(raw) {
    raw = raw && typeof raw === 'object' ? raw : {}
    const colors = {}
    for (const skin of Object.keys(defaults)) if (validColor(raw.colors?.[skin])) colors[skin] = raw.colors[skin].toLowerCase()
    return { colors, font: Object.hasOwn(fonts, raw.font) ? raw.font : 'inter',
      textSize: raw.textSize === 'large' ? 'large' : 'default',
      density: ['compact', 'comfortable'].includes(raw.density) ? raw.density : 'default',
      motion: raw.motion === 'reduced' ? 'reduced' : 'system' }
  }
  const WHITE = [255, 255, 255], BLACK = [0, 0, 0]
  const rgb = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16))
  const mix = (a, b, ratio) => a.map((v, i) => Math.round(v * (1 - ratio) + b[i] * ratio))
  const luminance = c => c.map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4 }).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0)
  const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05)
  // Move a colour away from the background until it reaches the target ratio.
  function readable(color, background, minimum) {
    const target = luminance(background) > .4 ? BLACK : WHITE
    let result = color
    for (let n = 0; contrast(result, background) < minimum && n < 100; n++) result = mix(result, target, .06)
    return result
  }
  // Mirrors the surfaces in src/styles/tokens.css.
  const SURFACES = {
    light: { surface: [255, 255, 255], hardest: [236, 240, 246] },
    dark: { surface: [18, 26, 43], hardest: [38, 48, 70] },
  }
  function apply(raw, skin, dark) {
    const prefs = normalize(raw), root = document.documentElement
    const color = rgb(prefs.colors[skin] || defaults[skin] || defaults.evolution)
    const { surface, hardest } = SURFACES[dark ? 'dark' : 'light']
    // Filled controls: visible against the surface, with an ink that reaches AA.
    let solid = readable(color, surface, 3)
    let ink = contrast(solid, WHITE) >= contrast(solid, BLACK) ? WHITE : BLACK
    for (let n = 0; contrast(solid, ink) < 4.5 && n < 100; n++) solid = mix(solid, ink === WHITE ? BLACK : WHITE, .06)
    const link = readable(color, hardest, 4.6)
    const tokens = {
      accent: solid, ink, link,
      hover: mix(solid, ink === WHITE ? BLACK : WHITE, .12),
      soft: mix(surface, color, dark ? .16 : .08),
      softStrong: mix(surface, color, dark ? .28 : .18),
      // Retained for callers that read the readable-on-white shade.
      group: readable(color, WHITE, 4.6),
    }
    for (const [name, value] of Object.entries(tokens)) root.style.setProperty('--pref-' + name, value.join(' '))
    root.style.setProperty('--pref-font', fonts[prefs.font].family)
    root.dataset.appearance = 'customizable'
    root.dataset.textSize = prefs.textSize
    root.dataset.density = prefs.density
    root.dataset.motion = prefs.motion
    root.dataset.font = prefs.font
  }
  window.BoronAppearance = { fonts, defaults, presets, normalize, apply, contrast, rgb }
})()
