import { adminNav, customerNav, resellerNav } from '@/config/nav'

const exactOverrides = {
  '/appearance': 'Appearance',
  '/accounts/new': 'Create hosting account',
  '/plans/new': 'Create plan',
  '/more': 'More tools',
}

export function routeTitle(pathname, search = '', role = 'customer') {
  if (pathname === '/email' && new URLSearchParams(search).get('webmail') === '1') return 'Webmail'
  if (exactOverrides[pathname]) return exactOverrides[pathname]
  if (/^\/plans\/[^/]+\/edit$/.test(pathname)) return 'Edit plan'
  if (/^\/accounts\/[^/]+\/domains\/[^/]+$/.test(pathname)) return 'Domain'
  if (/^\/accounts\/[^/]+\/php$/.test(pathname)) return 'PHP Settings'
  if (/^\/accounts\/[^/]+\/dns$/.test(pathname)) return 'DNS Management'
  if (/^\/accounts\/[^/]+\/ssl$/.test(pathname)) return 'SSL Certificates'
  if (/^\/accounts\/[^/]+\/redirects$/.test(pathname)) return 'Redirects'
  if (/^\/accounts\/[^/]+$/.test(pathname)) return 'Account'
  if (/^\/domains\/[^/]+$/.test(pathname)) return 'Domain'
  const nav = role === 'admin' ? adminNav : role === 'reseller' ? resellerNav : customerNav
  return nav.find(item => item.to?.split('?')[0] === pathname)?.label || 'Boron Panel'
}
