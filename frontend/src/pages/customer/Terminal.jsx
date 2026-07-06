import { useEffect, useRef, useState } from 'react'
import { TerminalSquare, RefreshCw } from 'lucide-react'
import { Terminal as Xterm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { useAccountUsername } from '@/hooks/useAccount'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'

// Phase 8 feature 7: xterm.js <-> WebSocket <-> SSH (as the account user).
// The session cookie authenticates the WebSocket (same-origin); the server
// injects/removes an ephemeral SSH key for the life of this socket.
export default function Terminal() {
  const username = useAccountUsername()
  const containerRef = useRef(null)
  const [status, setStatus] = useState('connecting') // connecting | open | closed
  const [nonce, setNonce] = useState(0) // bump to reconnect

  useEffect(() => {
    if (!username || !containerRef.current) return
    const term = new Xterm({
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      fontSize: 13,
      theme: { background: '#0b1120', foreground: '#e5e7eb', cursor: '#1FBED6' },
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(containerRef.current)
    fit.fit()

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/accounts/${username}/terminal`)

    const sendResize = () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ t: 'r', c: term.cols, r: term.rows }))
      }
    }

    ws.onopen = () => {
      setStatus('open')
      term.focus()
      sendResize()
    }
    ws.onmessage = (ev) => term.write(ev.data)
    ws.onclose = (ev) => {
      setStatus('closed')
      if (ev.code === 4401) term.write('\r\n\x1b[31m[not authenticated — please sign in again]\x1b[0m\r\n')
      else if (ev.code === 4403) term.write('\r\n\x1b[31m[not authorized for this account]\x1b[0m\r\n')
      else term.write('\r\n\x1b[33m[connection closed]\x1b[0m\r\n')
    }
    ws.onerror = () => term.write('\r\n\x1b[31m[connection error]\x1b[0m\r\n')

    const dataSub = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ t: 'i', d: data }))
    })

    const onResize = () => { fit.fit(); sendResize() }
    const ro = new ResizeObserver(onResize)
    ro.observe(containerRef.current)
    window.addEventListener('resize', onResize)

    return () => {
      window.removeEventListener('resize', onResize)
      ro.disconnect()
      dataSub.dispose()
      try { ws.close() } catch { /* ignore */ }
      term.dispose()
    }
  }, [username, nonce])

  const statusLabel = { connecting: 'Connecting…', open: 'Connected', closed: 'Disconnected' }[status]
  const statusColor = { connecting: 'text-warning', open: 'text-success', closed: 'text-muted-foreground' }[status]

  return (
    <div>
      <PageHeader title="Terminal" description="A shell on your account, running as your own user (never root)." icon={TerminalSquare}>
        <span className={`text-sm font-medium ${statusColor}`}>{statusLabel}</span>
        {status === 'closed' && (
          <Button variant="secondary" onClick={() => { setStatus('connecting'); setNonce((n) => n + 1) }}>
            <RefreshCw className="h-4 w-4" /> Reconnect
          </Button>
        )}
      </PageHeader>
      <Card className="overflow-hidden p-2" style={{ background: '#0b1120' }}>
        <div ref={containerRef} className="h-[70vh] w-full" />
      </Card>
      <p className="mt-2 text-xs text-muted-foreground">
        Sessions time out after 30 minutes idle · max 3 concurrent · the SSH key is created for this session and removed when you leave.
      </p>
    </div>
  )
}
