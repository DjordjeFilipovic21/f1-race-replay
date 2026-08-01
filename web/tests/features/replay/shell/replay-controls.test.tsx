/**
 * @vitest-environment jsdom
 */
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { parseElapsedParts, ReplayControls, type ReplayControlsProps, selectDriverId } from '../../../../src/features/replay/shell/ReplayControls'
import type { ReplayController, ReplayControllerSnapshot } from '../../../../src/engine/replay'

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
  vi.useRealTimers()
  vi.restoreAllMocks()
})

test('wires icon transport, seek, and speed controls to the controller', async () => {
  const user = userEvent.setup()
  const { controller, setSnapshot } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  await user.click(screen.getByRole('button', { name: 'Play' }))
  act(() => setSnapshot({ ...readySnapshot, isPlaying: true }))
  expect(screen.getByRole('button', { name: 'Pause' })).toBeTruthy()
  const slider = screen.getByRole('slider', { name: 'Seek replay' })
  expect(screen.getByRole('group', { name: 'Test Circuit live track map' })).toBeTruthy()
  fireEvent.input(slider, { target: { value: '1501' } })
  expect(controller.seek).not.toHaveBeenCalled()
  fireEvent.pointerUp(slider)
  await user.click(screen.getByRole('button', { name: '2×' }))
  act(() => setSnapshot({ ...readySnapshot, isPlaying: true, speed: 2 }))

  expect(controller.start).toHaveBeenCalledOnce()
  expect(controller.seek).toHaveBeenCalledWith(1501)
  expect(controller.setSpeed).toHaveBeenCalledWith(2)
  expect(screen.getByRole('button', { name: '2×' }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.queryByText('Seek replay')).toBeNull()
  expect(screen.queryByText('Replay samples ready.')).toBeNull()
})

test('accepts optional telemetry metadata without changing sampled telemetry', () => {
  const { controller } = createController(readySnapshot)
  const props: ReplayControlsProps = {
    controller,
    startMs: 0,
    endMs: 3000,
    drivers,
    trackAssets,
    seasonMetadata: { year: 2026 },
    telemetryCapabilities: {
      drs: 'not-published',
      overtakeMode: 'not-published',
      activeAero: 'not-published',
      ersReplacement: 'not-published',
    },
  }

  render(<ReplayControls {...props} />)

  expect(screen.getByRole('img', { name: /Speed 246 kilometers per hour/ })).toBeTruthy()
  expect(screen.getByRole('img', { name: /DRS \/ Overtake Mode Not published/ })).toBeTruthy()
})

test('renders status bands and DNF markers behind the sole native seek control', () => {
  const { controller } = createController({ ...readySnapshot, timeMs: 15_000 })
  render(<ReplayControls controller={controller} startMs={10_000} endMs={20_000} drivers={drivers} trackAssets={trackAssets} timelineSummary={{ contractVersion: 'v1', fixtureId: 'test-grand-prix', startMs: 10_000, endMs: 20_000, intervals: [{ kind: 'yellow', startMs: 11_000, endMs: 12_500 }, { kind: 'sc', startMs: 13_000, endMs: 14_000 }, { kind: 'red', startMs: 15_000, endMs: 16_000 }, { kind: 'vsc', startMs: 17_000, endMs: 19_000 }], dnfMarkers: [{ driverId: 'VER', timeMs: 17_500 }] }} />)

  const timeline = screen.getByRole('group', { name: 'Race status timeline' })
  expect(timeline.querySelector('.race-timeline__band--yellow')?.getAttribute('style')).toContain('left: 10%')
  expect(timeline.querySelector('.race-timeline__band--yellow')?.getAttribute('style')).toContain('width: 15%')
  expect(timeline.querySelector('.race-timeline__elapsed')?.getAttribute('style')).toContain('width: 50%')
  expect(timeline.querySelector('.race-timeline__remaining')?.getAttribute('style')).toContain('left: 50%')
  expect(screen.getByLabelText('Safety car from 0:00:03.000 to 0:00:04.000')).toBeTruthy()
  expect(screen.getByLabelText('DNF: VER at 0:00:07.500').getAttribute('class')).toContain('race-timeline__dnf-marker')
  expect(timeline.querySelector('[class*="lap-marker"]')).toBeNull()
  expect(screen.getAllByRole('slider', { name: 'Seek replay' })).toHaveLength(1)
})

test('omits the race timeline when no optional summary is delivered', () => {
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  expect(screen.queryByRole('group', { name: 'Race status timeline' })).toBeNull()
})

test('toggles replay playback with Space outside editable controls', () => {
  const { controller, setSnapshot } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  fireEvent.keyDown(window, { code: 'Space', key: ' ' })
  expect(controller.start).toHaveBeenCalledOnce()

  act(() => setSnapshot({ ...readySnapshot, isPlaying: true }))
  fireEvent.keyDown(window, { code: 'Space', key: ' ' })
  expect(controller.pause).toHaveBeenCalledOnce()

  fireEvent.keyDown(screen.getByRole('button', { name: 'Edit current lap' }), { code: 'Space', key: ' ' })
  expect(controller.pause).toHaveBeenCalledTimes(2)
  fireEvent.click(screen.getByRole('button', { name: 'Edit Seconds' }))
  fireEvent.keyDown(screen.getByLabelText('Seconds'), { code: 'Space', key: ' ' })
  expect(controller.pause).toHaveBeenCalledTimes(2)
})

test('rewinds and forwards by ten seconds within replay bounds', async () => {
  const user = userEvent.setup()
  const { controller, setSnapshot } = createController({ ...readySnapshot, timeMs: 15_000 })
  const { rerender } = render(<ReplayControls controller={controller} startMs={10_000} endMs={30_000} drivers={drivers} trackAssets={trackAssets} />)

  await user.click(screen.getByRole('button', { name: 'Rewind 10 seconds' }))
  setSnapshot({ ...readySnapshot, timeMs: 10_000 })
  await user.click(screen.getByRole('button', { name: 'Forward 10 seconds' }))
  expect(controller.seek).toHaveBeenNthCalledWith(1, 10_000)
  expect(controller.seek).toHaveBeenNthCalledWith(2, 20_000)

  const atEnd = createController({ ...readySnapshot, timeMs: 29_000 })
  rerender(<ReplayControls controller={atEnd.controller} startMs={10_000} endMs={30_000} drivers={drivers} trackAssets={trackAssets} />)
  await user.click(screen.getByRole('button', { name: 'Forward 10 seconds' }))
  expect(atEnd.controller.seek).toHaveBeenCalledWith(30_000)
})

test('jumps to the previous and next indexed lap with indicative controls', async () => {
  const user = userEvent.setup()
  const replay = { ...readySnapshot.replay!, leaderboardOrder: ['VER'], drivers: { VER: { ...readySnapshot.replay!.drivers.VER, lap: 2 } } }
  const { controller } = createController({ ...readySnapshot, replay })
  render(<ReplayControls controller={controller} startMs={10_000} endMs={40_000} drivers={drivers} lapStarts={[{ lap: 1, startMs: 10_000 }, { lap: 2, startMs: 20_000 }, { lap: 3, startMs: 30_000 }]} trackAssets={trackAssets} />)

  const previous = screen.getByRole('button', { name: 'Previous lap' })
  const next = screen.getByRole('button', { name: 'Next lap' })
  expect(previous.textContent).toContain('1L')
  expect(next.textContent).toContain('1L')
  expect(screen.getByRole('button', { name: 'Rewind 10 seconds' }).textContent).toContain('10s')
  expect(screen.getByRole('button', { name: 'Forward 10 seconds' }).textContent).toContain('10s')

  await user.click(previous)
  await user.click(next)

  expect(controller.seek).toHaveBeenNthCalledWith(1, 10_000)
  expect(controller.seek).toHaveBeenNthCalledWith(2, 30_000)
})

test('renders persistent workspace headers in canonical order with definition-driven spans', () => {
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  expect(Array.from(document.querySelector('.replay-workspace')?.children ?? []).map((element) => element.getAttribute('class'))).toEqual([
    'replay-panel-frame',
    'replay-panel-frame',
    'replay-panel-frame',
    'replay-panel-frame',
    'replay-panel-frame',
    'replay-panel-frame',
    'replay-panel-frame',
    'replay-panel-frame',
    'replay-panel-frame',
  ])
  const playerUnpin = screen.getByRole('button', { name: 'Unpin Player panel' })
  expect(playerUnpin.classList.contains('replay-panel-unpin')).toBe(true)
  expect(playerUnpin.hasAttribute('aria-pressed')).toBe(false)
  const playerPanel = document.querySelector('.replay-control-area')
  expect(playerPanel?.contains(screen.getByLabelText('Replay time'))).toBe(true)
  expect(playerPanel?.contains(screen.getByLabelText('Lap navigation'))).toBe(true)
  expect(screen.getByRole('button', { name: 'Move Track map panel' }).textContent).toContain('Track map')
  expect(Array.from(document.querySelector('.replay-workspace')?.children ?? []).map((element) => (element as HTMLElement).style.getPropertyValue('--replay-panel-columns'))).toEqual(['1', '2', '1', '1', '1', '1', '1', '2', '1'])
  expect(Array.from(document.querySelector('.replay-workspace')?.children ?? []).map((element) => (element as HTMLElement).style.getPropertyValue('--replay-panel-desktop-column'))).toEqual(['1', '2', '4', '1', '4', '4', '2', '2', '4'])
})

test('unpins and restores timestamp and lap navigation with the Player panel', () => {
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  fireEvent.click(screen.getByRole('button', { name: 'Unpin Player panel' }))
  expect(screen.queryByRole('form', { name: 'Replay time' })).toBeNull()
  expect(screen.queryByRole('form', { name: 'Lap navigation' })).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Panel Manager' }))
  fireEvent.click(screen.getByRole('button', { name: 'Pin Player panel' }))
  expect(screen.getByRole('form', { name: 'Replay time' })).toBeTruthy()
  expect(screen.getByRole('form', { name: 'Lap navigation' })).toBeTruthy()
})

test('removes an unpinned panel frame and restores it with its drag handle from Panel Manager', () => {
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  fireEvent.click(screen.getByRole('button', { name: 'Unpin Track map panel' }))

  expect(document.querySelectorAll('.replay-workspace > .replay-panel-frame')).toHaveLength(8)
  expect(screen.queryByRole('button', { name: 'Move Track map panel' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Panel Manager' }))
  expect(screen.getByRole('button', { name: 'Pin Track map panel' }).getAttribute('aria-pressed')).toBe('false')
  fireEvent.click(screen.getByRole('button', { name: 'Pin Track map panel' }))
  expect(screen.getByRole('button', { name: 'Move Track map panel' })).toBeTruthy()
})

test('unpins and restores panels while cleaning up and remounting specialized subscriptions', () => {
  const { controller, getUnsubscribeCalls } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  fireEvent.click(screen.getByRole('button', { name: 'Unpin Track map panel' }))
  expect(screen.queryByRole('group', { name: 'Test Circuit live track map' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Panel Manager' }))
  expect(screen.getByRole('button', { name: 'Pin Track map panel' }).getAttribute('aria-pressed')).toBe('false')
  expect(getUnsubscribeCalls()).toBe(1)

  fireEvent.click(screen.getByRole('button', { name: 'Pin Track map panel' }))
  expect(screen.getByRole('group', { name: 'Test Circuit live track map' })).toBeTruthy()
  const trackMapUnpin = within(screen.getByRole('region', { name: 'Track map' })).getByRole('button', { name: 'Unpin Track map panel' })
  expect(trackMapUnpin.classList.contains('replay-panel-unpin')).toBe(true)
  expect(trackMapUnpin.hasAttribute('aria-pressed')).toBe(false)
  expect(controller.subscribe).toHaveBeenCalledTimes(6)

  fireEvent.click(screen.getByRole('button', { name: 'Close Panel Manager' }))
  fireEvent.click(screen.getByRole('button', { name: 'Unpin Leaderboard panel' }))
  expect(screen.queryByRole('table')).toBeNull()
  expect(getUnsubscribeCalls()).toBe(2)

  fireEvent.click(screen.getByRole('button', { name: 'Panel Manager' }))
  fireEvent.click(screen.getByRole('button', { name: 'Pin Leaderboard panel' }))
  expect(screen.getByRole('table')).toBeTruthy()
  expect(controller.subscribe).toHaveBeenCalledTimes(7)
})

test('shows the latest crossed race-control message and clears it on rewind', () => {
  const crossed = { sessionTimeMs: 1_700, eventType: 'flag', description: 'Yellow flag in sector two' }
  const { controller, setSnapshot } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  act(() => setSnapshot({ ...readySnapshot, timeMs: 1_800, crossedEvents: [crossed] }))
  expect(screen.getByText('YELLOW FLAG IN SECTOR TWO')).toBeTruthy()

  act(() => setSnapshot({ ...readySnapshot, timeMs: 1_500, crossedEvents: [] }))
  expect(screen.queryByText('YELLOW FLAG IN SECTOR TWO')).toBeNull()
})

test('expires the active race-control message after five seconds of wall time', () => {
  vi.useFakeTimers()
  try {
    const crossed = { sessionTimeMs: 1_700, eventType: 'flag', description: 'Yellow flag in sector two' }
    const { controller, setSnapshot } = createController(readySnapshot)
    render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

    act(() => setSnapshot({ ...readySnapshot, timeMs: 1_800, crossedEvents: [crossed] }))
    expect(screen.getByText('YELLOW FLAG IN SECTOR TWO')).toBeTruthy()

    act(() => vi.advanceTimersByTime(5_000))
    expect(screen.getByText('YELLOW FLAG IN SECTOR TWO')).toBeTruthy()
    expect(document.querySelector('[data-state="exiting"]')).toBeTruthy()

    act(() => vi.advanceTimersByTime(240))
    expect(screen.queryByText('YELLOW FLAG IN SECTOR TWO')).toBeNull()
  } finally {
    vi.useRealTimers()
  }
})

test('shows zero-based replay times while seeking with absolute session times', () => {
  const { controller } = createController({ ...readySnapshot, timeMs: 11_500 })
  render(<ReplayControls controller={controller} startMs={10_000} endMs={13_000} drivers={drivers} trackAssets={trackAssets} />)

  const slider = screen.getByRole('slider', { name: 'Seek replay' }) as HTMLInputElement
  expect(timeFieldValues()).toEqual(['0', '00', '01', '500'])
  expect(screen.getByLabelText('Replay time').textContent).toContain('/0:00:03')
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
  expect(screen.getByLabelText('Replay time').textContent).toContain('/2:00:00')
  expect(screen.getByRole('button', { name: 'Edit current lap' }).textContent).toBe('18')
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
  const replay = { ...readySnapshot.replay!, leaderboardOrder: ['VER'], drivers: { VER: { ...readySnapshot.replay!.drivers.VER, lap: 1 } } }
  const { controller } = createController({ ...readySnapshot, replay })
  render(<ReplayControls controller={controller} startMs={10_000} endMs={20_000} drivers={drivers} lapStarts={[{ lap: 1, startMs: 10_000 }, { lap: 3, startMs: 17_500 }]} trackAssets={trackAssets} />)

  expect(screen.getByLabelText('Lap navigation').textContent).toContain('Lap1 / 3')

  await user.click(screen.getByRole('button', { name: 'Edit Seconds' }))
  const seconds = screen.getByLabelText('Seconds')
  await user.clear(seconds)
  await user.type(seconds, '1')
  await user.click(screen.getByRole('button', { name: 'Edit Milliseconds' }))
  const milliseconds = screen.getByLabelText('Milliseconds')
  await user.clear(milliseconds)
  await user.type(milliseconds, '250{Enter}')
  await user.click(screen.getByRole('button', { name: 'Edit current lap' }))
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

  await user.click(screen.getByRole('button', { name: 'Edit Seconds' }))
  const seconds = screen.getByLabelText('Seconds')
  await user.clear(seconds)
  await user.type(seconds, '2')
  fireEvent.blur(seconds, { relatedTarget: null })
  await user.click(screen.getByRole('button', { name: 'Edit current lap' }))
  const lap = screen.getByLabelText('Current lap')
  await user.clear(lap)
  await user.type(lap, '3{Enter}')

  expect(controller.seek).toHaveBeenNthCalledWith(1, 12_000)
  expect(controller.seek).toHaveBeenNthCalledWith(2, 17_500)
})

test.each([
  ['Enter', (input: HTMLInputElement) => fireEvent.keyDown(input, { key: 'Enter' })],
  ['blur', (input: HTMLInputElement) => fireEvent.blur(input, { relatedTarget: null })],
])('does not seek to a lap start when the unchanged current lap is confirmed with %s', async (_action, commit) => {
  const user = userEvent.setup()
  const replay = {
    ...readySnapshot.replay!,
    leaderboardOrder: ['VER'],
    drivers: { VER: { ...readySnapshot.replay!.drivers.VER, lap: 3 } },
  }
  const { controller } = createController({ ...readySnapshot, timeMs: 22_500, replay })
  render(<ReplayControls controller={controller} startMs={10_000} endMs={30_000} drivers={drivers} lapStarts={[{ lap: 1, startMs: 10_000 }, { lap: 3, startMs: 20_000 }]} trackAssets={trackAssets} />)

  await user.click(screen.getByRole('button', { name: 'Edit current lap' }))
  commit(screen.getByLabelText('Current lap') as HTMLInputElement)

  expect(controller.seek).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'Edit current lap' }).textContent).toBe('3')
})

test.each([
  ['Enter', (input: HTMLInputElement) => fireEvent.keyDown(input, { key: 'Enter' })],
  ['blur', (input: HTMLInputElement) => fireEvent.blur(input, { relatedTarget: null })],
])('closes an unavailable lap on %s without changing the current replay position', async (_action, commit) => {
  const user = userEvent.setup()
  const replay = {
    ...readySnapshot.replay!,
    leaderboardOrder: ['VER'],
    drivers: { VER: { ...readySnapshot.replay!.drivers.VER, lap: 3 } },
  }
  const { controller } = createController({ ...readySnapshot, timeMs: 22_500, replay })
  render(<ReplayControls controller={controller} startMs={10_000} endMs={30_000} drivers={drivers} lapStarts={[{ lap: 1, startMs: 10_000 }, { lap: 3, startMs: 20_000 }]} trackAssets={trackAssets} />)

  await user.click(screen.getByRole('button', { name: 'Edit current lap' }))
  const lap = screen.getByLabelText('Current lap')
  await user.clear(lap)
  await user.type(lap, '2')
  commit(lap as HTMLInputElement)

  expect(controller.seek).not.toHaveBeenCalled()
  expect(screen.queryByLabelText('Current lap')).toBeNull()
  expect(screen.getByRole('button', { name: 'Edit current lap' }).textContent).toBe('3')
  expect(screen.getByRole('alert').textContent).toContain('Enter an available race lap')
})

test('does not seek invalid or out-of-range time and lap values', async () => {
  const user = userEvent.setup()
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={10_000} endMs={20_000} drivers={drivers} lapStarts={[{ lap: 1, startMs: 10_000 }, { lap: 3, startMs: 17_500 }]} trackAssets={trackAssets} />)

  await user.click(screen.getByRole('button', { name: 'Edit Minutes' }))
  const minutes = screen.getByLabelText('Minutes')
  await user.clear(minutes)
  await user.type(minutes, '60{Enter}')
  expect(screen.queryByLabelText('Minutes')).toBeNull()
  expect(screen.getByRole('button', { name: 'Edit Minutes' }).textContent).toBe('00')
  await user.click(screen.getByRole('button', { name: 'Edit current lap' }))
  const lap = screen.getByLabelText('Current lap')
  await user.clear(lap)
  await user.type(lap, '2{Enter}')

  expect(controller.seek).not.toHaveBeenCalled()
  expect(screen.getAllByRole('alert')).toHaveLength(2)
  expect(screen.getByText('Minutes and seconds must be 0–59; milliseconds must be 0–999.')).toBeTruthy()
})

test('closes an invalid timestamp on blur while retaining the current replay time', async () => {
  const user = userEvent.setup()
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={10_000} endMs={20_000} drivers={drivers} trackAssets={trackAssets} />)

  await user.click(screen.getByRole('button', { name: 'Edit Seconds' }))
  const seconds = screen.getByLabelText('Seconds')
  await user.clear(seconds)
  await user.type(seconds, 'invalid')
  fireEvent.blur(seconds, { relatedTarget: null })

  expect(controller.seek).not.toHaveBeenCalled()
  expect(screen.queryByLabelText('Seconds')).toBeNull()
  expect(screen.getByRole('button', { name: 'Edit Seconds' }).textContent).toBe('00')
  expect(screen.getByRole('alert').textContent).toContain('Enter numeric hours')
})

test('keeps inline time seek available and explains unavailable lap navigation', () => {
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  expect((screen.getByRole('button', { name: 'Edit Hours' }) as HTMLButtonElement).disabled).toBe(false)
  expect((screen.getByRole('button', { name: 'Edit current lap' }) as HTMLButtonElement).disabled).toBe(true)
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
  expect(screen.getByRole('button', { name: 'Edit current lap' }).textContent).toBe('14')

  const loading = createController({ ...readySnapshot, status: 'loading', replay: null })
  rerender(<ReplayControls controller={loading.controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)
  expect(screen.getByRole('button', { name: 'Edit current lap' }).textContent).toBe('—')
})

test('avoids transient loading content and retries controller loading errors', async () => {
  const user = userEvent.setup()
  const loading = createController({ ...readySnapshot, status: 'loading', replay: null })
  const { rerender } = render(<ReplayControls controller={loading.controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)
  expect(screen.queryByText(/loading replay samples/i)).toBeNull()
  expect(document.querySelector('.replay-control-area')?.getAttribute('aria-busy')).toBe('true')

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

test('keeps transport unavailable until replay data is ready', () => {
  const loading = createController({ ...readySnapshot, status: 'loading', replay: null })
  render(<ReplayControls controller={loading.controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  expect((screen.getByRole('button', { name: 'Rewind 10 seconds' }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole('button', { name: 'Previous lap' }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole('button', { name: 'Play' }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole('button', { name: 'Forward 10 seconds' }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole('button', { name: 'Next lap' }) as HTMLButtonElement).disabled).toBe(true)
})

test('keeps elapsed time read-only until one segment is selected and Escape restores it', async () => {
  const user = userEvent.setup()
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  expect(within(screen.getByLabelText('Replay time')).queryByRole('textbox')).toBeNull()
  expect(within(screen.getByLabelText('Replay time')).queryByRole('button', { name: /duration/i })).toBeNull()
  await user.click(screen.getByRole('button', { name: 'Edit Seconds' }))
  expect(screen.getByRole('textbox', { name: 'Seconds' })).toBeTruthy()
  expect(screen.queryByRole('textbox', { name: 'Minutes' })).toBeNull()
  await user.clear(screen.getByRole('textbox', { name: 'Seconds' }))
  await user.type(screen.getByRole('textbox', { name: 'Seconds' }), '2{Escape}')

  expect(within(screen.getByLabelText('Replay time')).queryByRole('textbox')).toBeNull()
  expect(screen.getByRole('button', { name: 'Edit Seconds' }).textContent).toBe('01')
  expect(controller.seek).not.toHaveBeenCalled()
  expect(screen.getByLabelText('Replay time').textContent).toContain('/0:00:03')
})

test.each(['Hours', 'Minutes', 'Seconds', 'Milliseconds'])('edits only the selected %s timestamp segment', async (label) => {
  const user = userEvent.setup()
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  const trigger = screen.getByRole('button', { name: `Edit ${label}` })
  trigger.focus()
  await user.keyboard('{Enter}')

  expect(within(screen.getByLabelText('Replay time')).getAllByRole('textbox')).toHaveLength(1)
  expect(screen.getByRole('textbox', { name: label })).toBeTruthy()
})

test.each(['Hours', 'Minutes', 'Seconds', 'Milliseconds'])('opens the %s timestamp segment by pointer activation', async (label) => {
  const user = userEvent.setup()
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  await user.click(screen.getByRole('button', { name: `Edit ${label}` }))

  expect(within(screen.getByLabelText('Replay time')).getAllByRole('textbox')).toHaveLength(1)
  expect(screen.getByRole('textbox', { name: label })).toBeTruthy()
})

test('unsubscribes when the adapter unmounts', () => {
  const { controller, getUnsubscribeCalls } = createController(readySnapshot)
  const { unmount } = render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)
  unmount()
  expect(controller.subscribe).toHaveBeenCalledTimes(5)
  expect(getUnsubscribeCalls()).toBe(5)
})

function timeFieldValues(): string[] {
  return ['Hours', 'Minutes', 'Seconds', 'Milliseconds'].map(
    (label) => screen.getByRole('button', { name: `Edit ${label}` }).textContent ?? '',
  )
}

test('defaults to the race leader while preserving an explicit driver selection', () => {
  const replay = {
    ...readySnapshot.replay!,
    leaderboardOrder: ['NOR', 'VER'],
    drivers: { ...readySnapshot.replay!.drivers, NOR: { ...readySnapshot.replay!.drivers.VER, position: 1 } },
  }

  expect(selectDriverId(null, replay, [{ ...drivers[0], id: 'NOR' }, drivers[0]])).toBe('NOR')
  expect(selectDriverId('VER', { ...replay, leaderboardOrder: ['NOR', 'VER'] }, drivers)).toBe('VER')
})

test('shares leaderboard clicks with the Driver and Telemetry panels and selected track marker', () => {
  const twoDrivers = [...drivers, { id: 'NOR', displayName: 'Lando Norris', teamName: 'McLaren', colorHex: '#ff8000', carNumber: '4' }]
  const replay = {
    ...readySnapshot.replay!,
    leaderboardOrder: ['VER', 'NOR'],
    drivers: { ...readySnapshot.replay!.drivers, NOR: { ...readySnapshot.replay!.drivers.VER, position: 2, speed: 111 } },
  }
  const { controller, setSnapshot } = createController({ ...readySnapshot, replay })
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={twoDrivers} trackAssets={trackAssets} />)

  expect(screen.getByRole('region', { name: 'Driver' }).textContent).toContain('Max Verstappen')
  expect(within(screen.getByRole('region', { name: 'Telemetry' })).getByRole('img', { name: /Speed 246 kilometers per hour/ })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Select Max Verstappen' }))
  setSnapshot({ ...readySnapshot, replay: { ...replay, leaderboardOrder: ['NOR', 'VER'] } })

  expect(screen.getByRole('region', { name: 'Driver' }).textContent).toContain('Max Verstappen')
  expect(within(screen.getByRole('region', { name: 'Telemetry' })).getByRole('img', { name: /Speed 246 kilometers per hour/ })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Select Max Verstappen' }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.getByRole('img', { name: 'Max Verstappen (VER)' }).getAttribute('class')).toContain('live-track-map__marker--selected')
})

test('propagates sidecar data to the lap analysis and strategy panels and the leaderboard sectors mode', () => {
  const twoDrivers = [...drivers, { id: 'NOR', displayName: 'Lando Norris', teamName: 'McLaren', colorHex: '#ff8000', carNumber: '4' }]
  const replay = {
    ...readySnapshot.replay!,
    leaderboardOrder: ['VER', 'NOR'],
    drivers: { ...readySnapshot.replay!.drivers, NOR: { ...readySnapshot.replay!.drivers.VER, position: 2 } },
  }
  const lapSectorSidecar = {
    contractVersion: 'v1' as const,
    fixtureId: 'test-grand-prix',
    drivers: {
      VER: {
        lapNumber: [1], lapStartMs: [0], lapEndMs: [90000], lapDurationMs: [90000],
        sector1DurationMs: [28000], sector2DurationMs: [32000], sector3DurationMs: [30000],
        sector1SessionTimeMs: [28000], sector2SessionTimeMs: [60000], sector3SessionTimeMs: [90000],
      },
    },
  }
  const stintSummary = {
    contractVersion: 'v1' as const,
    fixtureId: 'test-grand-prix',
    drivers: {
      VER: {
        stintNumber: [1], compound: ['SOFT'], startLap: [1], endLap: [null],
        startTimeMs: [0], endTimeMs: [null], tyreLifeAtStart: [0],
        isFreshTyre: [true], pitInTimeMs: [null], pitOutTimeMs: [null],
      },
    },
  }
  const pitLossModel = {
    contractVersion: 'v1' as const,
    fixtureId: 'test-grand-prix',
    method: 'global-prior-weighted-mean-v1' as const,
    baselineMs: 22000,
    priorWeight: 2,
    timeMs: [90000],
    estimatedLossMs: [22000],
    observedSampleCount: [0],
  }
  const { controller } = createController({ ...readySnapshot, timeMs: 95_000, replay })
  render(
    <ReplayControls
      controller={controller}
      startMs={0}
      endMs={120_000}
      drivers={twoDrivers}
      trackAssets={trackAssets}
      lapSectorSidecar={lapSectorSidecar}
      stintSummary={stintSummary}
      pitLossModel={pitLossModel}
    />,
  )

  expect(screen.getByRole('region', { name: 'Lap analysis' })).toBeTruthy()
  expect(screen.getByRole('region', { name: 'Strategy' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Sectors' }).getAttribute('aria-pressed')).toBe('false')
})

test('renders empty states without errors when sidecar data is absent', () => {
  const { controller } = createController(readySnapshot)
  render(<ReplayControls controller={controller} startMs={0} endMs={3000} drivers={drivers} trackAssets={trackAssets} />)

  expect(screen.getByRole('region', { name: 'Lap analysis' })).toBeTruthy()
  expect(screen.getByRole('region', { name: 'Strategy' })).toBeTruthy()
  expect(screen.getByText(/no completed laps yet/i)).toBeTruthy()
  expect(screen.getByText(/no stint data is available yet/i)).toBeTruthy()
})
