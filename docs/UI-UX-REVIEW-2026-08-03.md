# Panel UI/UX review

**Scope:** React frontend source review of navigation, workflows, shared components, responsiveness, and accessibility. A live visual capture was not completed because the browser/network setup prompts were cancelled; findings marked **confirmed** come directly from the implementation.

## Overall assessment

The panel has a solid visual and component foundation: a coherent card/table/dialog system, useful empty/loading/error states, dark mode, a command palette, contextual account views, and clear success/error feedback. The next gains are less about restyling and more about information architecture, mobile completeness, and making high-consequence workflows calmer and safer.

## Priority 0 — fix before promoting mobile use

### Mobile navigation is incomplete (**confirmed**)

The top-bar hamburger changes `sidebarCollapsed`, but the sidebar is rendered only from the `md` breakpoint upward. On a phone, pressing the hamburger has no visible result. At the same time, the administrator bottom navigation sends “More” to the customer-only `MoreMenu`, which lists hosting tools rather than administrator pages.

**Recommendation:** implement a real mobile drawer containing the role-specific sidebar. Replace the admin `/more` destination with an `AdminMoreMenu` that includes all admin destinations, grouped identically to desktop. Preserve the current route, highlight the active item, close on navigation/Escape, and trap focus while it is open.

### Primary mobile actions need a responsive audit (**confirmed**)

The top bar can contain menu, search, account switcher, theme toggle, and user menu simultaneously. Tables use horizontal scrolling with no column-priority strategy, while the fixed bottom navigation has labels at 10px and likely misses the 44px touch-target guideline.

**Recommendation:** hide secondary chrome on small screens behind the user or overflow menu; make account switching a searchable full-screen sheet; use 44px minimum targets; turn key table rows into compact cards below `sm`, retaining only identity, status, and a clear actions menu.

## Priority 1 — reduce cognitive load in the core product

### The administrator navigation is too broad

The desktop sidebar presents about 30 destinations. Its headings help, but the amount of scanning required makes infrequent operations difficult to find. The command palette is a good shortcut for experienced operators, not a substitute for a comprehensible hierarchy.

**Recommendation:** promote five to seven daily destinations (Accounts, Health, Services, Mail Queue, Security overview, Updates) and place the rest under expandable groups or an “Administration” index. Add a dedicated server overview with actionable alerts, recent failures, pending updates, and a short “needs attention” queue.

### Deep pages rely on long, horizontally-scrollable tab strips (**confirmed**)

Account detail has 11 tabs; Email has 8. The tabs simply scroll horizontally, without an overflow label, counter, or URL state. On small screens, important content is easy to miss, and browser Back/forward cannot restore a selected tab.

**Recommendation:** use primary tabs for 3–5 core views and put secondary items in a “More” menu; or use a left sub-navigation on desktop and a select/overflow sheet on mobile. Put the active subview in the URL (`?tab=email` or nested routes) so views are shareable and history works.

### High-risk actions deserve stronger interaction design

Account termination, database deletion, mail deletion, WAF changes, and bulk operations are available from otherwise dense screens. Existing confirmation dialogs are a good base, but the highest-risk actions should demand more deliberate confirmation.

**Recommendation:** use a danger panel that states impact, names dependent resources, offers a backup/export step when possible, and requires typing the account/domain name for irreversible actions. Keep the destructive action visually separated from routine actions.

## Priority 2 — workflow improvements

### Make the dashboard task-oriented

The customer dashboard has useful quota cards and quick links. It should also answer “what needs my attention?” before “what can I click?”

**Recommendation:** add an alerts strip for expiring SSL certificates, failed backups, quota thresholds, suspended mailboxes, DNS/SSL configuration gaps, and recent deployment/app failures. Each alert should link directly to resolution, have clear severity, and permit dismissal only when appropriate.

### Improve account creation and configuration forms

Many forms use short labels and modal dialogs, which are compact but make complex setup feel abrupt. Forms generally do not show a review step, meaningful examples, or inline validation until submission.

**Recommendation:** for account, domain, application, and mail setup: use progressive disclosure; validate as the user leaves a field; explain generated values and irreversible choices; retain entered values on failure; add a review/summary step for multi-resource provisioning.

### Scale list views beyond client-side tables

`DataTable` filters, sorts, and paginates already-loaded rows in the browser. This is fine for domains or databases, but audit logs, mail queues, accounts, error logs, and monitoring records will become slow and imprecise on larger installations.

**Recommendation:** add server-side filtering, sorting, cursor/page pagination, row counts, saved filters, and URL-persisted query state. Keep client-side tables for small per-account collections only.

### Improve account switching

The top-bar account switcher loads all accounts into a dropdown. It becomes unwieldy on large servers and lacks a visible search or recent-account list.

**Recommendation:** make it a searchable combobox with recent/pinned accounts and an “All accounts” escape route. On mobile, show it in a full-height sheet.

## Accessibility and interaction quality

| Finding | Evidence | Recommendation |
|---|---|---|
| Clickable table rows are mouse-only | `TR` adds `onClick` to a plain `<tr>` | Make row navigation an explicit link, or add keyboard semantics, focus, Enter/Space support, and a visible focus indicator. |
| Sort state is not exposed | Sortable headers have buttons but no `aria-sort` | Set `aria-sort` on the active column header and give the control an accessible sort label. |
| Some icon buttons rely on `title` | Multiple delete/edit/kill buttons have no `aria-label` | Require `aria-label` in the shared icon-button API; treat `title` as supplementary. |
| Label/error wiring is inconsistent | Many `FormField` uses omit `htmlFor`; `invalid` does not add ARIA attributes | Generate stable IDs, set `htmlFor`, `aria-invalid`, `aria-describedby`, and give errors `role="alert"`. |
| Login errors are passive text | Error paragraph has no live-region semantics | Use `role="alert"`, move focus to the error summary, and support recovery-code wording in the MFA step. |
| Secondary text may be too faint in dark mode | `--muted-fg` is gray-500 on gray-800/900 surfaces | Measure both themes against WCAG AA; raise muted text to a lighter token if needed. |
| Large dialogs may overflow on phones | Dialog content has no viewport max-height/scroll treatment | Apply `max-h-[calc(100dvh-2rem)]`, make body scrollable, and keep the footer visible. |
| Toasts overlap mobile controls | Toast viewport is fixed at bottom while bottom nav is fixed | Offset toast placement above the bottom nav and account for safe-area insets. |

## Interaction polish worth adding

- Use optimistic updates only for reversible, low-risk toggles; use persistent job status for background provisioning.
- Add “last updated” and refresh controls to auto-refreshing monitoring pages.
- Make copy actions visibly acknowledge success and avoid hiding credentials before the user has copied them.
- Add empty-state onboarding for first domain, first mailbox, first database, and first backup.
- Preserve filters, selected account, table settings, and open subview in the URL where it helps repeat work.
- Give overflowed tabs and tables subtle fade/scroll affordances so horizontal content is discoverable.

## Recommended implementation order

1. Repair mobile drawer and role-specific administrator “More” navigation.
2. Make destructive actions safer and make account/domain creation more guided.
3. Rework account and email tab overload; place state in URLs.
4. Improve responsive tables and account switching.
5. Address the accessibility table above through shared primitives, then audit pages incrementally.
6. Add a server/customer attention dashboard and server-side list querying.

## What is already working well

- Shared loading, empty, error, confirmation, toast, table, and form components establish a strong baseline.
- Page headers and contextual quick actions create a clear visual rhythm.
- The command palette, keyboard shortcut, theme support, and reduced-motion CSS are thoughtful quality touches.
- The customer dashboard's resource summary and quick actions are a good base for a task-oriented home screen.
