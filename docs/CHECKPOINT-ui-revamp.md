# Checkpoint — UI revamp (React SPA control panel), 2026-07-06

Full React frontend replacing the Jinja2 admin UI, per the "Forgehost UI
Revamp" goal. Cloudways-inspired. Deployed to production and verified live.

## Stack & layout
React 18 + Vite 5 + Tailwind 3 + React Query 5 + React Router 6 + Recharts 2
+ Zustand 4 + Axios + Radix primitives + lucide-react. Source: `frontend/`.
Built to `static/dist/`, served by FastAPI at `/app` (SPA catch-all in
`api/main.py`; assets via the existing `/static` mount). Node installed via
apt (18.19).

Design tokens in `frontend/tailwind.config.js` + `frontend/src/index.css`
(CSS-variable surfaces for light/dark; fixed brand/semantic colors). Sidebar
`#111827`, content `#F9FAFB`, accent `#1FBED6`, Inter, 8px cards / 6px buttons.

## What was built
- **Design system** (`src/components/ui/`, 16 modules): Button (CVA variants),
  Card, DataTable (client sort/filter/paginate + built-in loading/empty/error),
  Dialog + ConfirmDialog, Toast (global `toast.*`), Badge, StatusBadge,
  Skeleton, EmptyState/ErrorState, Tabs, DropdownMenu, Select, Switch/Checkbox,
  Progress/UsageBar, Tooltip, PageHeader, Input/Textarea/Label/FormField.
- **Shell** (`src/components/layout/`): Sidebar (collapse 240↔64px persisted to
  localStorage, role-aware nav, live health mini-widget), Topbar (breadcrumb,
  admin account-switcher, notifications, theme toggle, user menu),
  MobileBottomNav (<768px), AppShell. Login (split-screen, inline 2FA),
  ProtectedRoute, maintenance/404.
- **31 pages** (`src/pages/customer/*`, `src/pages/admin/*`): see STATUS.md.
  Customer resource components are reused inside the admin tabbed AccountDetail
  via `useAccountUsername()` (URL `:username` for admin, own account for
  customers).
- **API layer** (`src/lib/`): Axios `withCredentials` client, 401→login /
  503→maintenance interceptors, React Query (30s stale, invalidate-on-mutation),
  Zustand auth + ui stores.

## Backend touch-points (serving/auth only — no API-endpoint behavior changed)
- `api/main.py`: `/app` SPA catch-all; path-aware CSP (`script-src 'self'` for
  `/app`, legacy `script-src 'none'` elsewhere); `GET /` → `/app`.
- `api/routers/auth.py`: added `GET /api/v1/whoami`; `GET /login` → `/app/login`;
  login / 2FA / change-password / logout now return JSON (not templates);
  login success redirects to `/app`.
- `scripts/deploy.sh`: excludes `frontend/node_modules`.

## Delivery approach
Foundation (design system, shell, auth, API layer, serving pipeline) built and
verified first; the 25 CRUD pages fanned out via a `Workflow` (25 agents, each
given `PAGE_GUIDE.md` + `API_CONTRACT.md` + its legacy template, writing one
file); the 4 complex pages (DomainDetail, Files, ServerHealth, AccountDetail)
built directly / by focused agents. Full `npm run build`: 2539 modules, clean.

## Verification (live, puppeteer against prod :9443, real data)
Login (JSON+whoami) → `/app/accounts`; `whoami` returns identity; pages render
real data (84 accounts, live health gauges + 24h Recharts, UFW rules);
create/mutations wired; change-password works; **mobile 375px** (sidebar→bottom
nav, tables scroll); **dark mode** (CPU gauge reddens at the danger threshold).
Screenshots: `docs/ui-screenshots/`. Disposable admin used for capture was
deleted afterward.

## Jinja removal — complete
A second workflow (8 agents) ported the remaining utilities to the SPA:
`Security` (2FA), `ApiTokens`, `Logs`, `DiskUsage` (new pages), plus
in-place additions — admin usage-limits (AccountDetail), namespace bulk-enable
(Accounts), backup-browse (Backups), nameservers + WordPress installer
(DomainDetail). Then all 58 `templates_ui/*.html` were deleted and every
`ui_router` registration removed from `api/main.py` (only JSON `api_router`s
remain; `/ui/*` → 404). 1178 backend tests still pass. `whoami` +
JSON login/2FA/change-password/logout keep auth template-free.

## Known future work (not blocking)
The JS bundle is a single ~1.14MB (323KB gz) chunk — route-level code-splitting
(React.lazy per page) is a straightforward future optimization. The unregistered
`ui_router` objects remain in the router modules as dead code (could be pruned).
