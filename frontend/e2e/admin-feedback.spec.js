import { test, expect } from '@playwright/test'

// Round 2 administrator flows: shared error handling (404/409 details, busy
// lock retry), sign-in refusals, task failure reasons, partial resource
// limits, plan propagation, reseller, token and backup job deletion.

const BUSY = 'A DNS backup, restore or management operation is in progress. Try again shortly.'
const REFERENCE = '6adc329b626448e9'

// A handler returns plain data (200 JSON) or reply(status, json) for anything else.
const reply = (status, json) => ({ fulfill: { status, json } })

async function session(page, { skin = 'modern', theme = 'light', role = 'admin', handle } = {}) {
  const state = { requests: [], errors: [] }
  page.on('pageerror', error => state.errors.push(error.message))
  await page.addInitScript(({ skin, theme, role }) => {
    localStorage.setItem('boron.public-branding.v1',JSON.stringify({panel_name:'Boron',theme:skin}));localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
    if (role) localStorage.setItem('boron.auth', JSON.stringify({ state: { role, username: 'admin' }, version: 0 }))
  }, { skin, theme, role })
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method()
    let body = null
    try { body = method === 'GET' ? null : request.postDataJSON() } catch { body = request.postData() }
    const entry = { method, path, search: url.search, body, at: Date.now() }
    state.requests.push(entry)
    const answer = await handle?.(entry)
    if (answer?.fulfill) return route.fulfill(answer.fulfill)
    if (answer !== undefined) return route.fulfill({ json: answer })
    let data = {}
    if (path.endsWith('/whoami')) data = { role: role || 'admin', username: 'admin' }
    else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
    else if (path.endsWith('/onboarding')) data = { completed: true }
    return route.fulfill({ json: data })
  })
  return state
}

const writes = (state, path) => state.requests.filter(item => item.method !== 'GET' && (!path || item.path === path))
const toasts = page => page.getByRole('region', { name: 'Notifications (F8)' })
const noHorizontalScroll = page => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)

function accountFixture(overrides = {}) {
  return {
    id: 1, username: 'alpha', status: 'active', primary_domain: 'example.com', php_version: '8.3', plan_id: 1,
    cpu_pct: null, cpu_cores: null, configured_cpu_cores: null, mem_mb: 1024, io_mb: null, pids_max: 100,
    limits_source: 'plan', host_cpu_cores: 8, host_memory_gb: 16, quota_hard_mb: 10240, ...overrides,
  }
}
const starter = { id: 1, name: 'Starter', cpu_pct: 100, cpu_cores: 1, mem_mb: 1024, io_mb: 50, pids_max: 100, quota_soft_mb: 10240, quota_hard_mb: 12288, inode_soft_limit: 450000, inode_hard_limit: 500000 }

// --- shared error handling ------------------------------------------------

test('a busy-lock 409 is retried once after a pause and then succeeds', async ({ page }) => {
  const account = accountFixture({ limits_source: 'account', plan_id: null, cpu_pct: 200, cpu_cores: 2, configured_cpu_cores: 2, io_mb: 50 })
  let attempts = 0
  const state = await session(page, { handle: ({ path, method, body }) => {
    if (path === '/api/v1/accounts/alpha' && method === 'GET') return account
    if (path === '/api/v1/accounts/alpha/limits') {
      attempts += 1
      if (attempts === 1) return reply(409, { detail: BUSY })
      Object.assign(account, body)
      return account
    }
  } })
  await page.goto('/app/accounts/alpha?tab=settings')
  await page.getByLabel('Max processes', { exact: true }).fill('150')
  await page.getByRole('button', { name: 'Update limits', exact: true }).click()
  await expect(toasts(page).getByText('Limits updated', { exact: true })).toBeVisible()
  const sent = writes(state, '/api/v1/accounts/alpha/limits')
  expect(sent).toHaveLength(2)
  expect(sent[1].at - sent[0].at).toBeGreaterThanOrEqual(2500)
  expect(sent.map(item => item.body)).toEqual([{ pids_max: 150 }, { pids_max: 150 }])
  await expect(toasts(page).getByText(BUSY)).toHaveCount(0)
  expect(state.errors).toEqual([])
})

test('a lock that stays busy, a 409 conflict and a 404 all show the server detail', async ({ page }) => {
  const account = accountFixture({ limits_source: 'account', plan_id: null, cpu_pct: 200, cpu_cores: 2, configured_cpu_cores: 2, io_mb: 50 })
  let limitsReply = reply(409, { detail: BUSY })
  const state = await session(page, { handle: ({ path, method }) => {
    if (path === '/api/v1/accounts/alpha' && method === 'GET') return account
    if (path === '/api/v1/accounts/ghost') return reply(404, { detail: "account 'ghost' not found" })
    if (path === '/api/v1/accounts/alpha/limits') return limitsReply
    if (path === '/api/v1/accounts/alpha/suspend') return reply(409, { detail: "cannot suspend an account in status 'terminating'" })
  } })
  await page.goto('/app/accounts/alpha?tab=settings')
  await page.getByLabel('Max processes', { exact: true }).fill('150')
  await page.getByRole('button', { name: 'Update limits', exact: true }).click()
  await expect(toasts(page).getByText(BUSY)).toBeVisible({ timeout: 15_000 })
  expect(writes(state, '/api/v1/accounts/alpha/limits')).toHaveLength(2)

  // An ordinary conflict is not a lock: no retry, its own reason.
  limitsReply = reply(409, { detail: "cannot change limits for an account in status 'terminating'" })
  await page.getByRole('button', { name: 'Update limits', exact: true }).click()
  await expect(toasts(page).getByText("cannot change limits for an account in status 'terminating'")).toBeVisible()
  expect(writes(state, '/api/v1/accounts/alpha/limits')).toHaveLength(3)

  // A refusal inside a confirmation stays in the dialog, readable while it is open.
  await page.getByRole('region', { name: 'Unsaved changes' }).getByRole('button', { name: 'Discard', exact: true }).click()
  await page.getByRole('button', { name: 'Suspend', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Suspend alpha?' })
  await dialog.getByLabel('Type alpha to confirm', { exact: true }).fill('alpha')
  await dialog.getByRole('button', { name: 'Suspend account', exact: true }).click()
  await expect(dialog.getByRole('alert')).toHaveText("cannot suspend an account in status 'terminating'")
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page.getByRole('button', { name: 'Suspend', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Suspend alpha?' }).getByRole('alert')).toHaveCount(0)
  await page.keyboard.press('Escape')

  await page.goto('/app/accounts/ghost')
  await expect(page.getByRole('alert').filter({ hasText: "account 'ghost' not found" })).toBeVisible()
  await expect(page.getByText(/could not complete this operation/)).toHaveCount(0)
  expect(state.errors).toEqual([])
})

// --- sign-in ----------------------------------------------------------------

// The form posts go to /login and /login/2fa, outside /api; `respond` gives
// reply(...) or raw fulfil options (for headers such as Retry-After).
async function signInPage(page, respond) {
  const calls = []
  await session(page, { role: null, handle: ({ path }) => path.endsWith('/whoami') ? reply(401, { detail: 'authentication required' }) : undefined })
  await page.route(url => url.pathname === '/login' || url.pathname === '/login/2fa', async route => {
    if (route.request().method() !== 'POST') return route.continue()
    const path = new URL(route.request().url()).pathname
    calls.push(path)
    const answer = respond(path, calls)
    return route.fulfill(answer.fulfill || answer)
  })
  await page.goto('/app/login')
  return calls
}

async function signIn(page, username = 'alpha') {
  await page.getByLabel('Username', { exact: true }).fill(username)
  await page.getByLabel('Password', { exact: true }).fill('Correct-horse-1')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
}

test('a rate-limited sign-in counts down from Retry-After and keeps the button disabled', async ({ page }) => {
  let retryAfter = '90'
  const calls = await signInPage(page, () => ({ status: 429, headers: { 'Retry-After': retryAfter, 'Content-Type': 'application/json' }, body: JSON.stringify({ detail: 'Too many requests.', reference: REFERENCE }) }))
  await signIn(page)
  await expect(page.getByRole('alert')).toHaveText('Too many sign-in attempts. Try again in 2 minutes.')
  const waiting = page.getByRole('button', { name: /^Try again in 1:(30|29|28)$/ })
  await expect(waiting).toBeDisabled()
  // The wait belongs to the refused name; another login may try at once.
  await page.getByLabel('Username', { exact: true }).fill('beta')
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeEnabled()
  await expect(page.getByRole('alert')).toHaveCount(0)
  retryAfter = '2'
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('alert')).toHaveText('Too many sign-in attempts. Try again in 2 seconds.')
  await expect(page.getByRole('button', { name: /^Try again in 0:0[12]$/ })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeEnabled({ timeout: 6000 })
  expect(calls).toEqual(['/login', '/login'])
})

test('a suspended customer sees why sign-in was refused and no session is attempted', async ({ page }) => {
  const calls = await signInPage(page, () => (reply(403, { detail: 'This hosting account is suspended. Contact your hosting provider.', account_unavailable: true })))
  const whoami = []
  page.on('request', request => { if (request.url().endsWith('/api/v1/whoami')) whoami.push(request.url()) })
  await signIn(page)
  await expect(page.getByRole('alert')).toHaveText('This hosting account is suspended. Contact your hosting provider.')
  await expect(page).toHaveURL(/\/app\/login$/)
  expect(calls).toEqual(['/login'])
  expect(whoami).toEqual([])
})

test('the second factor shows the lockout wait, refusals end the step, and Back returns to sign in', async ({ page }) => {
  let finish = reply(429, { detail: 'too many failed attempts -- try again in 5 minute(s)' })
  await signInPage(page, path => path === '/login' ? { json: { needs_2fa: true, pending_token: 'pending-test-token' } } : finish)
  await signIn(page)
  await page.getByLabel('Authentication or recovery code', { exact: true }).fill('123456')
  await page.getByRole('button', { name: 'Verify', exact: true }).click()
  await expect(page.getByRole('alert')).toHaveText('Too many sign-in attempts. Try again in 5 minutes.')
  await expect(page.getByRole('button', { name: /^Try again in (5:00|4:5\d)$/ })).toBeDisabled()
  await page.getByRole('button', { name: 'Back to sign in', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Sign in', exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)

  finish = reply(403, { detail: 'This hosting account is no longer available.', account_unavailable: true })
  await signIn(page)
  await page.getByLabel('Authentication or recovery code', { exact: true }).fill('123456')
  await page.getByRole('button', { name: 'Verify', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Sign in', exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveText('This hosting account is no longer available.')
})

// --- task failures ------------------------------------------------------------

test('failed tasks list their reason and the details show the error reference', async ({ page }) => {
  const failed = { key: 'control-operation:7', id: 7, kind: 'control-operation', label: 'Create account', account: null, status: 'failed', progress: 100, detail: "account 'qaacct1' already exists", started_at: '2026-11-01T10:00:00Z', duration_seconds: 2, request_id: 'req-7', error_reference: REFERENCE, can_retry: false, can_cancel: false }
  const state = await session(page, { handle: ({ path }) => {
    if (path === '/api/v1/tasks') return { tasks: [failed], kinds: ['control-operation'] }
    if (path === '/api/v1/tasks/control-operation/7') return { ...failed, timeline: [] }
  } })
  await page.goto('/app/tasks')
  await expect(page.getByRole('table').getByText("account 'qaacct1' already exists", { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Details', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Create account' })
  await expect(dialog.getByRole('heading', { name: 'Why it failed', exact: true })).toBeVisible()
  await expect(dialog.getByRole('alert')).toContainText("account 'qaacct1' already exists")
  await expect(dialog.getByRole('button', { name: 'Copy error reference', exact: true })).toBeVisible()
  await expect(dialog.getByRole('link', { name: 'View error details', exact: true })).toHaveAttribute('href', `/app/error-log?reference=${REFERENCE}`)
  expect(state.errors).toEqual([])
})

test('a failed account-create task shows the handler reason with its reference', async ({ page }) => {
  const state = await session(page, { handle: ({ path }) => {
    if (path === '/api/v1/admin/plans') return { plans: [] }
    if (path === '/api/v1/admin/ip-management') return { ips: [] }
    if (path === '/api/v1/task-operations/account-create') return reply(202, { id: 9, status: 'queued', progress_message: 'Queued' })
    if (path === '/api/v1/task-operations/9') return { id: 9, status: 'failed', progress_message: "account 'qaacct1' already exists", error_reference: REFERENCE }
  } })
  await page.goto('/app/accounts/new')
  await page.getByLabel('Username', { exact: false }).fill('qaacct1')
  await page.getByRole('button', { name: 'Create account', exact: true }).click()
  const alert = page.getByRole('alert').filter({ hasText: 'Account not created' })
  await expect(alert.getByText("Account not created: account 'qaacct1' already exists", { exact: true })).toBeVisible()
  await expect(alert.getByText(REFERENCE, { exact: true })).toBeVisible()
  await expect(alert.getByRole('button', { name: 'Copy error reference', exact: true })).toBeVisible()
  await expect(alert.getByRole('link', { name: 'View error details', exact: true })).toHaveAttribute('href', `/app/error-log?reference=${REFERENCE}`)
  expect(state.errors).toEqual([])
})

// --- account limits -------------------------------------------------------------

for (const skin of ['modern', 'classic', 'grid']) for (const theme of ['light', 'dark']) {
  test(`${skin} ${theme}: limits keep Unlimited values, send only edits and reset to the plan`, async ({ page }) => {
    const account = accountFixture()
    const state = await session(page, { skin, theme, handle: ({ path, method, body }) => {
      if (path === '/api/v1/accounts/alpha' && method === 'GET') return account
      if (path === '/api/v1/admin/plans') return { plans: [starter] }
      if (path === '/api/v1/accounts/alpha/limits') { Object.assign(account, body, { limits_source: 'account' }); return account }
      if (path === '/api/v1/admin/resources/accounts/alpha' && method === 'DELETE') { Object.assign(account, accountFixture()); return { username: 'alpha', policy: { scope_type: 'plan', scope_id: 1 } } }
    } })
    await page.setViewportSize({ width: 375, height: 800 })
    await page.goto('/app/accounts/alpha?tab=settings')
    const cpu = page.getByLabel('CPU cores', { exact: true })
    await expect(cpu).toHaveValue('')
    await expect(cpu).toHaveAttribute('placeholder', 'Unlimited')
    await expect(page.getByLabel('Disk IO (MB/s)', { exact: true })).toHaveValue('')
    await expect(page.getByLabel('Memory (GB)', { exact: true })).toHaveValue('1')
    await expect(page.getByLabel('Max processes', { exact: true })).toHaveValue('100')
    const note = page.getByRole('note', { name: 'Limit source' })
    await expect(note).toContainText('These limits come from the hosting plan Starter.')
    await expect(note).toContainText('Saving creates a per-account override')
    await expect(page.getByRole('button', { name: 'Update limits', exact: true })).toBeDisabled()
    expect(await noHorizontalScroll(page)).toBe(true)

    // Clearing a value cannot make it Unlimited through this form.
    await page.getByLabel('Max processes', { exact: true }).fill('')
    await page.getByLabel('Max processes', { exact: true }).blur()
    await expect(page.getByText('This form cannot remove a limit. Enter a value, or set Unlimited in Resource manager.')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Update limits', exact: true })).toBeDisabled()
    await page.getByLabel('Max processes', { exact: true }).fill('100')

    await page.getByLabel('Memory (GB)', { exact: true }).fill('2')
    await page.getByRole('button', { name: 'Update limits', exact: true }).click()
    await expect(toasts(page).getByText('Limits updated', { exact: true })).toBeVisible()
    expect(writes(state, '/api/v1/accounts/alpha/limits').map(item => item.body)).toEqual([{ mem_mb: 2048 }])
    await expect(cpu).toHaveValue('')
    await expect(note).toContainText('This account has its own limits, which replace those of the plan Starter.')

    await note.getByRole('button', { name: 'Reset to plan limits', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Reset alpha to plan limits?' })
    await dialog.getByRole('button', { name: 'Reset to plan limits', exact: true }).click()
    await expect(toasts(page).getByText('Limits reset to the plan', { exact: true })).toBeVisible()
    await expect(note).toContainText('These limits come from the hosting plan Starter.')
    await expect(page.getByLabel('Memory (GB)', { exact: true })).toHaveValue('1')
    expect(writes(state).map(item => `${item.method} ${item.path}`)).toEqual(['PATCH /api/v1/accounts/alpha/limits', 'DELETE /api/v1/admin/resources/accounts/alpha'])
    expect(await noHorizontalScroll(page)).toBe(true)
    expect(state.errors).toEqual([])
  })
}

test('a CPU allocation above the host can be reconciled without a false draft', async ({ page }) => {
  const account = accountFixture({ limits_source: 'account', plan_id: null, cpu_pct: 1600, cpu_cores: 8, configured_cpu_cores: 16, io_mb: 50 })
  const state = await session(page, { handle: ({ path, method, body }) => {
    if (path === '/api/v1/accounts/alpha' && method === 'GET') return account
    if (path === '/api/v1/admin/plans') return { plans: [] }
    if (path === '/api/v1/accounts/alpha/limits') { Object.assign(account, body, { cpu_cores: body.cpu_pct / 100, configured_cpu_cores: body.cpu_pct / 100 }); return account }
  } })
  await page.goto('/app/accounts/alpha?tab=settings')
  await expect(page.getByText('Stored as 16 cores; effective use is capped at 8. Update limits to store 8.')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Unsaved changes' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Update limits', exact: true }).click()
  await expect(toasts(page).getByText('Limits updated', { exact: true })).toBeVisible()
  expect(writes(state, '/api/v1/accounts/alpha/limits').map(item => item.body)).toEqual([{ cpu_pct: 800 }])
  await expect(page.getByText(/Stored as 16 cores/)).toHaveCount(0)
})

test('the PHP functions card explains replacement and the worker restart', async ({ page }) => {
  const state = await session(page, { handle: ({ path, method }) => {
    if (path === '/api/v1/accounts/alpha') return accountFixture()
    if (path === '/api/v1/admin/accounts/alpha/php-functions' && method === 'GET') return { default_disable_functions: ['exec', 'system'], account_override: null, domain_overrides: [] }
    if (path === '/api/v1/admin/accounts/alpha/php-functions') return { domain: null, disable_functions: ['exec', 'system'] }
    if (path === '/api/v1/accounts/alpha/domains') return { domains: [] }
  } })
  await page.goto('/app/accounts/alpha?tab=php-functions')
  await expect(page.getByText(/An override replaces the server default list; it is not added to it/)).toBeVisible()
  await expect(page.getByText(/saving or clearing an override restarts this account's PHP workers/)).toBeVisible()
  await page.getByRole('button', { name: 'Save override for this scope', exact: true }).click()
  await expect(toasts(page).getByText('PHP workers for alpha were restarted to apply it.', { exact: true })).toBeVisible()
  expect(state.errors).toEqual([])
})

// --- plans ------------------------------------------------------------------------

test('deleting a plan in use keeps the dialog open with the reason', async ({ page }) => {
  const reason = "plan 'Starter' is assigned to 2 account(s) (alpha, beta); apply another plan to them before deleting it"
  const state = await session(page, { handle: ({ path, method }) => {
    if (path === '/api/v1/admin/plans') return { plans: [starter] }
    if (path === '/api/v1/admin/plans/1' && method === 'DELETE') return reply(409, { detail: reason })
  } })
  await page.goto('/app/plans')
  await page.getByRole('button', { name: 'Actions for Starter', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Delete', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Delete Starter?' })
  await expect(dialog).toContainText('A plan that hosting accounts still use cannot be deleted')
  await dialog.getByRole('button', { name: 'Delete plan', exact: true }).click()
  await expect(dialog.getByRole('alert')).toHaveText(reason)
  await expect(dialog).toBeVisible()
  expect(writes(state)).toHaveLength(1)
})

test('a plan edit reports updated accounts and stays open for the ones that failed', async ({ page }) => {
  let reply = { ...starter, accounts_updated: 1, propagation_failures: [{ username: 'beta', error: 'disk quota could not be applied' }] }
  const state = await session(page, { handle: ({ path, method }) => {
    if (path === '/api/v1/admin/plans/1' && method === 'PATCH') return reply
    if (path === '/api/v1/admin/plans/1') return starter
    if (path === '/api/v1/admin/plans') return { plans: [starter] }
    if (path === '/api/v1/admin/resources') return { host: { cpu_cores: 8, memory_gb: 16 }, users: [], policies: [], history: [], faults: [] }
  } })
  await page.goto('/app/plans/1/edit')
  await page.getByRole('textbox', { name: 'Plan name', exact: true }).fill('Starter plus')
  await page.getByRole('button', { name: 'Save plan', exact: true }).first().click()
  const report = page.getByRole('alert', { name: 'Accounts not updated' })
  await expect(report).toContainText('The plan was saved, but 1 assigned account was not updated')
  await expect(report).toContainText('beta: disk quota could not be applied')
  await expect(report.getByRole('link', { name: 'beta', exact: true })).toHaveAttribute('href', '/app/accounts/beta?tab=settings')
  await expect(page).toHaveURL(/\/plans\/1\/edit$/)
  await report.getByRole('button', { name: 'Back to plans', exact: true }).click()
  await expect(page).toHaveURL(/\/plans$/)

  reply = { ...starter, accounts_updated: 3, propagation_failures: [] }
  await page.goto('/app/plans/1/edit')
  await page.getByRole('textbox', { name: 'Plan name', exact: true }).fill('Starter max')
  await page.getByRole('button', { name: 'Save plan', exact: true }).first().click()
  await expect(toasts(page).getByText('Applied to 3 assigned accounts.', { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/\/plans$/)
  expect(writes(state).map(item => item.body.name)).toEqual(['Starter plus', 'Starter max'])
  expect(state.errors).toEqual([])
})

// --- resellers ----------------------------------------------------------------------

for (const skin of ['modern', 'classic', 'grid']) for (const theme of ['light', 'dark']) {
  test(`${skin} ${theme}: deleting a reseller offers to return its accounts`, async ({ page }) => {
    const resellers = [
      { id: 4, username: 'sellerone', company: '', plan_id: 1, plan_name: 'Agency', account_count: 0, max_accounts: 10, status: 'active' },
      { id: 5, username: 'sellertwo', company: 'Two', plan_id: 1, plan_name: 'Agency', account_count: 2, max_accounts: 10, status: 'active' },
    ]
    const state = await session(page, { skin, theme, handle: ({ path, method, search }) => {
      if (path === '/api/v1/admin/resellers/plans') return { plans: [{ id: 1, name: 'Agency', max_accounts: 10, max_total_disk_mb: 102400, account_quota_soft_mb: 4096, account_quota_hard_mb: 5120, account_cpu_pct: 50, account_mem_mb: 1024, php_version: '8.3', reseller_count: 2 }, { id: 2, name: 'Spare', max_accounts: 5, max_total_disk_mb: 51200, account_quota_soft_mb: 4096, account_quota_hard_mb: 5120, account_cpu_pct: 50, account_mem_mb: 1024, php_version: '8.3', reseller_count: 0 }] }
      if (path === '/api/v1/admin/resellers') return { resellers }
      if (path === '/api/v1/accounts') return [{ id: 11, username: 'shopa', status: 'active', reseller: { id: 5, username: 'sellertwo' } }, { id: 12, username: 'shopb', status: 'active', reseller: { id: 5, username: 'sellertwo' } }, { id: 13, username: 'own', status: 'active', reseller: null }]
      if (path === '/api/v1/admin/resellers/4' && method === 'DELETE') return search ? { status: 'deleted', id: 4, released_accounts: ['late1'] } : reply(409, { detail: 'reseller still owns 1 account(s) (late1); move or terminate them, or delete with release_accounts to return them to administrator ownership' })
      if (path === '/api/v1/admin/resellers/5' && method === 'DELETE') return { status: 'deleted', id: 5, released_accounts: ['shopa', 'shopb'] }
    } })
    await page.setViewportSize({ width: 375, height: 800 })
    await page.goto('/app/resellers')
    await page.getByRole('button', { name: 'Actions for sellerone', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Delete reseller', exact: true }).click()
    let dialog = page.getByRole('dialog', { name: 'Delete reseller sellerone?' })
    const plain = dialog.getByRole('button', { name: 'Delete reseller', exact: true })
    await expect(plain).toBeDisabled()
    await dialog.getByLabel('Type sellerone to confirm', { exact: true }).fill('sellerone')
    await plain.click()
    await expect(dialog).toContainText('This reseller still owns hosting accounts, so it cannot be deleted on its own.')
    await expect(dialog.getByRole('alert')).toHaveCount(0)
    expect(await noHorizontalScroll(page)).toBe(true)
    await dialog.getByRole('button', { name: 'Return accounts to administrator and delete', exact: true }).click()
    await expect(toasts(page).getByText('1 hosting account was returned to administrator ownership.', { exact: true })).toBeVisible()
    await expect(dialog).toHaveCount(0)

    await page.getByRole('button', { name: 'Actions for sellertwo', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Delete reseller', exact: true }).click()
    dialog = page.getByRole('dialog', { name: 'Delete reseller sellertwo?' })
    await expect(dialog).toContainText('sellertwo owns 2 hosting accounts (shopa, shopb).')
    await dialog.getByLabel('Type sellertwo to confirm', { exact: true }).fill('sellertwo')
    await dialog.getByRole('button', { name: 'Return accounts to administrator and delete', exact: true }).click()
    await expect(toasts(page).getByText('2 hosting accounts were returned to administrator ownership.', { exact: true })).toBeVisible()
    expect(writes(state).map(item => `${item.path}${item.search}`)).toEqual(['/api/v1/admin/resellers/4', '/api/v1/admin/resellers/4?release_accounts=true', '/api/v1/admin/resellers/5?release_accounts=true'])

    await page.getByRole('tab', { name: 'Plans', exact: true }).click()
    await page.getByRole('button', { name: 'Actions for Agency', exact: true }).click()
    await expect(page.getByRole('menuitem', { name: 'Delete', exact: true })).toBeDisabled()
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Actions for Spare', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Delete', exact: true }).click()
    await expect(page.getByRole('dialog', { name: 'Delete Spare?' })).toContainText('A plan that resellers use can be deleted once they are moved to another plan or deleted themselves.')
    expect(state.errors).toEqual([])
  })
}

// --- API tokens ---------------------------------------------------------------------

test('token creation and revocation show the session refusal in the dialog', async ({ page }) => {
  const detail = 'A current administrator session is required'
  const state = await session(page, { handle: ({ path, method }) => {
    if (path === '/api/v1/tokens' && method === 'GET') return [{ id: 3, label: 'billing', role: 'admin', account_id: null, revoked: false, scope: 'full', expires_at: '2099-01-01T00:00:00Z' }]
    if (path.startsWith('/api/v1/tokens')) return reply(403, { detail, reference: REFERENCE })
  } })
  await page.goto('/app/tokens')
  await page.getByRole('button', { name: 'Create token', exact: true }).first().click()
  const create = page.getByRole('dialog', { name: 'Create API token' })
  await expect(create).toContainText('Only a signed-in administrator can create or revoke tokens')
  await create.getByLabel('Label', { exact: false }).fill('billing-two')
  await create.getByRole('button', { name: 'Create token', exact: true }).click()
  await expect(create.getByRole('alert')).toHaveText(`Could not create the token: ${detail} (reference: ${REFERENCE})`)
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Revoke', exact: true }).click()
  const revoke = page.getByRole('dialog', { name: /Revoke .billing.\?/ })
  await revoke.getByRole('button', { name: 'Revoke token', exact: true }).click()
  await expect(revoke.getByRole('alert')).toHaveText(`${detail} (reference: ${REFERENCE})`)
  expect(writes(state).map(item => `${item.method} ${item.path}`)).toEqual(['POST /api/v1/tokens', 'DELETE /api/v1/tokens/3'])
})

// --- backup jobs --------------------------------------------------------------------

for (const skin of ['modern', 'classic', 'grid']) for (const theme of ['light', 'dark']) {
  test(`${skin} ${theme}: backup jobs and legacy schedules can be deleted`, async ({ page }) => {
    let policyDeletes = 0
    let schedules = [
      { id: 5, account_id: null, frequency: 'daily', retention_count: 7, destination_id: 2, enabled: true },
      { id: 6, account_id: 11, frequency: 'weekly', retention_count: 4, destination_id: 2, enabled: false },
    ]
    let policies = [{ id: 3, name: 'Nightly', destination_id: 1, frequency: 'daily', enabled: true, options: { mode: 'incremental', retention_count: 7 } }]
    const state = await session(page, { skin, theme, handle: ({ path, method }) => {
      if (path === '/api/v1/accounts') return [{ id: 11, username: 'alpha', status: 'active' }]
      if (path === '/api/v1/backups/snapshots/destinations') return { destinations: [{ id: 1, name: 'Local', kind: 'local', status: 'ready', enabled: true }] }
      if (path === '/api/v1/backups/snapshots/policies') return { policies }
      if (path === '/api/v1/backups/snapshots/policies/3' && method === 'DELETE') {
        policyDeletes += 1
        if (policyDeletes === 1) return reply(400, { detail: "Wait for this job's active backups to finish before deleting it" })
        policies = []
        return { id: 3, name: 'Nightly', status: 'deleted', history_kept: true }
      }
      if (path === '/api/v1/backups/snapshots/catalog/accounts') return { counts: { active: 1, former: 0 }, accounts: [] }
      if (path === '/api/v1/backups/snapshots/destination-operations') return { operations: [] }
      if (path === '/api/v1/backups/schedules') return { schedules }
      if (path === '/api/v1/backups/destinations') return { destinations: [{ id: 2, name: 'Old archive disk', kind: 'local' }] }
      if (path === '/api/v1/backups/schedules/6' && method === 'DELETE') { schedules = schedules.filter(row => row.id !== 6); return { id: 6, status: 'deleted' } }
    } })
    await page.setViewportSize({ width: 375, height: 800 })
    await page.goto('/app/backup-jobs?tab=jobs')
    await page.getByRole('button', { name: 'Actions for Nightly', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Delete', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Delete backup job Nightly?' })
    await dialog.getByRole('button', { name: 'Delete job', exact: true }).click()
    await expect(dialog.getByRole('alert')).toHaveText("Wait for this job's active backups to finish before deleting it")
    await dialog.getByRole('button', { name: 'Delete job', exact: true }).click()
    await expect(toasts(page).getByText('Its run history and recovery points are kept and stay available for restore.', { exact: true })).toBeVisible()
    await expect(dialog).toHaveCount(0)

    // Phone width shows each schedule as a card.
    const legacy = page.getByRole('region', { name: 'Legacy archive schedules' })
    await expect(legacy.getByText('All accounts (server default)', { exact: true }).filter({ visible: true })).toBeVisible()
    await expect(legacy.getByText('Old archive disk', { exact: true }).filter({ visible: true })).toHaveCount(2)
    expect(await noHorizontalScroll(page)).toBe(true)
    await page.setViewportSize({ width: 1280, height: 900 })
    await expect(legacy.getByRole('cell', { name: 'All accounts (server default)', exact: true })).toBeVisible()
    await legacy.getByRole('row').filter({ hasText: 'alpha' }).getByRole('button', { name: 'Delete', exact: true }).click()
    const confirm = page.getByRole('dialog', { name: 'Delete legacy schedule?' })
    await expect(confirm).toContainText('Scheduled archives for alpha stop.')
    await confirm.getByRole('button', { name: 'Delete schedule', exact: true }).click()
    await expect(toasts(page).getByText('Legacy schedule deleted', { exact: true })).toBeVisible()
    await expect(legacy.getByRole('row').filter({ hasText: 'alpha' })).toHaveCount(0)
    await expect(legacy.getByRole('row').filter({ hasText: 'All accounts (server default)' })).toHaveCount(1)
    expect(writes(state).map(item => `${item.method} ${item.path}`)).toEqual(['DELETE /api/v1/backups/snapshots/policies/3', 'DELETE /api/v1/backups/snapshots/policies/3', 'DELETE /api/v1/backups/schedules/6'])
    expect(state.errors).toEqual([])
  })
}
