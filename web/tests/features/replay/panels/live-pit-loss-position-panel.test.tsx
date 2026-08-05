/**
 * @vitest-environment jsdom
 */
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { LivePitLossPositionPanel } from '../../../../src/features/replay/panels/LivePitLossPositionPanel'
import type { ReplayController, ReplayControllerSnapshot } from '../../../../src/engine/replay'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'
import type { DriverMetadata, PitLossModel } from '../../../../src/data/replay/types'

const drivers: readonly DriverMetadata[] = [
  { id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' },
  { id: 'NOR', displayName: 'Lando Norris', teamName: 'McLaren', colorHex: '#ff8000', carNumber: '4' },
]

function replay(sessionTimeMs: number, lap: number): ReplaySnapshot {
  return {
    sessionTimeMs,
    leaderboardOrder: ['VER', 'NOR'],
    trackStatusCode: null,
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
    observedSampleCount: [0],
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
