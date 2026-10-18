import { useEffect, useMemo, useRef, useState } from 'react'
import { Monitor, Smartphone } from 'lucide-react'
import { Button } from './Button'

// Preview documents are deliberately inert: no scripts, same-origin access,
// forms, redirects, child frames or external requests. The saved HTML is never
// changed by this preview-only rendering. An inert <template> avoids fetching
// resources while inspecting untrusted draft markup in the parent document.
export function templatePreviewDocument(content, { replacements = {}, heading, message, fromColor, toColor } = {}) {
  const source = document.createElement('template')
  source.innerHTML = String(content || '').replace(/<(\/?)(html|body)(?=[\s>])/gi, '<$1boron-preview-$2').replace(/\{\{\s*([a-zA-Z_]+)\s*\}\}/g, (match, key) => replacements[key] === undefined ? match : String(replacements[key]).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]))
  source.content.querySelectorAll('script, iframe, frame, frameset, object, embed, base, meta, link').forEach(node => node.remove())
  source.content.querySelectorAll('*').forEach(node => {
    for (const attribute of [...node.attributes]) {
      if (/^on/i.test(attribute.name) || ['href', 'xlink:href', 'action', 'formaction', 'srcdoc', 'srcset', 'ping', 'autofocus'].includes(attribute.name)) node.removeAttribute(attribute.name)
      if (attribute.name === 'src' && !(node.tagName === 'IMG' && /^data:image\/(?:png|gif|jpe?g|webp);base64,/i.test(attribute.value))) node.removeAttribute(attribute.name)
    }
  })
  if (heading !== undefined) { const title = source.content.querySelector('h1'); if (title) title.textContent = heading }
  if (message !== undefined) { const paragraph = source.content.querySelector('main p:not(.help)'); if (paragraph) paragraph.textContent = message }
  // The fragment parser normally drops html/body attributes. Inert placeholder
  // elements preserve their styling without ever constructing a live document.
  const attributes = tag => {
    const wrapper = source.content.querySelector(`boron-preview-${tag}`)
    if (!wrapper) return ''
    const values = ['class', 'style', 'lang', 'dir'].filter(name => wrapper.hasAttribute(name)).map(name => ` ${name}="${wrapper.getAttribute(name).replace(/[&<>"]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[character])}"`).join('')
    wrapper.replaceWith(...wrapper.childNodes)
    return values
  }
  const htmlAttributes = attributes('html')
  const bodyAttributes = attributes('body')
  let html = source.innerHTML
  if (/^#[0-9a-f]{6}$/i.test(fromColor || '') && /^#[0-9a-f]{6}$/i.test(toColor || '')) html = html.replace(new RegExp(fromColor, 'gi'), toColor)
  return `<!doctype html><html${htmlAttributes || ' lang="en"'}><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"><title>Template preview</title></head><body${bodyAttributes}>${html}</body></html>`
}

export function TemplatePreview({ content, title = 'Page preview', thumbnail = false, options }) {
  const [device, setDevice] = useState('desktop')
  const [availableWidth, setAvailableWidth] = useState(0)
  const container = useRef(null)
  const viewportWidth = device === 'mobile' ? 375 : 1024
  const viewportHeight = device === 'mobile' ? 680 : 640
  const scale = Math.min(1, availableWidth / viewportWidth)
  const preview = useMemo(() => templatePreviewDocument(content, options), [content, options])
  useEffect(() => {
    const observer = new ResizeObserver(entries => setAvailableWidth(entries[0].contentRect.width))
    if (container.current) observer.observe(container.current)
    return () => observer.disconnect()
  }, [])
  return <div className={`template-preview ${thumbnail ? 'template-preview--thumbnail' : ''}`}>
    {!thumbnail && <div className="template-preview__toolbar"><strong>{title}</strong><div role="group" aria-label={`${title} viewport`}><Button variant="ghost" size="sm" aria-pressed={device === 'desktop'} onClick={() => setDevice('desktop')}><Monitor className="h-4 w-4" />Desktop</Button><Button variant="ghost" size="sm" aria-pressed={device === 'mobile'} onClick={() => setDevice('mobile')}><Smartphone className="h-4 w-4" />Mobile</Button></div></div>}
    <div ref={container} className="template-preview__canvas"><div className="template-preview__viewport" style={{ width: viewportWidth * scale, height: viewportHeight * scale }}><iframe title={title} srcDoc={preview} sandbox="" referrerPolicy="no-referrer" tabIndex={thumbnail ? -1 : 0} aria-hidden={thumbnail || undefined} style={{ width: viewportWidth, height: viewportHeight, transform: `scale(${scale})` }} /></div></div>
    {!thumbnail && <p className="template-preview__note">Live draft preview · scripts, links and external content are disabled.</p>}
  </div>
}
