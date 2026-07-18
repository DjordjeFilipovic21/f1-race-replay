/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { ReplayControls } from '../src/replay-ui/ReplayControls'
import type { ReplayController, ReplayControllerSnapshot } from '../src/replay-engine'

const drivers = [{ id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' }]
const trackAssets = {
  contractVersion: 'v1', fixtureId: 'test-grand-prix', trackId: 'test-circuit', trackName: 'Test Circuit',
  coordinateSpace: { units: 'meters', origin: 'test' }, circuitLengthMeters: 1000, rotationDegrees: 0,
  startFinish: { center: { x: 0, y: 5 }, inner: { x: 0, y: 0 }, outer: { x: 0, y: 10 } },
  centerLine: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }],
  innerBoundary: [{ x: 1, y: 1 }, { x: 9, y: 1 }, { x: 9, y: 9 }, { x: 1, y: 9 }],
  outerBoundary: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }],
} as const

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

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

test('wires accessible playback, seek, and speed controls to the controller', async () => {
  const user = userEvent.setup()
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  await user.click(screen.getByRole('button', { name: 'Play' }))
  const slider = screen.getByRole('slider', { name: 'Seek replay' })
  expect(screen.getByRole('group', { name: 'Test Circuit live track map' })).toBeTruthy()
  fireEvent.input(slider, { target: { value: '1501' } })
  expect(controller.seek).not.toHaveBeenCalled()
  fireEvent.pointerUp(slider)
  await user.selectOptions(screen.getByRole('combobox', { name: 'Playback speed' }), '2')

  expect(controller.start).toHaveBeenCalledOnce()
  expect(controller.seek).toHaveBeenCalledWith(1501)
  expect(controller.setSpeed).toHaveBeenCalledWith(2)
  expect(screen.getByRole('status', { name: 'Replay status' }).textContent).toContain('ready')
})

test('shows zero-based replay times while seeking with absolute session times', () => {
  const { controller } = createController({ ...readySnapshot, timeMs: 11_500 })
  render(<ReplayControls controller={controller} startMs={10_000} endMs={13_000} drivers={drivers} trackAssets={trackAssets} />)

  const slider = screen.getByRole('slider', { name: 'Seek replay' }) as HTMLInputElement
  expect(screen.getByLabelText('Replay time').textContent).toBe('0:01.500 / 0:03.000')
  expect(slider.min).toBe('10000')
  expect(slider.max).toBe('13000')
  expect(slider.value).toBe('11500')
  expect(slider.getAttribute('aria-valuetext')).toBe('0:01.500')

  fireEvent.input(slider, { target: { value: '11501' } })
  expect(controller.seek).not.toHaveBeenCalled()
  fireEvent.pointerUp(slider)
  expect(controller.seek).toHaveBeenCalledWith(11501)
})

test('previews rapid scrubbing locally and commits only the final value', () => {
  const { controller } = createController({ ...readySnapshot, timeMs: 10_500 })
  render(<ReplayControls controller={controller} startMs={10_000} endMs={13_000} drivers={drivers} trackAssets={trackAssets} />)
  const slider = screen.getByRole('slider', { name: 'Seek replay' }) as HTMLInputElement

  fireEvent.input(slider, { target: { value: '11000' } })
  fireEvent.input(slider, { target: { value: '12000' } })
  fireEvent.input(slider, { target: { value: '12900' } })

  expect(controller.seek).not.toHaveBeenCalled()
  expect(screen.getByLabelText('Replay time').textContent).toBe('0:02.900 / 0:03.000')
  expect(slider.getAttribute('aria-valuetext')).toBe('0:02.900')
  fireEvent.pointerUp(slider)
  fireEvent.blur(slider)
  expect(controller.seek).toHaveBeenCalledOnce()
  expect(controller.seek).toHaveBeenCalledWith(12_900)
  expect(slider.value).toBe('10500')
  expect(slider.getAttribute('aria-valuetext')).toBe('0:00.500')
})

test.each([
  ['keyboard release', (slider: HTMLInputElement) => fireEvent.keyUp(slider, { key: 'ArrowRight' })],
  ['blur', (slider: HTMLInputElement) => fireEvent.blur(slider)],
])('commits the final absolute seek value on %s', (_label, commit) => {
  const { controller } = createController({ ...readySnapshot, timeMs: 10_500 })
  render(<ReplayControls controller={controller} startMs={10_000} endMs={13_000} drivers={drivers} trackAssets={trackAssets} />)
  const slider = screen.getByRole('slider', { name: 'Seek replay' }) as HTMLInputElement

  fireEvent.input(slider, { target: { value: '12250' } })
  expect(controller.seek).not.toHaveBeenCalled()
  expect(slider.getAttribute('aria-valuetext')).toBe('0:02.250')
  commit(slider)

  expect(controller.seek).toHaveBeenCalledOnce()
  expect(controller.seek).toHaveBeenCalledWith(12_250)
})

test('clamps before-start and after-end snapshots without changing absolute slider bounds', () => {
  // Arrange: snapshots fall outside an absolute 10,000ms–13,000ms session range.
  const beforeStart = createController({ ...readySnapshot, timeMs: 9_000 })
  const afterEnd = createController({ ...readySnapshot, timeMs: 14_000 })
  const { rerender } = render(<ReplayControls controller={beforeStart.controller} startMs={10_000} endMs={13_000} drivers={drivers} trackAssets={trackAssets} />)

  // Act: render the before-start snapshot, then replace it with the after-end snapshot.
  const beforeStartSlider = screen.getByRole('slider', { name: 'Seek replay' }) as HTMLInputElement
  const beforeStartValues = {
    output: screen.getByLabelText('Replay time').textContent,
    ariaValueText: beforeStartSlider.getAttribute('aria-valuetext'),
    min: beforeStartSlider.min,
    max: beforeStartSlider.max,
  }
  rerender(<ReplayControls controller={afterEnd.controller} startMs={10_000} endMs={13_000} drivers={drivers} trackAssets={trackAssets} />)
  const afterEndSlider = screen.getByRole('slider', { name: 'Seek replay' }) as HTMLInputElement
  const afterEndValues = {
    output: screen.getByLabelText('Replay time').textContent,
    ariaValueText: afterEndSlider.getAttribute('aria-valuetext'),
    min: afterEndSlider.min,
    max: afterEndSlider.max,
  }

  // Assert: presentation clamps elapsed time while the native range retains absolute session bounds.
  expect(beforeStartValues).toEqual({
    output: '0:00.000 / 0:03.000', ariaValueText: '0:00.000', min: '10000', max: '13000',
  })
  expect(afterEndValues).toEqual({
    output: '0:03.000 / 0:03.000', ariaValueText: '0:03.000', min: '10000', max: '13000',
  })
})

test('shows loading and error diagnostics and retries controller loading', async () => {
  const user = userEvent.setup()
  const loading = createController({ ...readySnapshot, status: 'loading', replay: null })
  const { rerender } = render(<ReplayControls controller={loading.controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)
  expect(screen.getByRole('status', { name: 'Replay loading' }).textContent).toContain('Loading')

  const failed = createController({ ...readySnapshot, status: 'error', replay: null, error: new Error('network unavailable') })
  rerender(<ReplayControls controller={failed.controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)
  await user.click(screen.getByRole('button', { name: 'Retry loading' }))

  expect(screen.getByRole('alert').textContent).toContain('network unavailable')
  expect(failed.controller.retry).toHaveBeenCalledOnce()
})

test('keeps Pause available while requested playback is loading or has failed', async () => {
  const user = userEvent.setup()
  const loading = createController({ ...readySnapshot, status: 'loading', replay: null, isPlaying: true })
  const { rerender } = render(<ReplayControls controller={loading.controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  const loadingPause = screen.getByRole('button', { name: 'Pause' }) as HTMLButtonElement
  expect(loadingPause.disabled).toBe(false)
  await user.click(loadingPause)
  expect(loading.controller.pause).toHaveBeenCalledOnce()

  const failed = createController({ ...readySnapshot, status: 'error', replay: null, error: new Error('network unavailable'), isPlaying: true })
  rerender(<ReplayControls controller={failed.controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)
  const failedPause = screen.getByRole('button', { name: 'Pause' }) as HTMLButtonElement
  expect(failedPause.disabled).toBe(false)
  await user.click(failedPause)
  expect(failed.controller.pause).toHaveBeenCalledOnce()
})

test('unsubscribes when the adapter unmounts', () => {
  const { controller, getUnsubscribeCalls } = createController(readySnapshot)
  const { unmount } = render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)
  unmount()
  expect(controller.subscribe).toHaveBeenCalledTimes(4)
  expect(getUnsubscribeCalls()).toBe(4)
})
