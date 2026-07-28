import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { expect, test, type Page } from '@playwright/test'

const fixtureRoot = resolve(import.meta.dirname, '../../contracts/replay-data/v1/fixtures/deterministic-race')

test('loads deterministic replay and supports its critical controls', async ({ page }) => {
  await installReplayRoutes(page)

  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'F1 Race Replay' })).toBeVisible()
  await expect(page.getByRole('table', { name: 'Live race leaderboard' })).toBeVisible()

  await page.getByRole('button', { name: 'Play', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Pause', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: '2×' }).click()
  await expect(page.getByRole('button', { name: '2×' })).toHaveAttribute('aria-pressed', 'true')

  await page.getByRole('button', { name: 'Select George Russell' }).click()
  await expect(page.getByRole('button', { name: 'Select George Russell' })).toHaveAttribute('aria-pressed', 'true')

  await page.getByRole('button', { name: 'Hide Track map panel' }).click()
  await expect(page.getByRole('button', { name: 'Show Track map panel' })).toHaveAttribute('aria-pressed', 'false')
})

test('recovers when the initial replay pointer request fails', async ({ page }) => {
  const routes = await installReplayRoutes(page, true)

  await page.goto('/')
  await expect(page.getByRole('alert', { name: 'Replay loading error' })).toContainText('Replay-data request failed: 503')

  routes.recover()
  await page.getByRole('button', { name: 'Retry loading' }).click()

  await expect(page.getByRole('heading', { name: 'F1 Race Replay' })).toBeVisible()
})

async function installReplayRoutes(page: Page, initiallyUnavailable = false): Promise<{ readonly recover: () => void }> {
  const manifest = JSON.parse(await readFile(resolve(fixtureRoot, 'manifest.json'), 'utf8')) as Record<string, unknown>
  const deliveryVersion = 'e2e-delivery'
  const manifestBytes = Buffer.from(JSON.stringify({ ...manifest, formatVersion: 'browser-delivery-v1', deliveryVersion }))
  const pointerBytes = Buffer.from(JSON.stringify({
    formatVersion: 'browser-delivery-v1',
    deliveryVersion,
    manifestPath: `generations/${deliveryVersion}/manifest.json`,
    manifestSha256: createHash('sha256').update(manifestBytes).digest('hex'),
  }))
  const assets = new Map<string, Buffer>([
    ['/replay-data/browser-current.json', pointerBytes],
    [`/replay-data/generations/${deliveryVersion}/manifest.json`, manifestBytes],
    [`/replay-data/generations/${deliveryVersion}/track-assets.json`, await readFile(resolve(fixtureRoot, 'track-assets.json'))],
    [`/replay-data/generations/${deliveryVersion}/chunks/chunk-001.json`, await readFile(resolve(fixtureRoot, 'chunks/chunk-001.json'))],
    [`/replay-data/generations/${deliveryVersion}/chunks/chunk-002.json`, await readFile(resolve(fixtureRoot, 'chunks/chunk-002.json'))],
  ])
  let unavailable = initiallyUnavailable

  await page.route('**/replay-data/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (unavailable && path === '/replay-data/browser-current.json') {
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
