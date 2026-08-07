import { Link } from 'react-router-dom'
import { Menu, ArrowRight, ExternalLink } from 'lucide-react'
import { PageHeader } from '@/components/ui/PageHeader'
import { useAuth } from '@/store/auth'
import { customerNav, adminNav } from '@/config/nav'

const CUSTOMER_PRIMARY = new Set(['/dashboard', '/domains', '/email', '/databases'])
const ADMIN_PRIMARY = new Set(['/accounts', '/health', '/services'])

function groupedItems(nav, excluded) {
  const groups = []
  let current = null
  nav.forEach((item) => {
    if (item.section) {
      current = { label: item.section, items: [] }
      groups.push(current)
    } else if (!excluded.has(item.to)) {
      if (!current) {
        current = { label: 'Tools', items: [] }
        groups.push(current)
      }
      current.items.push(item)
    }
  })
  return groups.filter((group) => group.items.length)
}

export default function MoreMenu() {
  const isAdmin = useAuth((s) => s.role === 'admin')
  const groups = groupedItems(isAdmin ? adminNav : customerNav, isAdmin ? ADMIN_PRIMARY : CUSTOMER_PRIMARY)

  return (
    <div>
      <PageHeader
        title={isAdmin ? 'Administration' : 'More'}
        description={isAdmin ? 'All server administration, security, logs, and integration tools.' : 'All tools and settings for your hosting account.'}
        icon={Menu}
      />
      <div className="space-y-7">
        {groups.map((group) => (
          <section key={group.label} aria-labelledby={`more-${group.label.replace(/\W+/g, '-').toLowerCase()}`}>
            <h2 id={`more-${group.label.replace(/\W+/g, '-').toLowerCase()}`} className="mb-3 text-sm font-semibold text-foreground">
              {group.label}
            </h2>
            <div className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-2 sm:grid-cols-3 lg:grid-cols-4">
              {group.items.map((item) => {
                const content = (
                  <>
                    <item.icon className="h-5 w-5 text-accent-600" />
                    <span className="flex items-center justify-between gap-2 text-sm font-medium text-foreground">
                      {item.label}
                      {item.external ? <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" /> : <ArrowRight className="h-3.5 w-3.5 text-muted-foreground transition-transform group-hover:translate-x-0.5" />}
                    </span>
                  </>
                )
                const classes = 'group flex min-h-24 flex-col justify-between gap-3 rounded-card border border-border bg-card p-4 transition-colors hover:border-accent hover:bg-accent-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:hover:bg-accent-950/40'
                return item.external ? (
                  <a key={item.to} href={item.to} target="_blank" rel="noopener noreferrer" className={classes}>{content}</a>
                ) : (
                  <Link key={item.to} to={item.to} className={classes}>{content}</Link>
                )
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}
