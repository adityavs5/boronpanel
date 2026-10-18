import { test, expect } from '@playwright/test'

test.use({ ignoreHTTPSErrors: true })

const complete = step => ({ step, status: 'ok' })
const running = step => ({ step, status: 'running' })
const initialSteps = [
  running('preflight'), complete('preflight'), complete('preflight'),
  complete('backup'), complete('download'), complete('signature'),
  running('checksum'),
]

function updateJob(overrides = {}) {
  return {
    id: 42, kind: 'update', status: 'running', from_version: '3.2.0', to_version: '3.2.2',
    started_at: '2026-10-18T12:00:00Z', initiated_by: 'preview', steps: initialSteps,
    ...overrides,
  }
}

async function mockUpdateStatus(page, { skin = 'evolution', theme = 'light', job = updateJob() } = {}) {
  await page.addInitScript(({ skin, theme }) => {
    localStorage.setItem('boron.auth', JSON.stringify({ state: { role: 'admin', username: 'preview' }, version: 0 }))
    localStorage.setItem('boron.ui', JSON.stringify({ state: { skin, theme }, version: 0 }))
  }, { skin, theme })
  let activeJob = job
  let mutations = 0
  let statusRequests = 0
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let data = {}
    if (path.endsWith('/whoami')) data = { role: 'admin', username: 'preview' }
    else if (path.endsWith('/branding')) data = { panel_name: 'Boron' }
    else if (path.endsWith('/update/status')) {
      statusRequests++
      data = {
        configured: true, channel: 'beta', current_version: '3.2.0', latest_version: '3.2.2',
        update_available: true, active_job: activeJob, release_metadata: { data_plane_reloads: [] },
      }
    } else if (path.endsWith('/update/history')) data = { jobs: [] }
    else if (path.endsWith('/update/database-backups')) data = { backups: [] }
    if (route.request().method() !== 'GET') mutations++
    await route.fulfill({ json: data })
  })
  return {
    setJob: nextJob => { activeJob = nextJob },
    get mutations() { return mutations },
    get statusRequests() { return statusRequests },
  }
}

async function expectProgress(page, completed, total, percent) {
  const section = page.getByRole('region', { name: 'Update progress', exact: true })
  await expect(section).toBeVisible()
  const progress = section.getByRole('progressbar', { name: 'Completed update stages' })
  await expect(progress).toHaveAttribute('aria-valuemin', '0')
  await expect(progress).toHaveAttribute('aria-valuenow', String(completed))
  await expect(progress).toHaveAttribute('aria-valuemax', String(total))
  await expect(section.getByText(`${percent}% · ${completed} of ${total} stages complete`, { exact: true })).toBeVisible()
  await expect(progress).toHaveAttribute('aria-valuetext', new RegExp(`^${completed} of ${total} stages complete\\.`))
  return section
}

async function expectTopLayout(page, section) {
  const sectionBox = await section.boundingBox()
  const tierBox = await page.getByText('Update tier', { exact: true }).boundingBox()
  expect(sectionBox).not.toBeNull()
  expect(tierBox).not.toBeNull()
  expect(sectionBox.y + sectionBox.height).toBeLessThanOrEqual(tierBox.y)
  const geometry = await section.evaluate(el => ({
    left: el.getBoundingClientRect().left,
    right: el.getBoundingClientRect().right,
    viewportWidth: innerWidth,
    sectionOverflows: el.scrollWidth > el.clientWidth,
    pageOverflows: document.documentElement.scrollWidth > innerWidth,
  }))
  expect(geometry.left).toBeGreaterThanOrEqual(0)
  expect(geometry.right).toBeLessThanOrEqual(geometry.viewportWidth)
  expect(geometry.sectionOverflows).toBe(false)
  expect(geometry.pageOverflows).toBe(false)
}

for (const skin of ['evolution', 'paper-lantern']) for (const theme of ['light', 'dark']) {
  test(`top update progress polls accurately without overflow ${skin} ${theme}`, async ({ page }) => {
    const mocked = await mockUpdateStatus(page, { skin, theme })
    await page.goto(process.env.BORON_UI_CHECK_URL || '/app/updates')
    for (const width of [1440, 768, 375]) {
      await page.setViewportSize({ width, height: 900 })
      const section = await expectProgress(page, 3, 8, 38)
      await expect(section.getByRole('status')).toHaveText('Verify SHA256 checksum')
      await expectTopLayout(page, section)
    }

    const requestsBeforeAdvance = mocked.statusRequests
    mocked.setJob(updateJob({ steps: [...initialSteps, complete('checksum'), running('venv')] }))
    await expectProgress(page, 4, 8, 50)
    expect(mocked.statusRequests).toBeGreaterThan(requestsBeforeAdvance)
    for (const width of [1440, 768, 375]) {
      await page.setViewportSize({ width, height: 900 })
      const section = await expectProgress(page, 4, 8, 50)
      await expect(section.getByRole('status')).toHaveText('Build Python environment')
      await expectTopLayout(page, section)
      await page.screenshot({ path: `test-results/update-progress-${skin}-${theme}-${width}.png` })
    }
    expect(mocked.mutations).toBe(0)
  })
}

test('finalizer substeps and repeated restarts do not inflate update progress', async ({ page }) => {
  const steps = [
    ...['preflight', 'backup', 'download', 'checksum', 'extract', 'venv', 'migrate'].map(complete),
    complete('preflight'), complete('signature'), complete('release-metadata'), complete('mail-guard'),
    running('finalize'), running('finalize'), complete('swap'),
    complete('restart'), complete('restart'), complete('healthcheck'), complete('readiness'),
  ]
  const mocked = await mockUpdateStatus(page, { job: updateJob({ status: 'finalizing', steps }) })
  await page.goto(process.env.BORON_UI_CHECK_URL || '/app/updates')
  const section = await expectProgress(page, 7, 8, 88)
  await expect(section.getByRole('status')).toHaveText('Swap, restart panel services, health check')
  expect(mocked.mutations).toBe(0)
})

test('rollback progress counts two stages and remains readable with reduced motion', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 375, height: 900 })
  const mocked = await mockUpdateStatus(page, {
    job: updateJob({
      kind: 'rollback', status: 'finalizing', from_version: '3.2.2', to_version: '3.2.0',
      steps: [complete('preflight'), complete('preflight'), running('finalize'), complete('swap'), complete('restart'), complete('restart')],
    }),
  })
  await page.goto(process.env.BORON_UI_CHECK_URL || '/app/updates')
  const section = await expectProgress(page, 1, 2, 50)
  await expect(section.getByRole('heading', { name: 'Rollback progress' })).toBeVisible()
  await expect(section.getByRole('status')).toHaveText('Swap, restart panel services, health check')
  await expect(section.getByText('Based on completed stages, not time remaining. Some stages take longer than others.', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true)
  await expectTopLayout(page, section)
  expect(mocked.mutations).toBe(0)
})
