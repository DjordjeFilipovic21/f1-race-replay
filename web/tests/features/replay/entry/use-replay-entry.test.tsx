/**
 * @vitest-environment jsdom
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, expect, test, vi } from 'vitest'
import { useReplayEntry } from '../../../../src/features/replay/entry/useReplayEntry'
import { loadReplayIndex } from '../../../../src/data/replay/loader'
import { createFetchSource } from '../../../../src/data/replay/source'
import { createReplayController, type ReplayController, type ReplayControllerSnapshot } from '../../../../src/engine/replay'
import type { ReplayIndex, ReplaySource, TelemetryCapabilities, TrackAssets, WeatherSidecar } from '../../../../src/data/replay/types'

vi.mock('../../../../src/data/replay/loader', () => ({ loadReplayIndex: vi.fn() }))
vi.mock('../../../../src/data/replay/source', () => ({ createFetchSource: vi.fn() }))
vi.mock('../../../../src/engine/replay', () => ({ createReplayController: vi.fn() }))

const source: ReplaySource = { read: vi.fn(async () => new Uint8Array()) }
const trackAssets: TrackAssets = {
  contractVersion: 'v1', fixtureId: 'test-race', trackId: 'test-track', trackName: 'Test Track',
  coordinateSpace: { units: 'meters', origin: 'test origin' }, circuitLengthMeters: 1000, rotationDegrees: 0,
  startFinish: { center: { x: 0, y: 5 }, inner: { x: 0, y: 0 }, outer: { x: 0, y: 10 } },
  centerLine: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }],
  innerBoundary: [{ x: 1, y: 1 }, { x: 9, y: 1 }, { x: 9, y: 9 }, { x: 1, y: 9 }],
  outerBoundary: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }],
}
const index = { manifest: { chunks: [{ startMs: 0, endMs: 3000 }], drivers: [] }, trackAssets } as unknown as ReplayIndex
const telemetryCapabilities: TelemetryCapabilities = {
  drs: 'not-published', overtakeMode: 'not-published', activeAero: 'not-published', ersReplacement: 'not-published',
}

function createController(): ReplayController {
  const snapshot: ReplayControllerSnapshot = { status: 'loading', timeMs: 0, speed: 1, isPlaying: false, replay: null, crossedEvents: [], error: null }
  return { getSnapshot: () => snapshot, subscribe: () => () => undefined, start: vi.fn(), pause: vi.fn(), seek: vi.fn(), setSpeed: vi.fn(), retry: vi.fn(async () => undefined), dispose: vi.fn() }
}

function ReplayEntryProbe({ browserBaseUrl, browserPointerPath }: { readonly browserBaseUrl: string; readonly browserPointerPath: string }) {
  const { replay, error, retry } = useReplayEntry({ browserBaseUrl, browserPointerPath })
  return (
    <>
      <output data-testid="state">{replay === null ? (error === null ? 'loading' : 'error') : 'ready'}</output>
      <output data-testid="season-metadata">{replay?.seasonMetadata?.year ?? 'absent'}</output>
      <output data-testid="telemetry-capabilities">{replay?.telemetryCapabilities?.drs ?? 'absent'}</output>
      <output data-testid="sidecar">{replay?.weatherSidecar?.fixtureId ?? 'none'}</output>
      <button type="button" onClick={retry}>Retry</button>
    </>
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

test('loads a nested browser pointer without creating a controller from stale StrictMode work', async () => {
  let resolveFirst!: (value: ReplayIndex) => void
  let resolveActive!: (value: ReplayIndex) => void
  const firstLoad = new Promise<ReplayIndex>((resolve) => { resolveFirst = resolve })
  const activeLoad = new Promise<ReplayIndex>((resolve) => { resolveActive = resolve })
  const activeController = createController()
  vi.mocked(createFetchSource).mockReturnValue(source)
  vi.mocked(loadReplayIndex).mockReturnValueOnce(firstLoad).mockReturnValueOnce(activeLoad)
  vi.mocked(createReplayController).mockReturnValue(activeController)

  const { unmount } = render(<StrictMode><ReplayEntryProbe browserBaseUrl="/seasons/2024/brazil/" browserPointerPath="nested/browser-current.json" /></StrictMode>)
  expect(screen.getByTestId('state').textContent).toBe('loading')

  await act(async () => { resolveFirst(index) })
  expect(createReplayController).not.toHaveBeenCalled()
  await act(async () => { resolveActive(index) })
  expect(createFetchSource).toHaveBeenCalledWith('/seasons/2024/brazil/')
  expect(loadReplayIndex).toHaveBeenCalledWith({ source, pointerPath: 'nested/browser-current.json' })
  expect(createReplayController).toHaveBeenCalledWith({ index, coordinateInterpolation: 'smooth' })
  expect(screen.getByTestId('state').textContent).toBe('ready')
  expect(screen.getByTestId('season-metadata').textContent).toBe('absent')
  expect(screen.getByTestId('telemetry-capabilities').textContent).toBe('absent')
  expect(screen.getByTestId('sidecar').textContent).toBe('none')

  unmount()
  expect(activeController.dispose).toHaveBeenCalledOnce()
})

test('retains optional season metadata and telemetry capabilities from a new manifest', async () => {
  const metadataIndex = {
    ...index,
    seasonMetadata: { year: 2026 },
    telemetryCapabilities,
  } as ReplayIndex
  const controller = createController()
  vi.mocked(createFetchSource).mockReturnValue(source)
  vi.mocked(loadReplayIndex).mockResolvedValue(metadataIndex)
  vi.mocked(createReplayController).mockReturnValue(controller)

  render(<ReplayEntryProbe browserBaseUrl="/seasons/2026/" browserPointerPath="browser-current.json" />)
  await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('ready'))

  expect(screen.getByTestId('season-metadata').textContent).toBe('2026')
  expect(screen.getByTestId('telemetry-capabilities').textContent).toBe('not-published')
})

test('retains season metadata while leaving telemetry capabilities absent for a season-only manifest', async () => {
  const seasonOnlyIndex = { ...index, seasonMetadata: { year: 2025 } } as ReplayIndex
  const controller = createController()
  vi.mocked(createFetchSource).mockReturnValue(source)
  vi.mocked(loadReplayIndex).mockResolvedValue(seasonOnlyIndex)
  vi.mocked(createReplayController).mockReturnValue(controller)

  render(<ReplayEntryProbe browserBaseUrl="/seasons/2024/" browserPointerPath="browser-current.json" />)
  await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('ready'))

  expect(screen.getByTestId('season-metadata').textContent).toBe('2025')
  expect(screen.getByTestId('telemetry-capabilities').textContent).toBe('absent')
})

test('returns an initialization error and retries the same race entry', async () => {
  const loadError = new Error('pointer unavailable')
  const controller = createController()
  vi.mocked(createFetchSource).mockReturnValue(source)
  vi.mocked(loadReplayIndex).mockRejectedValueOnce(loadError).mockResolvedValueOnce(index)
  vi.mocked(createReplayController).mockReturnValue(controller)

  render(<ReplayEntryProbe browserBaseUrl="/seasons/2024/" browserPointerPath="browser-current.json" />)
  await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('error'))

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry' })) })
  await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('ready'))
  expect(loadReplayIndex).toHaveBeenCalledTimes(2)
})

test('threads the optional weather sidecar from the index into the ready replay', async () => {
  const weatherSidecar = {
    contractVersion: 'v1',
    fixtureId: 'test-race',
    timeMs: [0],
    airTempC: [21.0],
    humidityPct: [60],
    pressureMbar: [1013.2],
    rainfall: [false],
    trackTempC: [30.5],
    windDirectionDeg: [180],
    windSpeedMps: [3.2],
  } as WeatherSidecar
  const weatherIndex = { ...index, weatherSidecar } as unknown as ReplayIndex
  const controller = createController()
  vi.mocked(createFetchSource).mockReturnValue(source)
  vi.mocked(loadReplayIndex).mockResolvedValueOnce(weatherIndex)
  vi.mocked(createReplayController).mockReturnValue(controller)

  render(<ReplayEntryProbe browserBaseUrl="/seasons/2024/" browserPointerPath="browser-current.json" />)
  await waitFor(() => expect(screen.getByTestId('state').textContent).toBe('ready'))

  expect(screen.getByTestId('sidecar').textContent).toBe('test-race')
})
