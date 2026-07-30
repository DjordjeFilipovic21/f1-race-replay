/**
 * @vitest-environment jsdom
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from '../../src/app/App'
import { loadCatalog } from '../../src/data/catalog/loader'
import { loadReplayIndex } from '../../src/data/replay/loader'
import { createReplayController, type ReplayController, type ReplayControllerSnapshot } from '../../src/engine/replay'
import type { CatalogV2 } from '../../src/data/catalog/types'
import type { ReplayIndex, TrackAssets } from '../../src/data/replay/types'

vi.mock('../../src/data/catalog/loader', () => ({ loadCatalog: vi.fn() }))
vi.mock('../../src/data/replay/loader', () => ({ loadReplayIndex: vi.fn() }))
vi.mock('../../src/engine/replay', () => ({ createReplayController: vi.fn() }))

interface Deferred<T> {
  readonly promise: Promise<T>
  readonly resolve: (value: T) => void
}

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((promiseResolve) => { resolve = promiseResolve })
  return { promise, resolve }
}

const trackAssets: TrackAssets = {
  contractVersion: 'v1', fixtureId: 'test-race', trackId: 'test-track', trackName: 'Test Track',
  coordinateSpace: { units: 'meters', origin: 'test origin' }, circuitLengthMeters: 1000, rotationDegrees: 0,
  startFinish: { center: { x: 0, y: 5 }, inner: { x: 0, y: 0 }, outer: { x: 0, y: 10 } },
  centerLine: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }],
  innerBoundary: [{ x: 1, y: 1 }, { x: 9, y: 1 }, { x: 9, y: 9 }, { x: 1, y: 9 }],
  outerBoundary: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }],
}

const index = {
  manifest: { chunks: [{ startMs: 0, endMs: 3000 }], drivers: [] },
  trackAssets,
  timelineSummary: {
    contractVersion: 'v1', fixtureId: 'test-race', startMs: 0, endMs: 3000,
    intervals: [{ kind: 'yellow', startMs: 500, endMs: 1000 }], dnfMarkers: [],
  },
} as unknown as ReplayIndex

const catalog: CatalogV2 = {
  schemaVersion: 2,
  year: 2024,
  atomicAcrossRaces: false,
  races: [{
    race_id: 'race-1',
    round_number: 1,
    event_name: 'Bahrain Grand Prix',
    country: 'Bahrain',
    sessions: [{
      session_code: 'r',
      session_name: 'Race',
      generation_id: 'gen-1',
      delivery_version: 'v1',
      outcome: 'classified',
      validated: true,
      canonical_pointer: 'canonical/race-1/sessions/r/manifest.json',
      browser_pointer: 'browser/race-1/sessions/r/browser-current.json',
    }],
  }],
}

function createController(): ReplayController {
  const snapshot: ReplayControllerSnapshot = {
    status: 'loading', timeMs: 0, speed: 1, isPlaying: false, replay: null, crossedEvents: [], error: null,
  }
  return {
    getSnapshot: () => snapshot,
    subscribe: () => () => undefined,
    start: vi.fn(), pause: vi.fn(), seek: vi.fn(), setSpeed: vi.fn(), retry: vi.fn(async () => undefined), dispose: vi.fn(),
  }
}

beforeEach(() => {
  window.history.replaceState(null, '', '/')
  vi.mocked(loadCatalog).mockResolvedValue(catalog)
  vi.mocked(loadReplayIndex).mockResolvedValue(index)
  vi.mocked(createReplayController).mockReturnValue(createController())
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  Reflect.deleteProperty(document, 'startViewTransition')
  delete document.documentElement.dataset.pageTransitionDirection
  window.history.replaceState(null, '', '/')
})

test('loads the catalog and renders the race library without entering replay', async () => {
  render(<App />)

  expect(await screen.findByRole('heading', { name: 'Race Replay Library' })).toBeTruthy()
  expect(screen.getByRole('button', { name: /Bahrain Grand Prix/ })).toBeTruthy()
  expect(loadReplayIndex).not.toHaveBeenCalled()
})

test('keeps landing selection local until Open Workspace writes the complete URL', async () => {
  render(<App />)
  await screen.findByRole('button', { name: /Bahrain Grand Prix/ })

  fireEvent.click(screen.getByRole('button', { name: /Bahrain Grand Prix/ }))
  fireEvent.click(screen.getByRole('radio', { name: /Race/ }))
  expect(window.location.search).toBe('')

  fireEvent.click(screen.getByRole('button', { name: 'Open replay workspace' }))
  expect(window.location.search).toBe('?year=2024&race=race-1&session=r')
  expect(await screen.findByRole('group', { name: 'Race status timeline' })).toBeTruthy()
  expect(loadReplayIndex).toHaveBeenCalledWith({
    source: expect.anything(),
    pointerPath: 'sessions/r/browser-current.json',
  })
})

test('uses directional transitions between the library, race details, and workspace', async () => {
  const directions: string[] = []
  Object.defineProperty(document, 'startViewTransition', {
    configurable: true,
    value: (update: () => void) => {
      directions.push(document.documentElement.dataset.pageTransitionDirection ?? '')
      update()
      return {
        finished: Promise.resolve(),
        ready: Promise.resolve(),
        skipTransition: vi.fn(),
        types: new Set<string>(),
        updateCallbackDone: Promise.resolve(),
      } as unknown as ViewTransition
    },
  })
  render(<App />)
  await screen.findByRole('button', { name: /Bahrain Grand Prix/ })

  fireEvent.click(screen.getByRole('button', { name: /Bahrain Grand Prix/ }))
  expect(screen.getByRole('heading', { name: 'Bahrain Grand Prix' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'All races' }))
  expect(screen.getByRole('heading', { name: 'Race Replay Library' })).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: /Bahrain Grand Prix/ }))
  fireEvent.click(screen.getByRole('radio', { name: /Race/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Open replay workspace' }))
  expect(await screen.findByRole('group', { name: 'Race status timeline' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Change session' }))

  expect(directions).toEqual(['forward', 'backward', 'forward', 'forward', 'backward'])
})

test('shows an actionable message for an invalid URL and never loads its pointer', async () => {
  window.history.replaceState(null, '', '/?trajectory=linear&year=2024&race=unknown&session=r')
  render(<App />)

  expect(await screen.findByRole('alert', { name: 'Replay Selection Unavailable' })).toBeTruthy()
  expect(screen.getByText(/Choose a listed race/)).toBeTruthy()
  expect(loadReplayIndex).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: /Bahrain Grand Prix/ }))
  fireEvent.click(screen.getByRole('radio', { name: /Race/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Open replay workspace' }))
  expect(window.location.search).toBe('?trajectory=linear&year=2024&race=race-1&session=r')
})

test('shows an actionable message for a malformed year parameter', async () => {
  window.history.replaceState(null, '', '/?trajectory=linear&year=not-a-year')
  render(<App />)

  expect(await screen.findByRole('alert', { name: 'Replay Selection Unavailable' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Race Replay Library' })).toBeTruthy()
  expect(loadReplayIndex).not.toHaveBeenCalled()
})

test('shows an actionable message for malformed race and session parameters', async () => {
  window.history.replaceState(null, '', '/?trajectory=linear&year=2024&race=../bahrain&session=')
  render(<App />)

  expect(await screen.findByRole('alert', { name: 'Replay Selection Unavailable' })).toBeTruthy()
  expect(screen.getByText(/Choose a listed race/)).toBeTruthy()
  expect(loadReplayIndex).not.toHaveBeenCalled()
})

test('loads a valid year-only URL without showing a selection error', async () => {
  window.history.replaceState(null, '', '/?trajectory=linear&year=2024')
  render(<App />)

  expect(await screen.findByRole('heading', { name: 'Race Replay Library' })).toBeTruthy()
  expect(screen.queryByRole('alert', { name: 'Replay Selection Unavailable' })).toBeNull()
  expect(screen.getByRole('button', { name: /Bahrain Grand Prix/ })).toBeTruthy()
  expect(screen.queryByRole('combobox', { name: 'Change circuit' })).toBeNull()
  expect(window.location.search).toBe('?trajectory=linear&year=2024')
})

test('change session returns to the selected race, preserves unrelated query values, and disposes replay', async () => {
  window.history.replaceState(null, '', '/?trajectory=linear&year=2024&race=race-1&session=r')
  const controller = createController()
  vi.mocked(createReplayController).mockReturnValue(controller)
  render(<App />)

  await screen.findByRole('group', { name: 'Race status timeline' })
  fireEvent.click(screen.getByRole('button', { name: 'Change session' }))

  expect(await screen.findByRole('heading', { name: 'Bahrain Grand Prix' })).toBeTruthy()
  expect(window.location.search).toBe('?trajectory=linear')
  expect(screen.getByRole('combobox', { name: 'Change circuit' })).toBeTruthy()
  expect(screen.getByRole('radio', { name: /Race/ }).getAttribute('aria-checked')).toBe('true')
  expect(controller.dispose).toHaveBeenCalledOnce()
})

test('switches screens on browser navigation while retaining StrictMode stale-load protection', async () => {
  const firstLoad = createDeferred<ReplayIndex>()
  const activeLoad = createDeferred<ReplayIndex>()
  const activeController = createController()
  window.history.replaceState(null, '', '/?year=2024&race=race-1&session=r')
  vi.mocked(loadReplayIndex).mockReturnValueOnce(firstLoad.promise).mockReturnValueOnce(activeLoad.promise)
  vi.mocked(createReplayController).mockReturnValue(activeController)

  const { unmount } = render(<StrictMode><App /></StrictMode>)
  await waitFor(() => expect(loadReplayIndex).toHaveBeenCalledTimes(2))

  await act(async () => { firstLoad.resolve(index) })
  expect(createReplayController).not.toHaveBeenCalled()
  await act(async () => { activeLoad.resolve(index) })
  expect(await screen.findByRole('group', { name: 'Race status timeline' })).toBeTruthy()

  act(() => {
    window.history.pushState(null, '', '/?trajectory=linear')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  expect(await screen.findByRole('heading', { name: 'Bahrain Grand Prix' })).toBeTruthy()
  expect(activeController.dispose).toHaveBeenCalledOnce()
  unmount()
})

test('uses directional transitions for browser back and forward navigation', async () => {
  const directions: string[] = []
  Object.defineProperty(document, 'startViewTransition', {
    configurable: true,
    value: (update: () => void) => {
      directions.push(document.documentElement.dataset.pageTransitionDirection ?? '')
      update()
      return {
        finished: Promise.resolve(),
        ready: Promise.resolve(),
        skipTransition: vi.fn(),
        types: new Set<string>(),
        updateCallbackDone: Promise.resolve(),
      } as unknown as ViewTransition
    },
  })
  window.history.replaceState(null, '', '/?year=2024&race=race-1&session=r')
  render(<App />)
  expect(await screen.findByRole('group', { name: 'Race status timeline' })).toBeTruthy()

  act(() => {
    window.history.pushState(null, '', '/')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  expect(await screen.findByRole('heading', { name: 'Bahrain Grand Prix' })).toBeTruthy()

  act(() => {
    window.history.pushState(null, '', '/?year=2024&race=race-1&session=r')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  expect(await screen.findByRole('group', { name: 'Race status timeline' })).toBeTruthy()
  expect(directions).toEqual(['backward', 'forward'])
})
