/**
 * @vitest-environment jsdom
 */
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { ReplayFpsIndicator } from '../src/replay-ui/ReplayFpsIndicator'
import type { ReplayController, ReplayControllerSnapshot } from '../src/replay-engine'

afterEach(cleanup)

test('reports controller update cadence while playing and resets when paused', () => {
  let frameAt = 0
  let snapshot: ReplayControllerSnapshot = { status: 'ready', timeMs: 0, speed: 1, isPlaying: true, replay: null, crossedEvents: [], error: null }
  const listeners = new Set<() => void>()
  const controller: ReplayController = {
    getSnapshot: () => snapshot,
    subscribe: (listener) => { listeners.add(listener); return () => listeners.delete(listener) },
    start: vi.fn(), pause: vi.fn(), seek: vi.fn(), setSpeed: vi.fn(), retry: vi.fn(async () => undefined), dispose: vi.fn(),
  }
  render(<ReplayFpsIndicator controller={controller} now={() => frameAt} />)

  act(() => {
    for (let frame = 1; frame <= 63; frame += 1) {
      frameAt = frame * 16
      snapshot = { ...snapshot, timeMs: frameAt }
      listeners.forEach((listener) => listener())
    }
  })
  expect(screen.getByLabelText('Replay frame rate').textContent).toBe('63 FPS · max 16 ms · 0 dropped')

  act(() => {
    snapshot = { ...snapshot, isPlaying: false }
    listeners.forEach((listener) => listener())
  })
  expect(screen.getByLabelText('Replay frame rate').textContent).toBe('— FPS')
})

test('does not count the play transition as a rendered frame', () => {
  let frameAt = 0
  let snapshot: ReplayControllerSnapshot = { status: 'ready', timeMs: 0, speed: 1, isPlaying: false, replay: null, crossedEvents: [], error: null }
  const listeners = new Set<() => void>()
  const controller: ReplayController = {
    getSnapshot: () => snapshot,
    subscribe: (listener) => { listeners.add(listener); return () => listeners.delete(listener) },
    start: vi.fn(), pause: vi.fn(), seek: vi.fn(), setSpeed: vi.fn(), retry: vi.fn(async () => undefined), dispose: vi.fn(),
  }
  render(<ReplayFpsIndicator controller={controller} now={() => frameAt} />)

  act(() => {
    snapshot = { ...snapshot, isPlaying: true }
    listeners.forEach((listener) => listener())
    for (let frame = 1; frame <= 24; frame += 1) {
      frameAt = frame * (1_000 / 24)
      snapshot = { ...snapshot, timeMs: Math.floor(frameAt) }
      listeners.forEach((listener) => listener())
    }
  })

  expect(screen.getByLabelText('Replay frame rate').textContent).toContain('24 FPS')
})
