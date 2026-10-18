/* Shared by the pre-paint bootstrap and React. No remote fonts or arbitrary CSS. */
(function () {
  const fonts = {
    'open-sans': { label: 'Open Sans', family: "'Open Sans', Arial, sans-serif" },
    inter: { label: 'Inter', family: "'Inter', Arial, sans-serif" },
    roboto: { label: 'Roboto', family: "'Roboto', Arial, sans-serif" },
    lato: { label: 'Lato', family: "'Lato', Arial, sans-serif" },
    system: { label: 'System font', family: 'system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif' },
  }
  const defaults = { evolution: '#bc942c', 'paper-lantern': '#307bb8' }
  const validColor = value => typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)
  function normalize(raw) {
    raw = raw && typeof raw === 'object' ? raw : {}
    const colors = {}
    for (const skin of Object.keys(defaults)) if (validColor(raw.colors?.[skin])) colors[skin] = raw.colors[skin].toLowerCase()
    return { colors, font: Object.hasOwn(fonts, raw.font) ? raw.font : 'open-sans',
      textSize: raw.textSize === 'large' ? 'large' : 'default',
      density: ['compact', 'comfortable'].includes(raw.density) ? raw.density : 'default',
      motion: raw.motion === 'reduced' ? 'reduced' : 'system' }
  }
  const rgb = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16))
  const mix = (a, b, ratio) => a.map((v, i) => Math.round(v * (1 - ratio) + b[i] * ratio))
  const luminance = c => c.map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4 }).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0)
  const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05)
  function readable(color, background, minimum) {
    const target = luminance(background) > .4 ? [0, 0, 0] : [255, 255, 255]
    let result = color
    for (let n = 0; contrast(result, background) < minimum && n < 100; n++) result = mix(result, target, .06)
    return result
  }
  function apply(raw, skin, dark) {
    const prefs = normalize(raw), root = document.documentElement
    const color = rgb(prefs.colors[skin] || defaults[skin] || defaults.evolution)
    const surface = dark ? [30, 38, 49] : [255, 255, 255]
    const solid = readable(color, surface, 3)
    const ink = contrast(solid, [0, 0, 0]) >= contrast(solid, [255, 255, 255]) ? [0, 0, 0] : [255, 255, 255]
    const tokens = {
      // Category headers deliberately retain white lettering. Derive a dark
      // enough brand shade; links must contrast with raised AND soft surfaces.
      accent: solid, ink, group: readable(color, [255, 255, 255], 4.6),
      link: readable(color, dark ? [37, 47, 60] : [244, 246, 249], 4.7),
      hover: mix(solid, ink[0] === 0 ? [255, 255, 255] : [0, 0, 0], .10),
      soft: mix(surface, color, .10), nav: mix(color, [18, 23, 30], .94),
      navHover: mix(mix(color, [18, 23, 30], .94), [255, 255, 255], .08),
    }
    for (const [name, value] of Object.entries(tokens)) root.style.setProperty('--pref-' + name, value.join(' '))
    root.style.setProperty('--pref-font', fonts[prefs.font].family)
    root.dataset.appearance = 'customizable'
    root.dataset.textSize = prefs.textSize
    root.dataset.density = prefs.density
    root.dataset.motion = prefs.motion
    root.dataset.font = prefs.font
  }
  window.BoronAppearance = { fonts, defaults, normalize, apply, contrast, rgb }
})()
