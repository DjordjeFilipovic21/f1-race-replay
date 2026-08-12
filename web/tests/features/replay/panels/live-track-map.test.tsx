/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { LapSectorSidecar, QualifyingSummary, QualifyingTimeline, SessionMode } from '../../../../src/data/replay/types'
import { createPaddedViewBox, createTrackMapGeometry, LiveTrackMap, toMapPoint } from '../../../../src/features/replay/panels/LiveTrackMap'
import type { ReplayController, ReplayControllerSnapshot } from '../../../../src/engine/replay'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'

const trackAssets = {
  contractVersion: 'v2', fixtureId: 'test-grand-prix', trackId: 'test-circuit', trackName: 'Test Circuit',
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
  let current: ReplayControllerSnapshot = { status: 'ready', timeMs: 0, speed: 1, isPlaying: false, committedSeekRevision: 0, replay, crossedEvents: [], error: null }
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

test('announces a neutral notice when every active driver lacks reliable position telemetry', () => {
  const replay = snapshot()
  const { controller, setReplay } = createController({
    ...replay,
    drivers: {
      VER: { ...replay.drivers.VER, x: null, y: null },
      NOR: { ...replay.drivers.NOR, x: null, y: null },
    },
  })
  render(<LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} />)

  const notice = screen.getByText('Reliable source telemetry is unavailable for this period.')
  expect(notice.hidden).toBe(false)

  setReplay(replay)

  expect(notice.hidden).toBe(true)
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

test('updates the accessible flag status and boundary semantics without rerendering the map', () => {
  const replay = {
    ...snapshot(),
    leaderboardOrder: ['VER'],
    trackStatusCode: 4,
    drivers: { ...snapshot().drivers, VER: { ...snapshot().drivers.VER, position: 1, trackDistanceMeters: 250 } },
  }
  const { controller, setReplay } = createController(replay)
  render(<LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} />)

  const status = screen.getByRole('status', { name: 'Track status: Safety Car' })
  const marker = screen.getByRole('img', { name: 'Safety Car (SC)' })
  const canvas = document.querySelector('.live-track-map__canvas')
  expect(status.textContent).toBe('Safety Car')
  expect(canvas?.contains(status)).toBe(true)
  expect(canvas?.contains(screen.getByRole('group', { name: `${trackAssets.trackName} live track map` }))).toBe(true)
  expect(document.querySelectorAll('.live-track-map__boundary--yellow')).toHaveLength(2)
  expect(marker.getAttribute('visibility')).toBe('visible')
  expect(marker.getAttribute('transform')).toBe('translate(0 9)')
  expect(marker.querySelector('text')?.textContent).toBe('SC')

  const setStatusAttribute = vi.spyOn(status, 'setAttribute')
  setReplay(replay)
  expect(setStatusAttribute).not.toHaveBeenCalled()

  setReplay({ ...replay, drivers: { ...replay.drivers, VER: { ...replay.drivers.VER, x: 8, y: 2 } } })
  expect(marker.getAttribute('transform')).not.toBe('translate(0 9)')

  setReplay({ ...replay, trackStatusCode: 5 })

  expect(screen.getByRole('status', { name: 'Track status: Red Flag' }).textContent).toBe('Red Flag')
  expect(document.querySelectorAll('.live-track-map__boundary--red')).toHaveLength(2)
  expect(marker.getAttribute('visibility')).toBe('hidden')
})

test('uses leader coordinates without requiring derived track progress', () => {
  const replay = { ...snapshot(), leaderboardOrder: ['VER'], trackStatusCode: 4 }
  const { controller } = createController(replay)
  render(<LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} />)

  expect(screen.getByRole('status', { name: 'Track status: Safety Car' })).toBeTruthy()
  const marker = screen.getByRole('img', { name: 'Safety Car (SC)' })
  expect(marker.getAttribute('visibility')).toBe('visible')
  expect(marker.getAttribute('transform')).toBe('translate(0 9)')
})

test('renders the selected marker last with glow styling on its driver dot', () => {
  const { controller } = createController(snapshot())
  render(<LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} selectedDriverId="VER" />)

  const markers = Array.from(document.querySelectorAll('.live-track-map__marker'))
  expect(markers.at(-1)?.getAttribute('aria-label')).toBe('Max Verstappen (VER)')
  const selected = screen.getByRole('img', { name: 'Max Verstappen (VER)' })
  expect(selected.getAttribute('class')).toContain('live-track-map__marker--selected')
  expect(selected.getAttribute('color')).toBe('#3671c6')
  expect(selected.querySelectorAll('circle')).toHaveLength(1)
  expect(selected.querySelector('.live-track-map__driver-dot')).toBeTruthy()
})

test('exposes causal qualifying outlap and flying states without replacing selected marker semantics', () => {
  const qualifyingSummary: QualifyingSummary = {
    contractVersion: 'v2',
    fixtureId: 'test-grand-prix',
    drivers: {
      VER: { qualifyingPosition: [1], q1TimeMs: [90], q2TimeMs: [80], q3TimeMs: [70], bestLapNumber: [1], bestLapTimeMs: [70] },
    },
  }
  const lapSectorSidecar: LapSectorSidecar = {
    contractVersion: 'v2',
    fixtureId: 'test-grand-prix',
    phaseBoundaries: [],
    drivers: {
      VER: {
        lapNumber: [1, 2], lapStartMs: [0, 100], lapEndMs: [90, 190], lapDurationMs: [90, 90],
        sector1DurationMs: [30, 30], sector2DurationMs: [30, 30], sector3DurationMs: [30, 30],
        sector1SessionTimeMs: [40, null], sector2SessionTimeMs: [null, null], sector3SessionTimeMs: [null, null],
        qualifyingPhase: [null, null],
      },
    },
  }
  const replay = { ...snapshot(), sessionTimeMs: 50, drivers: { ...snapshot().drivers, VER: { ...snapshot().drivers.VER, lap: 1 } } }
  const { controller, setReplay } = createController(replay)
  render(
    <LiveTrackMap
      trackAssets={trackAssets}
      controller={controller}
      drivers={drivers}
      selectedDriverId="VER"
      sessionMode="qualifying"
      qualifyingSummary={qualifyingSummary}
      lapSectorSidecar={lapSectorSidecar}
    />,
  )

  const marker = screen.getByRole('img', { name: /Max Verstappen \(VER\)/ })
  expect(marker.getAttribute('data-qualifying-lap-state')).toBe('flying')
  expect(marker.getAttribute('aria-label')).toContain('qualifying lap state: Flying')
  expect(marker.getAttribute('class')).toContain('live-track-map__marker--selected')

  setReplay({
    ...replay,
    sessionTimeMs: 120,
    drivers: { ...replay.drivers, VER: { ...replay.drivers.VER, lap: 2, x: 6, y: 3 } },
  })

  expect(marker.getAttribute('data-qualifying-lap-state')).toBe('outlap')
  expect(marker.getAttribute('aria-label')).toContain('qualifying lap state: Outlap')
  expect(marker.getAttribute('class')).toContain('live-track-map__marker--selected')
  expect(marker.querySelector('.live-track-map__driver-dot')).toBeTruthy()
})

test.each(['qualifying', 'sprint-qualifying', 'sprint-shootout'] as const)('renders the accessible lap-state legend for %s maps', (sessionMode: SessionMode) => {
  const { controller } = createController(snapshot())
  render(<LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} sessionMode={sessionMode} />)

  const legend = screen.getByRole('group', { name: 'Qualifying lap state legend' })
  expect(legend).toBeTruthy()
  expect(legend.textContent).toContain('Outlap')
  expect(legend.textContent).toContain('Flying')
  expect(legend.querySelector('.live-track-map__legend-swatch--outlap')).toBeTruthy()
  expect(legend.querySelector('.live-track-map__legend-swatch--flying')).toBeTruthy()
})

test.each(['race', 'sprint'] as const)('does not add qualifying legend or marker state to %s maps', (sessionMode: SessionMode) => {
  const { controller } = createController(snapshot())
  render(<LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} sessionMode={sessionMode} />)

  expect(screen.queryByRole('group', { name: 'Qualifying lap state legend' })).toBeNull()
  expect(screen.getByRole('img', { name: 'Max Verstappen (VER)' }).getAttribute('data-qualifying-lap-state')).toBeNull()
})

test('keeps the legend available while unavailable qualifying evidence leaves marker state unknown', () => {
  const { controller } = createController(snapshot())
  render(<LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} sessionMode="qualifying" />)

  expect(screen.getByRole('group', { name: 'Qualifying lap state legend' })).toBeTruthy()
  const marker = screen.getByRole('img', { name: 'Max Verstappen (VER)' })
  expect(marker.getAttribute('data-qualifying-lap-state')).toBe('unknown')
  expect(marker.getAttribute('aria-label')).toBe('Max Verstappen (VER)')
})

test('hides only the causal qualifying incident marker and restores it when seeking before the incident', () => {
  const qualifyingTimeline: QualifyingTimeline = {
    contractVersion: 'v2',
    fixtureId: 'test-grand-prix',
    startMs: 0,
    endMs: 300,
    intervals: [],
    incidentMarkers: [{ driverId: 'VER', timeMs: 100, source: 'race-control-car-event', rawMessage: 'CAR 1 CRASH' }],
  }
  const replay = { ...snapshot(), sessionTimeMs: 99 }
  const { controller, setReplay } = createController(replay)
  render(<LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} sessionMode="qualifying" qualifyingTimeline={qualifyingTimeline} />)

  const marker = screen.getByRole('img', { name: 'Max Verstappen (VER)' })
  expect(marker.getAttribute('visibility')).toBe('visible')

  setReplay({ ...replay, sessionTimeMs: 100 })
  expect(marker.getAttribute('visibility')).toBe('hidden')
  expect(marker.getAttribute('aria-label')).toBe('Max Verstappen (VER)')

  setReplay({ ...replay, sessionTimeMs: 99, drivers: { ...replay.drivers, VER: { ...replay.drivers.VER, status: 'OffTrack' } } })
  expect(marker.getAttribute('visibility')).toBe('visible')
})

test('fails closed for absent qualifying incident evidence and does not hide on a missing sample alone', () => {
  const replay = { ...snapshot(), sessionTimeMs: 150, drivers: { ...snapshot().drivers, VER: { ...snapshot().drivers.VER, status: 'OffTrack' } } }
  const { controller } = createController(replay)
  render(<LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} sessionMode="qualifying" />)

  expect(screen.getByRole('img', { name: 'Max Verstappen (VER)' }).getAttribute('visibility')).toBe('visible')
  expect(screen.getByRole('img', { name: 'Lando Norris (NOR)', hidden: true }).getAttribute('visibility')).toBe('hidden')
})
