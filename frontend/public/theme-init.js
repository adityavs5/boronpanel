// External script for CSP compatibility: apply preferences before first paint.
// The server names the theme on <html>; the cached copy of the public branding
// covers a page served without it.
(function () {
  var state = {}
  try { state = JSON.parse(localStorage.getItem('boron.ui') || '{}').state || {} } catch (e) {}
  var root = document.documentElement
  var theme = root.dataset.theme
  if (!theme) { try { theme = JSON.parse(localStorage.getItem('boron.public-branding.v1') || '{}').theme } catch (e) {} }
  if (['classic', 'grid', 'modern'].indexOf(theme) < 0) theme = 'modern'
  root.dataset.theme = theme
  var dark = theme === 'modern' && state.theme === 'dark'
  root.classList.toggle('dark', dark)
  root.style.colorScheme = dark ? 'dark' : 'light'
  window.BoronAppearance?.apply(state.appearance, theme, dark)
})()
