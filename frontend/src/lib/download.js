import { humanErrorMessage } from './api'

// Starts a browser download of a file the API streams (Content-Disposition:
// attachment) without holding it in memory, and still reports a refusal: the
// URL loads in a hidden same-origin frame. A file download never loads a
// document there; a JSON error ({detail}) does, so its text is read back and
// passed to onError. The frame is removed once the error is read, or after
// `keepMs` when the download started (it may take minutes to prepare).
export function startDownload(url, { onError, keepMs = 10 * 60 * 1000 } = {}) {
  const frame = document.createElement('iframe')
  frame.hidden = true
  frame.setAttribute('aria-hidden', 'true')
  frame.title = 'Download'
  const remove = () => frame.remove()
  const timer = window.setTimeout(remove, keepMs)
  frame.addEventListener('load', () => {
    let text = ''
    try { text = frame.contentDocument?.body?.innerText || '' } catch { return }
    if (!text.trim()) return
    let message = text.trim()
    try {
      const data = JSON.parse(message)
      message = typeof data?.detail === 'string' ? data.detail : data?.error || message
    } catch { /* not JSON: show the text as sent */ }
    window.clearTimeout(timer)
    remove()
    onError?.(new Error(humanErrorMessage(message)))
  })
  frame.src = url
  document.body.appendChild(frame)
}
