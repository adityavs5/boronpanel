import { useId } from 'react'
import { ShieldCheck } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { InlineWarning } from './InlineFeedback'

// OpenLiteSpeed reads only rewrite rules from .htaccess. The panel turns the
// Apache access directives it understands (Require, Order/Allow/Deny) into
// server rules and reports the rest; this renders that report, from
// GET .../htaccess-status or the reload-htaccess response.

function describeRule(rule) {
  const allow = rule.allow || [], deny = rule.deny || []
  if (rule.mode === 'deny') return 'Blocked for every visitor'
  if (rule.mode === 'allow') return 'Open to every visitor'
  if (deny.includes('*')) return allow.length ? `Only ${allow.join(', ')} can visit` : 'Blocked for every visitor'
  if (allow.includes('*')) return deny.length ? `Blocked for ${deny.join(', ')}` : 'Open to every visitor'
  return `Allowed: ${allow.join(', ') || 'none'}. Blocked: ${deny.join(', ') || 'none'}`
}

// True when the report has anything the customer should look at.
export function htaccessNeedsAttention(report) {
  return !!report && ((report.unsupported_access_directives || []).length > 0 || report.access_scan_complete === false || !!report.access_scan_reused_previous)
}

export function HtaccessReport({ report }) {
  const headingId = useId()
  const rules = report?.access_rules || []
  // Line 0 is not a line of the file: it is a note about the whole
  // .htaccess (a <Files>/<Limit> grant, an access restriction that cannot be
  // limited to some files), so it is listed apart from the ignored lines.
  const all = report?.unsupported_access_directives || []
  const unsupported = all.filter(item => item.line)
  const notes = all.filter(item => !item.line)
  return (
    <div className="space-y-4">
      <section aria-labelledby={headingId} className="space-y-2">
        <h3 id={headingId} className="text-sm font-semibold text-foreground">Protected folders</h3>
        {rules.length ? (
          <ul className="divide-y divide-border rounded-btn border border-border">
            {rules.map(rule => (
              <li key={rule.directory} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
                <code className="min-w-0 break-all font-mono text-xs text-foreground">{rule.directory}</code>
                <Badge variant="success"><ShieldCheck className="h-3 w-3" aria-hidden="true" /> Protected by the server</Badge>
                <span className="min-w-0 basis-full break-words text-muted-foreground sm:basis-auto">{describeRule(rule)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No folder of this website restricts visitors through .htaccess, so no access rules are applied.</p>
        )}
      </section>
      {unsupported.length > 0 && (
        <InlineWarning title="Directives the server cannot apply">
          <p>OpenLiteSpeed ignores these lines, so they do not protect anything. Use a password-protected directory or the IP blocking settings instead.</p>
          <ul className="space-y-1">
            {unsupported.map((item, index) => (
              <li key={`${item.path}:${item.line}:${index}`} className="break-words">
                <code className="break-all font-mono text-xs">{item.path}{item.line ? `, line ${item.line}` : ''}</code>: <code className="break-all font-mono text-xs">{item.directive}</code>
              </li>
            ))}
          </ul>
        </InlineWarning>
      )}
      {notes.length > 0 && (
        <InlineWarning title="Access rules the server enforces differently">
          <ul className="space-y-1">
            {notes.map((item, index) => (
              <li key={`${item.path}:${index}`} className="break-words">
                <code className="break-all font-mono text-xs">{item.path}</code>: {item.directive}
              </li>
            ))}
          </ul>
        </InlineWarning>
      )}
      {/* access_scan_reused_previous: folders this check did not re-read
          keep the rules of the last complete check. */}
      {report?.access_scan_reused_previous ? (
        <InlineWarning title="The check did not re-read every folder">
          <p>This site has more folders than the server re-reads at once; rules found by the last full check stay enforced for folders that were not re-read.</p>
        </InlineWarning>
      ) : report?.access_scan_complete === false && (
        <InlineWarning title="The check did not reach every folder">
          <p>This website has too many folders or .htaccess files to check at once, so rules in folders it did not reach may be missing from this list and may not be applied.</p>
        </InlineWarning>
      )}
    </div>
  )
}
