// Original SVG artwork for the Evo grid. Paper uses its own outline vocabulary.
// These are local assets, not extracted competitor icons or remote dependencies.
const colors = { blue: '#59bdec', deep: '#5273af', purple: '#7164a8', pale: '#d9f4ff', orange: '#ffb15f', teal: '#69c7cf' }
export function HostingIcon({ kind }) {
  const c = colors
  const [base, detail] = kind.split(':')
  const details = {
    settings: <><circle cx="38" cy="37" r="4"/><path d="M38 30v3m0 8v3m-7-7h3m8 0h3m-12-5 2 2m6 6 2 2m0-10-2 2m-6 6-2 2"/></>,
    dns: <><circle cx="38" cy="37" r="6"/><ellipse cx="38" cy="37" rx="2.5" ry="6"/><path d="M32 37h12"/></>,
    shield: <path d="m38 30 6 3v4c0 3-3 6-6 7-3-1-6-4-6-7v-4Zm-3 7 2 2 4-5"/>,
    transfer: <path d="M32 34h12l-3-3m3 9H32l3 3"/>,
    plan: <path d="M33 31h10v13H33Zm2 4h6m-6 3h6m-6 3h4"/>,
    reseller: <><circle cx="38" cy="33" r="3"/><path d="M32 44v-3a6 6 0 0 1 12 0v3"/></>,
    health: <path d="M30 38h4l2-6 4 11 2-5h4"/>,
  }
  const art = {
    globe: <><circle cx="24" cy="24" r="18" fill={c.blue}/><ellipse cx="24" cy="24" rx="8" ry="18" fill="none" stroke={c.pale} strokeWidth="1.5"/><path d="M7 18h34M7 30h34M6 24h36" stroke={c.pale} strokeWidth="1.5"/><rect x="3" y="19" width="42" height="11" rx="2" fill={c.orange}/><path d="m12 22 2 5 2-5 2 5 2-5m3 0 2 5 2-5 2 5 2-5" fill="none" stroke="#634125" strokeWidth="1.4"/></>,
    database: <><rect x="9" y="10" width="30" height="29" fill={c.deep}/><ellipse cx="24" cy="10" rx="15" ry="6" fill={c.blue}/><path d="M9 20c0 8 30 8 30 0M9 30c0 8 30 8 30 0" fill="none" stroke={c.pale} strokeWidth="2"/><ellipse cx="24" cy="39" rx="15" ry="6" fill={c.blue}/></>,
    mail: <><path d="m4 18 20-13 20 13v25H4Z" fill={c.orange}/><rect x="10" y="5" width="28" height="27" rx="2" fill={c.pale}/><path d="M15 12h18M15 18h14" stroke={c.blue} strokeWidth="2"/><path d="m4 18 20 16L44 18v25H4Z" fill="#ffcb88"/><path d="m4 43 15-13m25 13L29 30" stroke="#e5a157" strokeWidth="1.3"/></>,
    certificate: <><path d="m15 28-3 17 12-6 12 6-3-17" fill={c.orange}/><path d="m24 2 5 4 7 1 2 7 4 5-3 6-1 7-7 2-7 4-6-4-7-2-1-7-4-6 4-5 2-7 7-1Z" fill={c.blue}/><circle cx="24" cy="20" r="11" fill={c.pale}/><path d="m18 20 4 4 8-9" fill="none" stroke={c.deep} strokeWidth="3"/></>,
    folder: <><path d="M4 9h15l5 6h20v27H4Z" fill={c.orange}/><path d="M4 18h40v24H4Z" fill="#ffcd89"/><path d="M12 26h23M12 32h17" stroke="#fff8e9" strokeWidth="2.5"/><rect x="31" y="31" width="14" height="14" rx="3" fill={c.blue}/><path d="m38 34-4 4h8m-4-4v8" stroke="white" strokeWidth="1.5"/></>,
    window: <><rect x="5" y="5" width="38" height="35" rx="2" fill={c.pale}/><path d="M5 5h38v9H5Z" fill={c.purple}/><path d="M10 9h3m4 0h3m4 0h3" stroke={c.blue} strokeWidth="2"/><path d="m16 22-5 5 5 5m16-10 5 5-5 5m-6-12-4 17" fill="none" stroke={c.blue} strokeWidth="2.5"/></>,
    users: <><circle cx="24" cy="12" r="8" fill={c.blue}/><path d="M11 41v-8a13 13 0 0 1 26 0v8Z" fill={c.blue}/><circle cx="8" cy="18" r="5" fill={c.teal}/><path d="M1 37v-9a7 7 0 0 1 11-5l-4 14Z" fill={c.teal}/><circle cx="40" cy="18" r="5" fill={c.teal}/><path d="M47 37v-9a7 7 0 0 0-11-5l4 14Z" fill={c.teal}/></>,
    server: <><rect x="9" y="3" width="29" height="12" rx="2" fill={c.purple}/><rect x="9" y="18" width="29" height="12" rx="2" fill={c.deep}/><rect x="9" y="33" width="29" height="12" rx="2" fill={c.purple}/><path d="M14 9h3m-3 15h3m-3 15h3" stroke={c.blue} strokeWidth="2.5"/><circle cx="32" cy="9" r="2" fill={c.teal}/><circle cx="32" cy="24" r="2" fill={c.orange}/><circle cx="32" cy="39" r="2" fill={c.teal}/></>,
  }
  return art[base] ? <svg viewBox="0 0 48 48" className="hosting-artwork" aria-hidden="true">{art[base]}{details[detail] && <><circle cx="38" cy="37" r="10" fill={c.deep}/><g fill="none" stroke="white" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">{details[detail]}</g></>}</svg> : null
}
export const hostingArtwork = {
  '/domains':'globe', '/subdomains':'window:dns', '/dns':'globe:settings', '/cloudflare':'globe:shield',
  '/databases':'database', '/db-monitor':'database:health', '/slow-queries':'database:plan',
  '/ssl':'certificate', '/security':'certificate', '/website-security':'certificate',
  '/email':'mail', '/email/settings':'mail:settings', '/email/dns':'mail:dns', '/email/spam':'mail:shield', '/email/migration':'mail:transfer', '/mail-queue':'mail:plan',
  '/ftp':'folder', '/files':'folder', '/backups':'folder', '/backup-jobs':'folder',
  '/php':'window', '/terminal':'window', '/node-apps':'window', '/python-apps':'window',
  '/accounts':'users', '/resellers':'users:reseller', '/plans':'users:plan', '/health':'server:health', '/services':'server:settings', '/openlitespeed':'server:transfer',
}
