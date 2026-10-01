// DNS owner names may be relative. Never hide the zone suffix from the editor.
export function dnsOwnerPreview(zone, input) {
  const origin = (zone || '').trim().toLowerCase().replace(/\.$/, '')
  const raw = (input || '').trim().toLowerCase()
  const name = raw.replace(/\.$/, '')
  if (!name || name === '@' || name === origin) return { fqdn: origin, confirmRelative: false }
  if (name.endsWith(`.${origin}`)) return { fqdn: name, confirmRelative: false }
  if (raw.endsWith('.')) return { fqdn: name, error: `This hostname is outside ${origin}. Enter a name within this DNS zone.`, confirmRelative: false }
  return { fqdn: `${name}.${origin}`, confirmRelative: name.includes('.') }
}
