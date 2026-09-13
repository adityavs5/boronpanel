// External script for CSP compatibility: apply preferences before first paint.
(function () {
  var state = {}
  try { state = JSON.parse(localStorage.getItem('boron.ui') || '{}').state || {} } catch (e) {}
  var root = document.documentElement
  var dark = state.theme === 'dark'
  root.dataset.skin = state.skin === 'paper-lantern' ? 'paper-lantern' : 'evolution'
  root.classList.toggle('dark', dark)
  root.style.colorScheme = dark ? 'dark' : 'light'
})()
