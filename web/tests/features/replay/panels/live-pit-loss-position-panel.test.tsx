/**
 * @vitest-environment jsdom
 */
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { LivePitLossPositionPanel } from '../../../../src/features/replay/panels/LivePitLossPositionPanel'
import type { ReplayController, ReplayControllerSnapshot } from '../../../../src/engine/replay'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'
import type { DriverMetadata, PitLossEstimateSidecar, PitLossModel } from '../../../../src/data/replay/types'

const drivers: readonly DriverMetadata[] = [
  { id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' },
  { id: 'NOR', displayName: 'Lando Norris', teamName: 'McLaren', colorHex: '#ff8000', carNumber: '4' },
]

function replay(sessionTimeMs: number, lap: number, trackStatusCode: number | null = null): ReplaySnapshot {
  return {
    sessionTimeMs,
    leaderboardOrder: ['VER', 'NOR'],
    trackStatusCode,
    weatherState: null,
    events: [],
    drivers: {
      VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 0, lap, position: 1, gear: null, drs: null, tyreCompound: 'SOFT', status: 'Running', isInPitLane: false },
      NOR: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 2_000, lap, position: 2, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'Running', isInPitLane: false },
    },
  }
}

function createPitLossModel(): PitLossModel {
  return {
    contractVersion: 'v2',
    fixtureId: 'fixture-1',
    method: 'global-prior-weighted-mean-v1',
    baselineMs: 22_000,
    priorWeight: 2,
    timeMs: [0],
    estimatedLossMs: [22_000],
    observedSampleCount: [1],
  }
}

/**
 * Australia curated baseline entry: Green 19300 ms, VSC 12300 ms, SC 9300 ms.
 * Curated status timelines are single replay-start points without current-race
 * observedSampleCount; catalog audit metadata is not part of the browser payload.
 */
function createCuratedPitLossEstimateSidecar(): PitLossEstimateSidecar {
  return {
    contractVersion: 'v2',
    fixtureId: 'fixture-1',
    trackId: 'track-1',
    method: 'curated-track-baseline-v1',
    race: { timeMs: [0], estimatedLossMs: [19_300] },
    safetyCar: { timeMs: [0], estimatedLossMs: [9_300] },
    virtualSafetyCar: { timeMs: [0], estimatedLossMs: [12_300] },
  }
}

function createController(initial: ReplayControllerSnapshot) {
  let snapshot = initial
  const listeners = new Set<() => void>()
  const controller: ReplayController = {
    getSnapshot: () => snapshot,
    subscribe: (listener) => { listeners.add(listener); return () => listeners.delete(listener) },
    start: vi.fn(), pause: vi.fn(), seek: vi.fn(), setSpeed: vi.fn(), retry: vi.fn(async () => undefined), dispose: vi.fn(),
  }
  return {
    controller,
    publish: (next: ReplayControllerSnapshot) => act(() => {
      snapshot = next
      listeners.forEach((listener) => listener())
    }),
    snapshot: () => snapshot,
  }
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

test('does not publish playing snapshot changes before 1 second', () => {
  vi.useFakeTimers()
  const pitLossModel = createPitLossModel()
  const source = createController({ status: 'ready', timeMs: 0, speed: 1, isPlaying: true, replay: replay(0, 1), crossedEvents: [], error: null })

  render(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      pitLossModel={pitLossModel}
    />,
  )

  // Initially shows pit-loss estimate
  expect(screen.getByText('Pit loss position')).toBeTruthy()
  expect(screen.getByText('Pit-loss estimate')).toBeTruthy()
  expect(screen.getByText('+22.000s')).toBeTruthy()

  // Advance the controller, but the throttled store should not have published yet
  source.publish({ ...source.snapshot(), timeMs: 60_000, replay: replay(60_000, 10) })
  act(() => { vi.advanceTimersByTime(500) })

  // Still shows the same estimate (no update yet)
  expect(screen.getByText('+22.000s')).toBeTruthy()

  act(() => { vi.advanceTimersByTime(499) })
  expect(screen.getByText('+22.000s')).toBeTruthy()

  source.controller.dispose()
})

test('publishes playing snapshot changes at 1 second', () => {
  vi.useFakeTimers()
  const pitLossModel = createPitLossModel()
  const source = createController({ status: 'ready', timeMs: 0, speed: 1, isPlaying: true, replay: replay(0, 1), crossedEvents: [], error: null })

  render(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      pitLossModel={pitLossModel}
    />,
  )

  source.publish({ ...source.snapshot(), timeMs: 60_000, replay: replay(60_000, 10) })
  act(() => { vi.advanceTimersByTime(1_000) })

  // Panel is still rendered after throttle period
  expect(screen.getByText('Pit loss position')).toBeTruthy()
  expect(screen.getByText('Pit-loss estimate')).toBeTruthy()

  source.controller.dispose()
})

test('flushes immediately when refreshKey changes', () => {
  vi.useFakeTimers()
  const pitLossModel = createPitLossModel()
  const source = createController({ status: 'ready', timeMs: 0, speed: 1, isPlaying: true, replay: replay(0, 1), crossedEvents: [], error: null })

  const { rerender } = render(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      pitLossModel={pitLossModel}
    />,
  )

  // Advance the controller — still within throttle window
  source.publish({ ...source.snapshot(), timeMs: 60_000, replay: replay(60_000, 10) })
  expect(screen.getByText('+22.000s')).toBeTruthy()

  // Simulate a seek-driven refreshKey bump — must flush immediately
  rerender(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={1}
      selectedDriverId="VER"
      pitLossModel={pitLossModel}
    />,
  )

  // Panel is still rendered after flush
  expect(screen.getByText('Pit loss position')).toBeTruthy()
  expect(screen.getByText('Pit-loss estimate')).toBeTruthy()

  source.controller.dispose()
})

test('driver-selection changes render from the latest retained throttled snapshot', () => {
  vi.useFakeTimers()
  const pitLossModel = createPitLossModel()
  const source = createController({ status: 'ready', timeMs: 60_000, speed: 1, isPlaying: true, replay: replay(60_000, 10), crossedEvents: [], error: null })

  const { rerender } = render(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId={null}
      pitLossModel={pitLossModel}
    />,
  )

  // No driver selected → empty state
  expect(screen.getByText(/Pit loss position is unavailable/i)).toBeTruthy()

  // Select a driver — should render immediately using the retained snapshot
  rerender(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      pitLossModel={pitLossModel}
    />,
  )

  expect(screen.getByText('Pit loss position')).toBeTruthy()
  expect(screen.getByText('Pit-loss estimate')).toBeTruthy()
  expect(screen.getByText('+22.000s')).toBeTruthy()

  source.controller.dispose()
})

test('renders after pit comparison when pit-loss data is available', () => {
  const pitLossModel = createPitLossModel()
  const source = createController({ status: 'ready', timeMs: 120_000, speed: 1, isPlaying: false, replay: replay(120_000, 10), crossedEvents: [], error: null })

  render(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      pitLossModel={pitLossModel}
    />,
  )

  expect(screen.getByText('Pit loss position')).toBeTruthy()
  expect(screen.getByText('Pit-loss estimate')).toBeTruthy()
  expect(screen.getByText('+22.000s')).toBeTruthy()
  expect(screen.getByText('After pit comparison')).toBeTruthy()

  source.controller.dispose()
})

test('renders after pit comparison as unavailable when no pit-loss model', () => {
  const source = createController({ status: 'ready', timeMs: 120_000, speed: 1, isPlaying: false, replay: replay(120_000, 10), crossedEvents: [], error: null })

  render(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      pitLossModel={null}
    />,
  )

  expect(screen.getByText('Pit loss position')).toBeTruthy()
  expect(screen.getByText('Pit-loss estimate')).toBeTruthy()

  // Pit-loss estimate shows unavailable
  const pitLossCard = screen.getByText('Pit-loss estimate').closest('.pit-loss-position-panel__summary-cell')
  expect(pitLossCard).not.toBeNull()
  expect(pitLossCard!.textContent).toContain('Unavailable')

  // After pit comparison also shows unavailable
  const rejoinLabel = screen.getByText('After pit comparison')
  expect(rejoinLabel).toBeTruthy()
  const rejoinCard = rejoinLabel.closest('.pit-loss-position-panel__summary-cell')
  expect(rejoinCard).not.toBeNull()
  expect(rejoinCard!.textContent).toContain('Unavailable')

  source.controller.dispose()
})

test('renders curated Green catalog value at replay start without a legacy model', () => {
  const source = createController({ status: 'ready', timeMs: 0, speed: 1, isPlaying: false, replay: replay(0, 1, 1), crossedEvents: [], error: null })

  render(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      pitLossModel={null}
      pitLossEstimateSidecar={createCuratedPitLossEstimateSidecar()}
    />,
  )

  // Catalog-backed values are available from replay start without current-race samples
  expect(screen.getByText('+19.300s')).toBeTruthy()
  expect(screen.getByText('Green Flag value')).toBeTruthy()
  expect(screen.queryByText(/Baseline/)).toBeNull()

  source.controller.dispose()
})

test('switches curated catalog values as the track status changes across the cursor', () => {
  vi.useFakeTimers()
  const source = createController({ status: 'ready', timeMs: 0, speed: 1, isPlaying: true, replay: replay(0, 1, 1), crossedEvents: [], error: null })

  render(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      pitLossModel={null}
      pitLossEstimateSidecar={createCuratedPitLossEstimateSidecar()}
    />,
  )

  // Green at replay start
  expect(screen.getByText('+19.300s')).toBeTruthy()

  // Safety Car at a later cursor
  source.publish({ ...source.snapshot(), timeMs: 600_000, replay: replay(600_000, 45, 4) })
  act(() => { vi.advanceTimersByTime(1_000) })
  expect(screen.getByText('+9.300s')).toBeTruthy()

  // VSC at a later cursor
  source.publish({ ...source.snapshot(), timeMs: 900_000, replay: replay(900_000, 50, 6) })
  act(() => { vi.advanceTimersByTime(1_000) })
  expect(screen.getByText('+12.300s')).toBeTruthy()

  // Back to Green
  source.publish({ ...source.snapshot(), timeMs: 1_200_000, replay: replay(1_200_000, 55, 1) })
  act(() => { vi.advanceTimersByTime(1_000) })
  expect(screen.getByText('+19.300s')).toBeTruthy()
  expect(screen.queryByText(/Baseline/)).toBeNull()

  source.controller.dispose()
})

test('fails closed for an unknown status with a curated sidecar and no legacy model', () => {
  vi.useFakeTimers()
  const source = createController({ status: 'ready', timeMs: 0, speed: 1, isPlaying: true, replay: replay(0, 1, 1), crossedEvents: [], error: null })

  render(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      pitLossModel={null}
      pitLossEstimateSidecar={createCuratedPitLossEstimateSidecar()}
    />,
  )

  expect(screen.getByText('+19.300s')).toBeTruthy()

  // Unknown track status at a later cursor
  source.publish({ ...source.snapshot(), timeMs: 120_000, replay: replay(120_000, 10, 99) })
  act(() => { vi.advanceTimersByTime(1_000) })

  // Explicit unavailable, never a value or the old 22000 ms baseline
  const pitLossCard = screen.getByText('Pit-loss estimate').closest('.pit-loss-position-panel__summary-cell')
  expect(pitLossCard).not.toBeNull()
  expect(pitLossCard!.textContent).toContain('—')
  expect(pitLossCard!.textContent).toContain('Unavailable')
  expect(screen.queryByText('+22.000s')).toBeNull()
  expect(screen.queryByText(/Baseline/)).toBeNull()

  source.controller.dispose()
})

test('never renders catalog-only source status metadata', () => {
  const sidecar = createCuratedPitLossEstimateSidecar() as unknown as Record<string, unknown>
  sidecar.sourceStatus = 'official'
  ;(sidecar.race as Record<string, unknown>).sourceStatus = 'measured'
  const source = createController({ status: 'ready', timeMs: 0, speed: 1, isPlaying: false, replay: replay(0, 1, 1), crossedEvents: [], error: null })

  render(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      pitLossModel={null}
      pitLossEstimateSidecar={sidecar as unknown as PitLossEstimateSidecar}
    />,
  )

  // The catalog-only field name and its values never reach the browser DOM
  expect(screen.queryByText(/sourceStatus/i)).toBeNull()
  expect(document.body.textContent).not.toContain('sourceStatus')
  expect(document.body.textContent).not.toContain('measured')

  source.controller.dispose()
})

test('renders only the selected curated value, never catalog audit metadata', () => {
  const source = createController({ status: 'ready', timeMs: 0, speed: 1, isPlaying: false, replay: replay(0, 1, 1), crossedEvents: [], error: null })

  render(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      pitLossModel={null}
      pitLossEstimateSidecar={createCuratedPitLossEstimateSidecar()}
    />,
  )

  const pitLossCard = screen.getByText('Pit-loss estimate').closest('.pit-loss-position-panel__summary-cell')
  expect(pitLossCard).not.toBeNull()
  expect(pitLossCard!.textContent).toContain('Green Flag value')
  expect(pitLossCard!.textContent).not.toContain('Catalog evidence')
  expect(pitLossCard!.textContent).not.toContain('Confidence')
  expect(pitLossCard!.textContent).not.toContain('sample')

  source.controller.dispose()
})

test('labels legacy sidecar observations as current-race samples, never catalog evidence', () => {
  const sidecar: PitLossEstimateSidecar = {
    contractVersion: 'v2',
    fixtureId: 'fixture-1',
    trackId: 'track-1',
    method: 'track-status-median-v1',
    race: { timeMs: [0, 100], estimatedLossMs: [22_000, 21_500], observedSampleCount: [0, 4] },
  }
  const source = createController({ status: 'ready', timeMs: 120_000, speed: 1, isPlaying: false, replay: replay(120_000, 10, 1), crossedEvents: [], error: null })

  render(
    <LivePitLossPositionPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      pitLossModel={null}
      pitLossEstimateSidecar={sidecar}
    />,
  )

  // Current-race observations are labelled as samples, not catalog metadata
  const pitLossCard = screen.getByText('Pit-loss estimate').closest('.pit-loss-position-panel__summary-cell')
  expect(pitLossCard).not.toBeNull()
  expect(pitLossCard!.textContent).toContain('4 samples')
  expect(pitLossCard!.textContent).not.toContain('Catalog evidence')
  expect(pitLossCard!.textContent).not.toContain('calibration')
  expect(screen.queryByText(/Baseline/)).toBeNull()

  source.controller.dispose()
})
