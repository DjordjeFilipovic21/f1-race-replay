/**
 * @vitest-environment jsdom
 */
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { LiveTyreStrategyPanel } from '../../../../src/features/replay/panels/LiveTyreStrategyPanel'
import type { ReplayController, ReplayControllerSnapshot } from '../../../../src/engine/replay'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'
import type { DriverMetadata, StintSummary } from '../../../../src/data/replay/types'

const drivers: readonly DriverMetadata[] = [
  { id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' },
]

function replay(sessionTimeMs: number, lap: number): ReplaySnapshot {
  return {
    sessionTimeMs,
    leaderboardOrder: ['VER'],
    trackStatusCode: null,
    weatherState: null,
    events: [],
    drivers: {
      VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 0, lap, position: 1, gear: null, drs: null, tyreCompound: 'SOFT', status: 'Running', isInPitLane: false },
    },
  }
}

function createStintSummary(): StintSummary {
  return {
    contractVersion: 'v2',
    fixtureId: 'fixture-1',
    drivers: {
      VER: {
        stintNumber: [1, 2],
        compound: ['SOFT', 'MEDIUM'],
        startLap: [1, 10],
        endLap: [9, null],
        startTimeMs: [0, 60_000],
        endTimeMs: [55_000, null],
        tyreLifeAtStart: [0, 0],
        isFreshTyre: [true, true],
        pitInTimeMs: [52_000, null],
        pitOutTimeMs: [58_000, null],
      },
    },
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
  const stintSummary = createStintSummary()
  const source = createController({ status: 'ready', timeMs: 0, speed: 1, isPlaying: true, replay: replay(0, 1), crossedEvents: [], error: null })

  render(
    <LiveTyreStrategyPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      stintSummary={stintSummary}
      totalLaps={20}
    />,
  )

  // Initially only stint 1 (SOFT) is visible because sessionTimeMs=0.
  expect(screen.getAllByText('Soft').length).toBeGreaterThanOrEqual(1)
  expect(screen.queryByText('Medium')).toBeNull()

  // Advance the controller past stint 2's startTimeMs, but the throttled
  // store should not have published the update yet.
  source.publish({ ...source.snapshot(), timeMs: 60_000, replay: replay(60_000, 10) })
  act(() => { vi.advanceTimersByTime(500) })
  expect(screen.queryByText('Medium')).toBeNull()

  act(() => { vi.advanceTimersByTime(499) })
  expect(screen.queryByText('Medium')).toBeNull()

  source.controller.dispose()
})

test('publishes playing snapshot changes at 1 second', () => {
  vi.useFakeTimers()
  const stintSummary = createStintSummary()
  const source = createController({ status: 'ready', timeMs: 0, speed: 1, isPlaying: true, replay: replay(0, 1), crossedEvents: [], error: null })

  render(
    <LiveTyreStrategyPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      stintSummary={stintSummary}
      totalLaps={20}
    />,
  )

  source.publish({ ...source.snapshot(), timeMs: 60_000, replay: replay(60_000, 10) })
  act(() => { vi.advanceTimersByTime(1_000) })

  // Stint 2 (MEDIUM) is now visible because the throttled store published.
  expect(screen.getAllByText('Medium').length).toBeGreaterThanOrEqual(1)

  source.controller.dispose()
})

test('flushes immediately when refreshKey changes', () => {
  vi.useFakeTimers()
  const stintSummary = createStintSummary()
  const source = createController({ status: 'ready', timeMs: 0, speed: 1, isPlaying: true, replay: replay(0, 1), crossedEvents: [], error: null })

  const { rerender } = render(
    <LiveTyreStrategyPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      stintSummary={stintSummary}
      totalLaps={20}
    />,
  )

  // Advance the controller — still within throttle window.
  source.publish({ ...source.snapshot(), timeMs: 60_000, replay: replay(60_000, 10) })
  expect(screen.queryByText('Medium')).toBeNull()

  // Simulate a seek-driven refreshKey bump — must flush immediately.
  rerender(
    <LiveTyreStrategyPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={1}
      selectedDriverId="VER"
      stintSummary={stintSummary}
      totalLaps={20}
    />,
  )
  expect(screen.getAllByText('Medium').length).toBeGreaterThanOrEqual(1)

  source.controller.dispose()
})

test('driver-selection changes render from the latest retained throttled snapshot', () => {
  vi.useFakeTimers()
  const stintSummary = createStintSummary()
  const source = createController({ status: 'ready', timeMs: 60_000, speed: 1, isPlaying: true, replay: replay(60_000, 10), crossedEvents: [], error: null })

  const { rerender } = render(
    <LiveTyreStrategyPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId={null}
      stintSummary={stintSummary}
      totalLaps={20}
    />,
  )

  // No driver selected → empty state.
  expect(screen.getByText(/Tyre strategy is unavailable/i)).toBeTruthy()

  // Select a driver — should render immediately using the retained snapshot,
  // regardless of the throttle window.
  rerender(
    <LiveTyreStrategyPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      stintSummary={stintSummary}
      totalLaps={20}
    />,
  )
  expect(screen.getAllByText('Soft').length).toBeGreaterThanOrEqual(1)
  expect(screen.getAllByText('Medium').length).toBeGreaterThanOrEqual(1)

  source.controller.dispose()
})

test('labels practice stint data as Tyre runs without race claims', () => {
  const stintSummary = createStintSummary()
  const source = createController({ status: 'ready', timeMs: 60_000, speed: 1, isPlaying: false, replay: replay(60_000, 10), crossedEvents: [], error: null })

  render(
    <LiveTyreStrategyPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      stintSummary={stintSummary}
      totalLaps={20}
      sessionMode="practice"
    />,
  )

  expect(screen.getByRole('heading', { name: 'Tyre runs' })).toBeTruthy()
  expect(screen.queryByRole('heading', { name: 'Strategy' })).toBeNull()
  expect(screen.queryByText('Race distance timeline')).toBeNull()
  const timeline = document.querySelector('[aria-label*="distance timeline"], [aria-label*="stint timeline"]')
  expect(timeline?.getAttribute('aria-label')).toContain('Session')

  source.controller.dispose()
})

test('keeps race labels when the session mode is explicitly race', () => {
  const stintSummary = createStintSummary()
  const source = createController({ status: 'ready', timeMs: 60_000, speed: 1, isPlaying: false, replay: replay(60_000, 10), crossedEvents: [], error: null })

  render(
    <LiveTyreStrategyPanel
      controller={source.controller}
      drivers={drivers}
      refreshKey={0}
      selectedDriverId="VER"
      stintSummary={stintSummary}
      totalLaps={20}
      sessionMode="race"
    />,
  )

  expect(screen.getByRole('heading', { name: 'Strategy' })).toBeTruthy()
  expect(screen.queryByRole('heading', { name: 'Tyre runs' })).toBeNull()

  source.controller.dispose()
})
