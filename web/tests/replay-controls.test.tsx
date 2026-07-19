/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { parseElapsedParts, ReplayControls } from '../src/replay-ui/ReplayControls'
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
  expect(timeFieldValues()).toEqual(['0', '00', '01', '500'])
  expect(screen.getByLabelText('Replay time').textContent).toContain('/ 0:00:03.000')
  expect(slider.min).toBe('10000')
  expect(slider.max).toBe('13000')
  expect(slider.value).toBe('11500')
  expect(slider.getAttribute('aria-valuetext')).toBe('0:00:01.500')

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
  expect(timeFieldValues()).toEqual(['0', '00', '02', '900'])
  expect(slider.getAttribute('aria-valuetext')).toBe('0:00:02.900')
  fireEvent.pointerUp(slider)
  fireEvent.blur(slider)
  expect(controller.seek).toHaveBeenCalledOnce()
  expect(controller.seek).toHaveBeenCalledWith(12_900)
  expect(slider.value).toBe('10500')
  expect(slider.getAttribute('aria-valuetext')).toBe('0:00:00.500')
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
  expect(slider.getAttribute('aria-valuetext')).toBe('0:00:02.250')
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
    time: timeFieldValues(),
    ariaValueText: beforeStartSlider.getAttribute('aria-valuetext'),
    min: beforeStartSlider.min,
    max: beforeStartSlider.max,
  }
  rerender(<ReplayControls controller={afterEnd.controller} startMs={10_000} endMs={13_000} drivers={drivers} trackAssets={trackAssets} />)
  const afterEndSlider = screen.getByRole('slider', { name: 'Seek replay' }) as HTMLInputElement
  const afterEndValues = {
    time: timeFieldValues(),
    ariaValueText: afterEndSlider.getAttribute('aria-valuetext'),
    min: afterEndSlider.min,
    max: afterEndSlider.max,
  }

  // Assert: presentation clamps elapsed time while the native range retains absolute session bounds.
  expect(beforeStartValues).toEqual({
    time: ['0', '00', '00', '000'], ariaValueText: '0:00:00.000', min: '10000', max: '13000',
  })
  expect(afterEndValues).toEqual({
    time: ['0', '00', '03', '000'], ariaValueText: '0:00:03.000', min: '10000', max: '13000',
  })
})

test('formats replay time with hours and displays the leaders current lap', () => {
  const replay = {
    ...readySnapshot.replay!,
    leaderboardOrder: ['VER'],
    drivers: { VER: { ...readySnapshot.replay!.drivers.VER, lap: 18 } },
  }
  const { controller } = createController({ ...readySnapshot, timeMs: 3_723_456, replay })

  render(<ReplayControls controller={controller} startMs={0} endMs={7_200_000} drivers={drivers} trackAssets={trackAssets} />)

  expect(timeFieldValues()).toEqual(['1', '02', '03', '456'])
  expect(screen.getByLabelText('Replay time').textContent).toContain('/ 2:00:00.000')
  expect((screen.getByLabelText('Current lap') as HTMLInputElement).value).toBe('18')
})

test.each([
  [{ hours: '0', minutes: '00', seconds: '00', milliseconds: '000' }, 4_000_000, 0],
  [{ hours: '1', minutes: '02', seconds: '03', milliseconds: '456' }, 4_000_000, 3_723_456],
  [{ hours: '1', minutes: '60', seconds: '00', milliseconds: '000' }, 4_000_000, 'Minutes and seconds must be 0–59; milliseconds must be 0–999.'],
  [{ hours: 'x', minutes: '00', seconds: '01', milliseconds: '000' }, 4_000_000, 'Enter numeric hours, minutes, seconds, and milliseconds.'],
  [{ hours: '2', minutes: '00', seconds: '00', milliseconds: '000' }, 3_000, 'Enter a time within the replay duration.'],
])('parses segmented elapsed time %#', (value, durationMs, expected) => {
  expect(parseElapsedParts(value, durationMs)).toBe(expected)
})

test('seeks segmented elapsed time on Enter and an indexed race lap on blur', async () => {
  const user = userEvent.setup()
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={10_000} endMs={20_000} drivers={drivers} lapStarts={[{ lap: 1, startMs: 10_000 }, { lap: 3, startMs: 17_500 }]} trackAssets={trackAssets} />)

  const seconds = screen.getByLabelText('Seconds')
  const milliseconds = screen.getByLabelText('Milliseconds')
  await user.clear(seconds)
  await user.type(seconds, '1')
  await user.clear(milliseconds)
  await user.type(milliseconds, '250{Enter}')
  const lap = screen.getByLabelText('Current lap')
  await user.clear(lap)
  await user.type(lap, '3')
  fireEvent.blur(lap)

  expect(controller.seek).toHaveBeenNthCalledWith(1, 11_250)
  expect(controller.seek).toHaveBeenNthCalledWith(2, 17_500)
})

test('seeks elapsed time on group blur and a race lap on Enter', async () => {
  const user = userEvent.setup()
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={10_000} endMs={20_000} drivers={drivers} lapStarts={[{ lap: 1, startMs: 10_000 }, { lap: 3, startMs: 17_500 }]} trackAssets={trackAssets} />)

  const seconds = screen.getByLabelText('Seconds')
  await user.clear(seconds)
  await user.type(seconds, '2')
  fireEvent.blur(seconds, { relatedTarget: null })
  const lap = screen.getByLabelText('Current lap')
  await user.clear(lap)
  await user.type(lap, '3{Enter}')

  expect(controller.seek).toHaveBeenNthCalledWith(1, 12_000)
  expect(controller.seek).toHaveBeenNthCalledWith(2, 17_500)
})

test('does not seek invalid or out-of-range time and lap values', async () => {
  const user = userEvent.setup()
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={10_000} endMs={20_000} drivers={drivers} lapStarts={[{ lap: 1, startMs: 10_000 }, { lap: 3, startMs: 17_500 }]} trackAssets={trackAssets} />)

  const minutes = screen.getByLabelText('Minutes')
  await user.clear(minutes)
  await user.type(minutes, '60{Enter}')
  const lap = screen.getByLabelText('Current lap')
  await user.clear(lap)
  await user.type(lap, '2{Enter}')

  expect(controller.seek).not.toHaveBeenCalled()
  expect(screen.getAllByRole('alert')).toHaveLength(2)
})

test('keeps inline time seek available and explains unavailable lap navigation', () => {
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  expect((screen.getByLabelText('Hours') as HTMLInputElement).disabled).toBe(false)
  expect((screen.getByLabelText('Current lap') as HTMLInputElement).disabled).toBe(true)
  expect(screen.getByText(/lap seek unavailable/i)).toBeTruthy()
})

test('falls back to the highest valid lap and shows a placeholder without replay data', () => {
  const replay = {
    ...readySnapshot.replay!, leaderboardOrder: ['MISSING'],
    drivers: {
      VER: { ...readySnapshot.replay!.drivers.VER, lap: 12 },
      NOR: { ...readySnapshot.replay!.drivers.VER, lap: 14 },
    },
  }
  const first = createController({ ...readySnapshot, replay })
  const { rerender } = render(<ReplayControls controller={first.controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)
  expect((screen.getByLabelText('Current lap') as HTMLInputElement).value).toBe('14')

  const loading = createController({ ...readySnapshot, status: 'loading', replay: null })
  rerender(<ReplayControls controller={loading.controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)
  expect((screen.getByLabelText('Current lap') as HTMLInputElement).value).toBe('')
  expect((screen.getByLabelText('Current lap') as HTMLInputElement).placeholder).toBe('—')
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

function timeFieldValues(): string[] {
  return ['Hours', 'Minutes', 'Seconds', 'Milliseconds'].map(
    (label) => (screen.getByLabelText(label) as HTMLInputElement).value,
  )
}
