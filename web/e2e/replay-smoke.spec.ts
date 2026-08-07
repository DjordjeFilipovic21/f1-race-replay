import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

const fixtureRoot = resolve(import.meta.dirname, '../../contracts/replay-data/v2/fixtures/deterministic-race')
const raceId = '2026-round-1-deterministic-race'
const generationId = '2026-round-1-session-race-mode-race'

test('loads deterministic replay and supports its critical controls', async ({ page }) => {
  const { recover } = await installReplayRoutes(page)

  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'Race Replay Library' })).toBeVisible()
  await page.getByRole('button', { name: /Deterministic Grand Prix/ }).click()
  await page.getByRole('radio', { name: 'Race' }).click()
  await page.getByRole('button', { name: 'Open replay workspace' }).click()
  await expect(page.getByRole('heading', { name: 'F1 Race Replay' })).toBeVisible()
  await expect(page.getByRole('table', { name: 'Live race leaderboard' })).toBeVisible()

  await page.getByRole('button', { name: 'Play', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Pause', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: '2×' }).click()
  await expect(page.getByRole('button', { name: '2×' })).toHaveAttribute('aria-pressed', 'true')

  await page.getByRole('button', { name: 'Select George Russell' }).click()
  await expect(page.getByRole('button', { name: 'Select George Russell' })).toHaveAttribute('aria-pressed', 'true')

  await page.getByRole('button', { name: 'Unpin Track map panel' }).click()
  await expect(page.getByRole('region', { name: 'Track map', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Panel Manager' }).click()
  await expect(page.getByRole('dialog', { name: 'Panel Manager' })).toBeVisible()
  await page.getByRole('button', { name: 'Pin Track map panel' }).click()
  await expect(page.getByRole('region', { name: 'Track map', exact: true })).toBeVisible()
})

test('recovers when the initial replay pointer request fails', async ({ page }) => {
  const routes = await installReplayRoutes(page, true)

  await page.goto('/')
  await page.getByRole('button', { name: /Deterministic Grand Prix/ }).click()
  await page.getByRole('radio', { name: 'Race' }).click()
  await page.getByRole('button', { name: 'Open replay workspace' }).click()
  await expect(page.getByRole('alert', { name: 'Replay loading error' })).toContainText('Replay-data request failed: 503')

  routes.recover()
  await page.getByRole('button', { name: 'Retry loading' }).click()

  await expect(page.getByRole('heading', { name: 'F1 Race Replay' })).toBeVisible()
})

test('renders accessible globe and circuit preview for a race before entering workspace', async ({ page }) => {
  await installReplayRoutes(page)

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Race Replay Library' })).toBeVisible()

  // Act — select the deterministic race (visual metadata is nested in the catalog)
  await page.getByRole('button', { name: /Deterministic Grand Prix/ }).click()

  // Assert — accessible globe is rendered, labeled for the race with valid coordinates
  const globe = page.locator('svg[role="img"][aria-label="Globe centred on Deterministic Grand Prix"]')
  await expect(globe).toBeVisible()
  await expect(globe.locator('title')).toHaveText('Globe centred on Deterministic Grand Prix')
  await expect(globe.locator('desc')).toContainText('48.7544°')
  await expect(globe.locator('desc')).toContainText('2.2211°')

  // Assert — accessible circuit preview resolves from the routed JSON asset
  const circuit = page.locator('svg[role="img"][aria-label="Deterministic Grand Prix circuit preview"]')
  await expect(circuit).toBeVisible()
  await expect(circuit.locator('title')).toHaveText('Deterministic Grand Prix circuit preview')
  await expect(circuit.locator('.circuit-preview__path')).toHaveAttribute('d', 'M 150 50 A 100 100 0 1 1 149.9999 50')

  // Assert — URL selection remains local; no query parameters are pushed yet
  expect(new URL(page.url()).search).toBe('')

  // Assert — workspace still opens explicitly after the visual preview step
  await page.getByRole('radio', { name: 'Race' }).click()
  await page.getByRole('button', { name: 'Open replay workspace' }).click()
  await expect(page.getByRole('heading', { name: 'F1 Race Replay' })).toBeVisible()
  expect(new URL(page.url()).searchParams.get('race')).toBe(raceId)
})

test('renders globe with reduced-motion preference', async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: 'reduce' })
  const page = await context.newPage()
  try {
    await installReplayRoutes(page)

    await page.goto('/')
    await page.getByRole('button', { name: /Deterministic Grand Prix/ }).click()

    const globe = page.locator('svg[role="img"][aria-label="Globe centred on Deterministic Grand Prix"]')
    await expect(globe).toBeVisible()
    await expect(globe.locator('desc')).toContainText('48.7544°')
  } finally {
    await context.close()
  }
})

async function installReplayRoutes(page: Page, initiallyUnavailable = false): Promise<{ readonly recover: () => void }> {
  const manifest = JSON.parse(await readFile(resolve(fixtureRoot, 'manifest.json'), 'utf8')) as Record<string, unknown>
  const deliveryVersion = 'e2e-delivery'
  const browserRoot = `/replay-data/seasons/2026/browser/${raceId}/`
  const catalog = {
    schemaVersion: 2,
    year: 2026,
    atomicAcrossRaces: true,
    races: [{
      race_id: raceId,
      round_number: 1,
      event_name: 'Deterministic Grand Prix',
      country: 'Testland',
      visual: {
        latitude: 48.7544,
        longitude: 2.2211,
        circuitPreview: 'previews/deterministic-circuit.json',
      },
      sessions: [{
        session_code: 'r',
        session_name: 'Race',
        generation_id: generationId,
        delivery_version: deliveryVersion,
        outcome: 'classified',
        validated: true,
        canonical_pointer: `canonical/${raceId}/sessions/r/manifest.json`,
        browser_pointer: `browser/${raceId}/sessions/r/browser-current.json`,
      }],
    }],
  }
  // Minimal valid circuit-preview asset: a near-closed arc so the path parser
  // accepts drawable geometry without requiring real track coordinates.
  const circuitPreview = JSON.stringify({
    pathData: 'M 150 50 A 100 100 0 1 1 149.9999 50',
    viewBox: '50 -50 200 200',
  })
  const manifestBytes = Buffer.from(JSON.stringify({ ...manifest, deliveryVersion }))
  const pointerBytes = Buffer.from(JSON.stringify({
    formatVersion: 'browser-delivery-v2',
    deliveryVersion,
    manifestPath: `generations/${deliveryVersion}/manifest.json`,
    manifestSha256: createHash('sha256').update(manifestBytes).digest('hex'),
  }))
  const assets = new Map<string, Buffer>([
    ['/replay-data/seasons/2026/catalog.json', Buffer.from(JSON.stringify(catalog))],
    ['/replay-data/seasons/2026/previews/deterministic-circuit.json', Buffer.from(circuitPreview)],
    [`${browserRoot}sessions/r/browser-current.json`, pointerBytes],
    [`${browserRoot}generations/${deliveryVersion}/manifest.json`, manifestBytes],
    [`${browserRoot}generations/${deliveryVersion}/track-assets.json`, await readFile(resolve(fixtureRoot, 'track-assets.json'))],
    [`${browserRoot}generations/${deliveryVersion}/chunks/chunk-001.json`, await readFile(resolve(fixtureRoot, 'chunks/chunk-001.json'))],
    [`${browserRoot}generations/${deliveryVersion}/chunks/chunk-002.json`, await readFile(resolve(fixtureRoot, 'chunks/chunk-002.json'))],
  ])
  let unavailable = initiallyUnavailable

  await page.route('**/replay-data/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (unavailable && path === `${browserRoot}sessions/r/browser-current.json`) {
      await route.fulfill({ status: 503, contentType: 'application/json', body: '{}' })
      return
    }
    const body = assets.get(path)
    await route.fulfill(body === undefined
      ? { status: 404, contentType: 'application/json', body: '{}' }
      : { status: 200, contentType: 'application/json', body })
  })

  return { recover: () => { unavailable = false } }
}
