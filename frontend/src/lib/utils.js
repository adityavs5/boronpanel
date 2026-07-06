// Shared formatters/helpers for the UI layer.

export function formatBytes(bytes, decimals = 1) {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return '—'
  if (bytes === 0) return '0 B'
  const k = 1024
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  const i = Math.min(Math.floor(Math.log(Math.abs(bytes)) / Math.log(k)), units.length - 1)
  const val = bytes / Math.pow(k, i)
  return `${val.toFixed(i === 0 ? 0 : decimals)} ${units[i]}`
}

export function formatMB(mb) {
  if (mb === null || mb === undefined) return '—'
  return formatBytes(mb * 1024 * 1024)
}

export function formatNumber(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return new Intl.NumberFormat().format(n)
}

export function formatDate(iso, opts) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  return d.toLocaleString(undefined, opts || { dateStyle: 'medium', timeStyle: 'short' })
}

export function formatDateShort(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  return d.toLocaleDateString(undefined, { dateStyle: 'medium' })
}

export function relativeTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  const diff = (d.getTime() - Date.now()) / 1000
  const abs = Math.abs(diff)
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  const steps = [
    ['year', 31536000],
    ['month', 2592000],
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
    ['second', 1],
  ]
  for (const [unit, secs] of steps) {
    if (abs >= secs || unit === 'second') {
      return rtf.format(Math.round(diff / secs), unit)
    }
  }
  return ''
}

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const s = Math.floor(seconds)
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m`
  return `${s}s`
}

export function percent(used, total) {
  if (!total) return 0
  return Math.min(100, Math.round((used / total) * 100))
}

export function pluralize(n, singular, plural) {
  return `${formatNumber(n)} ${n === 1 ? singular : plural || singular + 's'}`
}

export function titleCase(str) {
  if (!str) return ''
  return String(str)
    .replace(/[_-]/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export function truncate(str, n = 60) {
  if (!str) return ''
  return str.length > n ? str.slice(0, n - 1) + '…' : str
}

// Copy text to clipboard, returning a promise that resolves to success bool.
export async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
