/* Shared by the pre-paint bootstrap and React. No remote fonts or arbitrary CSS.
   Writes the --pref-* RGB triplets that src/styles/tokens.css builds the accent
   tokens from, so a theme's accent stays readable in both colour modes. */
(function () {
  // Each theme sets its own accent and typeface; the server administrator
  // chooses the theme. People keep only their accessibility preferences.
  const themes = {
    classic: { accent: '#3989cf', font: "'Open Sans', Arial, sans-serif" },
    grid: { accent: '#0c7bb0', font: "'Montserrat', Arial, sans-serif" },
    modern: { accent: '#4f46e5', font: "'Inter', Arial, sans-serif" },
  }
  function normalize(raw) {
    raw = raw && typeof raw === 'object' ? raw : {}
    return { textSize: raw.textSize === 'large' ? 'large' : 'default',
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
  function apply(raw, theme, dark) {
    const prefs = normalize(raw), root = document.documentElement
    const chosen = themes[theme] || themes.modern
    const color = rgb(chosen.accent)
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
    root.style.setProperty('--pref-font', chosen.font)
    root.dataset.textSize = prefs.textSize
    root.dataset.density = prefs.density
    root.dataset.motion = prefs.motion
  }
  window.BoronAppearance = { themes, normalize, apply, contrast, rgb }
})()
