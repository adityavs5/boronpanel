// Serve the production build with the same /app and /static/dist mounts as
// FastAPI. Browser tests intercept API calls; an unmocked call fails loudly.
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
const root = path.resolve('../static/dist')
const types = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2', '.ico': 'image/x-icon' }
http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost')
  const relative = url.pathname.startsWith('/app') ? 'index.html' : decodeURIComponent(url.pathname.replace(/^\/static\/dist\//, ''))
  const file = path.resolve(root, relative)
  if (!file.startsWith(root + '/') || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end('Not found'); return }
  res.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream')
  res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; worker-src 'self' blob:")
  fs.createReadStream(file).pipe(res)
}).listen(4173, '127.0.0.1')
