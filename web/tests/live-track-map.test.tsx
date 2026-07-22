/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { createPaddedViewBox, createTrackMapGeometry, LiveTrackMap, toMapPoint } from '../src/replay-ui/LiveTrackMap'
import type { ReplayController, ReplayControllerSnapshot } from '../src/replay-engine'
import type { ReplaySnapshot } from '../src/replay-engine/types'

const trackAssets = {
  contractVersion: 'v1', fixtureId: 'test-grand-prix', trackId: 'test-circuit', trackName: 'Test Circuit',
  coordinateSpace: { units: 'meters', origin: 'test origin' }, circuitLengthMeters: 1000, rotationDegrees: 90,
  startFinish: { center: { x: 0, y: 5 }, inner: { x: 0, y: 0 }, outer: { x: 0, y: 10 } },
  centerLine: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }],
  innerBoundary: [{ x: 1, y: 1 }, { x: 9, y: 1 }, { x: 9, y: 9 }, { x: 1, y: 9 }],
  outerBoundary: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 0, y: 10 }],
} as const

const drivers = [
  { id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' },
  { id: 'NOR', displayName: 'Lando Norris', teamName: 'McLaren', colorHex: '#ff8000', carNumber: '4' },
]

function snapshot(): ReplaySnapshot {
  return {
    sessionTimeMs: 0, leaderboardOrder: null, trackStatusCode: null, weatherState: null, events: [],
    drivers: {
      VER: { x: 5, y: 2, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: null, lap: null, position: null, gear: null, drs: null, tyreCompound: null, status: null, isInPitLane: null },
      NOR: { x: null, y: 5, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: null, lap: null, position: null, gear: null, drs: null, tyreCompound: null, status: null, isInPitLane: null },
      BAD: { x: Number.NaN, y: 4, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: null, lap: null, position: null, gear: null, drs: null, tyreCompound: null, status: null, isInPitLane: null },
    },
  }
}

afterEach(cleanup)

function createController(replay: ReplaySnapshot | null) {
  let current: ReplayControllerSnapshot = { status: 'ready', timeMs: 0, speed: 1, isPlaying: false, replay, crossedEvents: [], error: null }
  const listeners = new Set<() => void>()
  let unsubscribeCalls = 0
  const controller: ReplayController = {
    getSnapshot: () => current,
    subscribe: vi.fn((listener: () => void) => {
      listeners.add(listener)
      return () => { unsubscribeCalls += 1; listeners.delete(listener) }
    }),
    start: vi.fn(), pause: vi.fn(), seek: vi.fn(), setSpeed: vi.fn(), retry: vi.fn(async () => undefined), dispose: vi.fn(),
  }
  return {
    controller,
    getUnsubscribeCalls: () => unsubscribeCalls,
    setReplay: (next: ReplaySnapshot | null) => {
      current = { ...current, replay: next }
      listeners.forEach((listener) => listener())
    },
  }
}

test('rotates coordinates and derives a finite padded viewBox deterministically', () => {
  expect(toMapPoint({ x: 0, y: 10 }, 0)).toEqual({ x: 0, y: -10 })
  expect(toMapPoint({ x: 10, y: 0 }, 90)).toEqual({ x: expect.closeTo(0), y: 10 })
  expect(createPaddedViewBox([{ x: 0, y: 0 }, { x: 10, y: 5 }])).toEqual({ minX: -0.8, minY: -0.8, width: 11.6, height: 6.6 })
  expect(createPaddedViewBox([{ x: Number.NaN, y: 0 }])).toBeNull()
})

test.each([90, -90])('renders portrait geometry in landscape using a %s degree rotation', (rotationDegrees) => {
  const portraitAssets = {
    ...trackAssets,
    rotationDegrees,
    centerLine: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 40 }, { x: 0, y: 40 }],
    innerBoundary: [{ x: 1, y: 1 }, { x: 9, y: 1 }, { x: 9, y: 39 }, { x: 1, y: 39 }],
    outerBoundary: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 40 }, { x: 0, y: 40 }],
  } as const

  const geometry = createTrackMapGeometry(portraitAssets)

  expect(geometry).not.toBeNull()
  expect(geometry?.viewBox.width).toBeGreaterThan(geometry?.viewBox.height ?? Number.POSITIVE_INFINITY)
})

test('renders labelled track geometry and only finite sampled driver markers', () => {
  const { controller } = createController(snapshot())
  render(<LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} />)

  expect(screen.getByRole('region', { name: 'Test Circuit track map' })).toBeTruthy()
  expect(screen.getByRole('group', { name: 'Test Circuit live track map' })).toBeTruthy()
  expect(screen.getByRole('img', { name: 'Max Verstappen (VER)' })).toBeTruthy()
  expect(screen.getByRole('img', { name: 'Lando Norris (NOR)', hidden: true }).getAttribute('visibility')).toBe('hidden')
  expect(screen.queryByRole('img', { name: 'BAD (BAD)' })).toBeNull()
  expect(document.querySelectorAll('.live-track-map__boundary')).toHaveLength(2)
  const svg = screen.getByRole('group', { name: 'Test Circuit live track map' })
  const viewBox = svg.getAttribute('viewBox')?.split(' ').map(Number) ?? []
  expect(viewBox).toHaveLength(4)
  expect(viewBox[0]).toBeCloseTo(-0.8)
  expect(viewBox[1]).toBeCloseTo(-0.8)
  expect(viewBox[2]).toBeCloseTo(11.6)
  expect(viewBox[3]).toBeCloseTo(11.6)

  const centerLine = document.querySelector('.live-track-map__center-line')
  expect(centerLine?.getAttribute('d')).toMatch(/^M 0 0 L [^ ]+ 10 L 10 /)
  const startFinish = document.querySelector('.live-track-map__start-finish')
  expect(Number(startFinish?.getAttribute('x1'))).toBeCloseTo(0)
  expect(Number(startFinish?.getAttribute('y1'))).toBeCloseTo(0)
  expect(Number(startFinish?.getAttribute('x2'))).toBeCloseTo(10)
  expect(Number(startFinish?.getAttribute('y2'))).toBeCloseTo(0)

  const marker = screen.getByRole('img', { name: 'Max Verstappen (VER)' })
  const markerCircle = marker.querySelector('circle')
  const markerLabel = marker.querySelector('text')
  expect(marker.getAttribute('transform')).toBe('translate(2 5)')
  expect(Number(markerCircle?.getAttribute('cx'))).toBe(0)
  expect(Number(markerCircle?.getAttribute('cy'))).toBe(0)
  expect(Number(markerCircle?.getAttribute('r'))).toBeCloseTo(11.6 * 0.03)
  expect(Number(markerLabel?.getAttribute('font-size'))).toBeCloseTo(11.6 * 0.021)
})

test('keeps manifest marker nodes mounted while notifications update transforms and cleans up', () => {
  const replay = snapshot()
  const { controller, getUnsubscribeCalls, setReplay } = createController(replay)
  const { unmount } = render(<LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} />)
  const marker = screen.getByRole('img', { name: 'Max Verstappen (VER)' })

  setReplay({ ...replay, drivers: { ...replay.drivers, VER: { ...replay.drivers.VER, x: 6, y: 3 } } })

  expect(screen.getByRole('img', { name: 'Max Verstappen (VER)' })).toBe(marker)
  expect(marker.getAttribute('transform')).toBe('translate(3 6)')
  expect(screen.getByRole('img', { name: 'Lando Norris (NOR)', hidden: true }).getAttribute('visibility')).toBe('hidden')
  setReplay(null)
  expect(marker.getAttribute('transform')).toBe('translate(3 6)')
  expect(marker.getAttribute('visibility')).toBe('visible')
  unmount()
  expect(controller.subscribe).toHaveBeenCalledOnce()
  expect(getUnsubscribeCalls()).toBe(1)
})

test('hides only markers sampled with terminal OUT status', () => {
  const replay = snapshot()
  const { controller, setReplay } = createController(replay)
  render(<LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} />)
  const marker = screen.getByRole('img', { name: 'Max Verstappen (VER)' })

  setReplay({ ...replay, drivers: { ...replay.drivers, VER: { ...replay.drivers.VER, status: 'OUT' } } })
  expect(marker.getAttribute('visibility')).toBe('hidden')

  setReplay({ ...replay, drivers: { ...replay.drivers, VER: { ...replay.drivers.VER, status: 'OffTrack' } } })
  expect(marker.getAttribute('visibility')).toBe('visible')
})

test('renders the selected marker last with a visible selection ring', () => {
  const { controller } = createController(snapshot())
  render(<LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} selectedDriverId="VER" />)

  const markers = Array.from(document.querySelectorAll('.live-track-map__marker'))
  expect(markers.at(-1)?.getAttribute('aria-label')).toBe('Max Verstappen (VER)')
  expect(screen.getByRole('img', { name: 'Max Verstappen (VER)' }).getAttribute('class')).toContain('live-track-map__marker--selected')
  expect(screen.getByRole('img', { name: 'Max Verstappen (VER)' }).querySelector('.live-track-map__selection-ring')).toBeTruthy()
})
