/**
 * @vitest-environment jsdom
 */
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { LiveLeaderboardPanel } from '../src/replay-ui/LiveLeaderboardPanel'
import type { ReplayController, ReplayControllerSnapshot } from '../src/replay-engine'
import type { ReplaySnapshot } from '../src/replay-engine/types'

const drivers = [{ id: 'NOR', displayName: 'Lando Norris', teamName: 'McLaren', colorHex: '#ff8000', carNumber: '4' }]

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

function replay(gapToLeaderMs: number): ReplaySnapshot {
  return {
    sessionTimeMs: 0, leaderboardOrder: ['NOR'], trackStatusCode: null, weatherState: null, events: [],
    drivers: { NOR: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs, lap: null, position: 2, gear: null, drs: null, tyreCompound: null, status: null, isInPitLane: null } },
  }
}

test('bounds playing table updates while publishing pause and explicit refresh immediately', () => {
  vi.useFakeTimers()
  let snapshot: ReplayControllerSnapshot = { status: 'ready', timeMs: 0, speed: 1, isPlaying: true, replay: replay(1_000), crossedEvents: [], error: null }
  const listeners = new Set<() => void>()
  const controller: ReplayController = {
    getSnapshot: () => snapshot,
    subscribe: (listener) => { listeners.add(listener); return () => listeners.delete(listener) },
    start: vi.fn(), pause: vi.fn(), seek: vi.fn(), setSpeed: vi.fn(), retry: vi.fn(async () => undefined), dispose: vi.fn(),
  }
  const publish = (timeMs: number, gapToLeaderMs: number, isPlaying = true) => act(() => {
    snapshot = { ...snapshot, timeMs, isPlaying, replay: replay(gapToLeaderMs) }
    listeners.forEach((listener) => listener())
  })
  const { rerender } = render(<LiveLeaderboardPanel controller={controller} drivers={drivers} refreshKey={0} />)

  publish(42, 2_000)
  publish(84, 3_000)
  expect(screen.getByText('+1.000')).toBeTruthy()
  publish(126, 4_000)
  act(() => vi.advanceTimersByTime(999))
  expect(screen.getByText('+1.000')).toBeTruthy()
  act(() => vi.advanceTimersByTime(1))
  expect(screen.getByText('+4.000')).toBeTruthy()
  act(() => {
    snapshot = { ...snapshot, status: 'loading', replay: null }
    listeners.forEach((listener) => listener())
  })
  expect(screen.getByText('+4.000')).toBeTruthy()
  snapshot = { ...snapshot, status: 'ready', replay: replay(4_000) }
  publish(127, 5_000, false)
  expect(screen.getByText('+5.000')).toBeTruthy()

  publish(128, 6_000)
  publish(129, 7_000)
  rerender(<LiveLeaderboardPanel controller={controller} drivers={drivers} refreshKey={1} />)
  expect(screen.getByText('+7.000')).toBeTruthy()
})
