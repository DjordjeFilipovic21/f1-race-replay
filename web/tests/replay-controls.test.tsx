/**
 * @vitest-environment jsdom
 */
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { ReplayControls } from '../src/replay-ui/ReplayControls'
import type { ReplayController, ReplayControllerSnapshot } from '../src/replay-engine'

function createController(snapshot: ReplayControllerSnapshot) {
  let current = snapshot
  const listeners = new Set<() => void>()
  let unsubscribeCalls = 0
  const controller: ReplayController = {
    getSnapshot: () => current,
    subscribe: vi.fn((listener: () => void) => {
      listeners.add(listener)
      return () => {
        unsubscribeCalls += 1
        listeners.delete(listener)
      }
    }),
    start: vi.fn(), pause: vi.fn(), seek: vi.fn(), setSpeed: vi.fn(), retry: vi.fn(async () => undefined), dispose: vi.fn(),
  }
  return {
    controller,
    listeners,
    getUnsubscribeCalls: () => unsubscribeCalls,
    setSnapshot: (next: ReplayControllerSnapshot) => { current = next; listeners.forEach((listener) => listener()) },
  }
}

const readySnapshot: ReplayControllerSnapshot = {
  status: 'ready', timeMs: 1500, speed: 1, isPlaying: false, crossedEvents: [], error: null,
  replay: { sessionTimeMs: 1500, leaderboardOrder: null, trackStatusCode: null, weatherState: null, events: [], drivers: { VER: { x: null, y: null, trackDistanceMeters: null, speed: 246.4, throttle: null, brake: null, gapToLeaderMs: null, lap: null, position: 1, gear: 7, drs: null, tyreCompound: null, status: null, isInPitLane: null } } },
}

afterEach(() => vi.restoreAllMocks())

test('wires accessible playback, seek, and speed controls to the controller', async () => {
  const user = userEvent.setup()
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} />)

  await user.click(screen.getByRole('button', { name: 'Play' }))
  const slider = screen.getByRole('slider', { name: 'Seek replay' })
  fireEvent.input(slider, { target: { value: '1501' } })
  await user.selectOptions(screen.getByRole('combobox', { name: 'Playback speed' }), '2')

  expect(controller.start).toHaveBeenCalledOnce()
  expect(controller.seek).toHaveBeenCalledWith(1501)
  expect(controller.setSpeed).toHaveBeenCalledWith(2)
  expect(screen.getByRole('status', { name: 'Replay status' }).textContent).toContain('ready')
})

test('shows loading and error diagnostics and retries controller loading', async () => {
  const user = userEvent.setup()
  const loading = createController({ ...readySnapshot, status: 'loading', replay: null })
  const { rerender } = render(<ReplayControls controller={loading.controller} startMs={0} endMs={3000} />)
  expect(screen.getByRole('status', { name: 'Replay loading' }).textContent).toContain('Loading')

  const failed = createController({ ...readySnapshot, status: 'error', replay: null, error: new Error('network unavailable') })
  rerender(<ReplayControls controller={failed.controller} startMs={0} endMs={3000} />)
  await user.click(screen.getByRole('button', { name: 'Retry loading' }))

  expect(screen.getByRole('alert').textContent).toContain('network unavailable')
  expect(failed.controller.retry).toHaveBeenCalledOnce()
})

test('keeps Pause available while requested playback is loading or has failed', async () => {
  const user = userEvent.setup()
  const loading = createController({ ...readySnapshot, status: 'loading', replay: null, isPlaying: true })
  const { rerender } = render(<ReplayControls controller={loading.controller} startMs={0} endMs={3000} />)

  const loadingPause = screen.getByRole('button', { name: 'Pause' }) as HTMLButtonElement
  expect(loadingPause.disabled).toBe(false)
  await user.click(loadingPause)
  expect(loading.controller.pause).toHaveBeenCalledOnce()

  const failed = createController({ ...readySnapshot, status: 'error', replay: null, error: new Error('network unavailable'), isPlaying: true })
  rerender(<ReplayControls controller={failed.controller} startMs={0} endMs={3000} />)
  const failedPause = screen.getByRole('button', { name: 'Pause' }) as HTMLButtonElement
  expect(failedPause.disabled).toBe(false)
  await user.click(failedPause)
  expect(failed.controller.pause).toHaveBeenCalledOnce()
})

test('unsubscribes when the adapter unmounts', () => {
  const { controller, getUnsubscribeCalls } = createController(readySnapshot)
  const { unmount } = render(<ReplayControls controller={controller} startMs={0} endMs={3000} />)
  unmount()
  expect(controller.subscribe).toHaveBeenCalledOnce()
  expect(getUnsubscribeCalls()).toBe(1)
})
