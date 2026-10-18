/* Adapt only the trusted FileBrowser interface. Never inspect file contents,
   attach event handlers to file actions, or change permissions/selection. */
(() => {
  const viewport = document.querySelector('meta[name="viewport"]')
  if (viewport) viewport.setAttribute('content', 'width=device-width, initial-scale=1')
  function repair() {
    const size = document.getElementById('gallery-size')
    if (size && !size.hasAttribute('aria-label') && !size.hasAttribute('aria-labelledby')) {
      size.setAttribute('aria-label', 'File preview size')
    }
    // Leave upstream selection semantics intact. Its styling and delayed
    // keyboard scrolling both query aria-selected; rows can also contain
    // a focusable download action, so remapping them to options is unsafe.
  }
  let scheduled = false
  const observer = new MutationObserver(() => {
    if (scheduled) return
    scheduled = true
    requestAnimationFrame(() => { scheduled = false; repair() })
  })
  observer.observe(document.body, { childList: true, subtree: true })
  repair()
})()
