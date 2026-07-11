// Applies the persisted dark-mode class before first paint. Lives in its own
// file (not inline in index.html) because the panel's CSP is script-src 'self'
// — inline scripts are blocked.
try {
  var s = JSON.parse(localStorage.getItem('boron.ui') || '{}')
  if (s.state && s.state.theme === 'dark') document.documentElement.classList.add('dark')
} catch (e) {}
