/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { QualifyingClassificationPanel } from '../../../../src/features/replay/panels/QualifyingClassificationPanel'
import type { LapSectorSidecar, QualifyingLapStatusSidecar, QualifyingSummary } from '../../../../src/data/replay/types'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'

const drivers = [
  { id: 'HAM', displayName: 'Lewis Hamilton', teamName: 'Ferrari', colorHex: '#e8002d', carNumber: '44' },
  { id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' },
] as const

const snapshot = {
  sessionTimeMs: 150,
  leaderboardOrder: ['HAM', 'VER'],
  trackStatusCode: null,
  weatherState: null,
  events: [],
  drivers: {},
} as const

afterEach(cleanup)

test('renders sampled classification state without exposing final elimination early', () => {
  render(<QualifyingClassificationPanel snapshot={snapshot} drivers={drivers} sessionMode="qualifying" qualifyingLapStatus={null} qualifyingSummary={{ contractVersion: 'v2', fixtureId: 'test', drivers: {
    HAM: { qualifyingPosition: [1], q1TimeMs: [90_000], q2TimeMs: [89_000], q3TimeMs: [88_000], bestLapNumber: [3], bestLapTimeMs: [88_000] },
    VER: { qualifyingPosition: [2], q1TimeMs: [91_000], q2TimeMs: [null], q3TimeMs: [null], bestLapNumber: [null], bestLapTimeMs: [null] },
  } }} />)

  expect(screen.getByRole('table', { name: 'Qualifying classification' })).toBeTruthy()
  expect(screen.getAllByRole('columnheader').map((header) => header.textContent)).toEqual(['Classification', 'Team colour', 'Driver', 'Leader'])
  expect(within(screen.getAllByRole('row')[1]).getAllByRole('cell')[2].textContent).toBe('No Time')
  const eliminated = screen.getAllByRole('row').find((row) => within(row).queryByRole('rowheader')?.textContent === 'VER')
  expect(eliminated).toBeTruthy()
  expect(within(eliminated!).getAllByRole('cell')[0].textContent).toBe('—')
  expect(within(eliminated!).getAllByRole('cell')[2].textContent).toBe('—')

  const controls = within(screen.getByRole('group', { name: 'Qualifying metric' }))
  expect(controls.getAllByRole('button').map((button) => button.textContent)).toEqual(['Leader', 'Lap time', 'Tyres', 'Sectors'])
  expect(controls.getByRole('button', { name: 'Leader' }).getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(controls.getByRole('button', { name: 'Lap time' }))
  expect(within(eliminated!).getAllByRole('cell')[2].textContent).toBe('No Time')
  fireEvent.click(controls.getByRole('button', { name: 'Tyres' }))
  expect(within(eliminated!).getAllByRole('cell')[2].textContent).toBe('Unavailable')
})

test('renders the fastest completed causal lap in the active delivered phase', () => {
  // Arrange
  const lapSectorSidecar = {
    contractVersion: 'v2',
    fixtureId: 'test',
    phaseBoundaries: [{ phase: 'Q1', startMs: 0 }],
    drivers: {
      HAM: {
        lapNumber: [1], lapStartMs: [0], lapEndMs: [90_000], lapDurationMs: [90_000],
        sector1DurationMs: [30_000], sector2DurationMs: [30_000], sector3DurationMs: [30_000],
        sector1SessionTimeMs: [30_000], sector2SessionTimeMs: [60_000], sector3SessionTimeMs: [90_000],
         qualifyingPhase: ['Q1'], lapKind: ['flying'],
      },
    },
  } as const
  const qualifyingLapStatus = {
    contractVersion: 'v2', fixtureId: 'test',
    drivers: { HAM: { lapNumber: [1], lapStartMs: [0], lapEndMs: [90_000], status: ['valid'], deletedReason: [null] } },
    events: [],
  } as const

  // Act
  render(<QualifyingClassificationPanel
    snapshot={{ ...snapshot, sessionTimeMs: 100_000 }}
    drivers={drivers}
    sessionMode="qualifying"
    qualifyingSummary={{ contractVersion: 'v2', fixtureId: 'test', drivers: {
      HAM: { qualifyingPosition: [1], q1TimeMs: [90_000], q2TimeMs: [null], q3TimeMs: [null], bestLapNumber: [1], bestLapTimeMs: [90_000] },
      VER: { qualifyingPosition: [2], q1TimeMs: [null], q2TimeMs: [null], q3TimeMs: [null], bestLapNumber: [null], bestLapTimeMs: [null] },
    } }}
    lapSectorSidecar={lapSectorSidecar}
    qualifyingLapStatus={qualifyingLapStatus}
  />)

  // Assert
  expect(within(screen.getAllByRole('row')[1]).getAllByRole('cell')[2].textContent).toBe('1:30.000')
  expect(screen.queryByText('L1')).toBeNull()
  expect(screen.queryByText('Flying')).toBeNull()
  expect(screen.queryByText('Outlap')).toBeNull()
})

test('layers qualifying finish styling without replacing the completed lap time', () => {
  const lapSectorSidecar = {
    contractVersion: 'v2', fixtureId: 'test',
    phaseBoundaries: [{ phase: 'Q1', startMs: 0 }, { phase: 'Q2', startMs: 100 }, { phase: 'Q3', startMs: 200 }],
    drivers: {
      HAM: {
        lapNumber: [1, 2, 3], lapStartMs: [0, 100, 200], lapEndMs: [90, 190, 290], lapDurationMs: [90, 90, 88_000],
        sector1DurationMs: [30, 30, 29_000], sector2DurationMs: [30, 30, 29_000], sector3DurationMs: [30, 30, 30_000],
         sector1SessionTimeMs: [30, 130, 230], sector2SessionTimeMs: [60, 160, 260], sector3SessionTimeMs: [90, 190, 290], qualifyingPhase: ['Q1', 'Q2', 'Q3'], lapKind: ['flying', 'flying', 'flying'],
      },
      VER: {
        lapNumber: [1, 2, 3], lapStartMs: [0, 100, 200], lapEndMs: [91, 191, 291], lapDurationMs: [91, 91, 89_000],
        sector1DurationMs: [30, 30, 29_000], sector2DurationMs: [30, 30, 30_000], sector3DurationMs: [31, 31, 30_000],
         sector1SessionTimeMs: [30, 130, 230], sector2SessionTimeMs: [60, 160, 260], sector3SessionTimeMs: [91, 191, 291], qualifyingPhase: ['Q1', 'Q2', 'Q3'], lapKind: ['flying', 'flying', 'flying'],
      },
    },
  } as const
  const qualifyingLapStatus = {
    contractVersion: 'v2', fixtureId: 'test',
    drivers: {
      HAM: { lapNumber: [1, 2, 3], lapStartMs: [0, 100, 200], lapEndMs: [90, 190, 290], status: ['valid', 'valid', 'valid'], deletedReason: [null, null, null] },
      VER: { lapNumber: [1, 2, 3], lapStartMs: [0, 100, 200], lapEndMs: [91, 191, 291], status: ['valid', 'valid', 'valid'], deletedReason: [null, null, null] },
    }, events: [],
  } as const
  const summary = {
    contractVersion: 'v2' as const, fixtureId: 'test', drivers: {
      HAM: { qualifyingPosition: [1], q1TimeMs: [90], q2TimeMs: [90], q3TimeMs: [88_000], bestLapNumber: [3], bestLapTimeMs: [88_000] },
      VER: { qualifyingPosition: [2], q1TimeMs: [91], q2TimeMs: [91], q3TimeMs: [89_000], bestLapNumber: [3], bestLapTimeMs: [89_000] },
    },
  }

  render(<QualifyingClassificationPanel snapshot={{ ...snapshot, sessionTimeMs: 300, drivers: { HAM: sampledDriver({ tyreCompound: 'SOFT', tyreAge: 4 }) } }} drivers={drivers} sessionMode="qualifying" qualifyingSummary={summary} qualifyingLapStatus={qualifyingLapStatus} lapSectorSidecar={lapSectorSidecar} replayEndMs={300} />)

  const row = qualifyingRowForDriver('HAM')
  expect(row.className).not.toContain('live-leaderboard__row--terminal')
  expect(row.querySelector('.live-leaderboard__gap')?.className).toContain('live-leaderboard__gap--finished')
  expect(row.querySelector('.live-leaderboard__qualifying-lap-time')?.className).toContain('live-leaderboard__qualifying-lap-time--finished')
  expect(within(row).getAllByRole('cell')[0].textContent).toBe('1')
  expect(within(row).getAllByRole('cell')[2].textContent).toBe('1:28.000')
  const secondRow = qualifyingRowForDriver('VER')
  expect(within(secondRow).getAllByRole('cell')[2].textContent).toBe('+1.000s')
  expect(secondRow.querySelector('.live-leaderboard__gap')?.className).toContain('live-leaderboard__gap--finished')

  fireEvent.click(screen.getByRole('button', { name: 'Lap time' }))
  expect(screen.getAllByRole('columnheader').at(-1)?.textContent).toBe('Lap time')
  expect(within(row).getAllByRole('cell')[2].textContent).toBe('1:28.000')
  expect(within(secondRow).getAllByRole('cell')[2].textContent).toBe('1:29.000')
  expect(row.querySelector('.live-leaderboard__qualifying-lap-time--finished')).toBeTruthy()
  expect(secondRow.querySelector('.live-leaderboard__qualifying-lap-time--finished')).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: 'Tyres' }))
  expect(within(row).getByRole('img', { name: 'Soft tyre' })).toBeTruthy()
  expect(within(row).getByText('4 laps')).toBeTruthy()
  expect(within(row).queryByRole('img', { name: 'Finished' })).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Sectors' }))
  expect(row.querySelector('.live-leaderboard__sectors')).toBeTruthy()
  expect(row.querySelector('.live-leaderboard__sectors')?.children).toHaveLength(3)
})

test('hides prior-phase eliminations until final Q3 classification is complete', () => {
  // Arrange
  const q2 = qualifyingFixture(20, 'Q2')
  const q3 = qualifyingFixture(20, 'Q3')

  // Act
  render(<QualifyingClassificationPanel {...q2} />)

  // Assert
  expect(screen.queryByRole('button', { name: 'Select Driver D15' })).toBeNull()
  expect(screen.getAllByRole('row')).toHaveLength(16)

  cleanup()
  render(<QualifyingClassificationPanel {...q3} />)
  expect(screen.queryByRole('button', { name: 'Select Driver D15' })).toBeNull()
  expect(screen.getAllByRole('row')).toHaveLength(11)

  cleanup()
  render(<QualifyingClassificationPanel {...q3} snapshot={{ ...q3.snapshot, sessionTimeMs: 300 }} />)
  expect(screen.getByRole('button', { name: 'Select Driver D15' })).toBeTruthy()
  expect(screen.getAllByRole('row')).toHaveLength(21)
})

test('renders a no-time Q3 elimination as OUT without finish styling', () => {
  // Arrange
  const phase = qualifyingFixture(20, 'Q3')
  const noTimeSummary: QualifyingSummary = {
    ...phase.qualifyingSummary,
    drivers: {
      ...phase.qualifyingSummary.drivers,
      D00: { ...phase.qualifyingSummary.drivers.D00, q3TimeMs: [null], bestLapTimeMs: [null] },
    },
  }
  const v2Sidecar = phase.lapSectorSidecar as Extract<LapSectorSidecar, { contractVersion: 'v2' }>
  const noTimeSidecar: Extract<LapSectorSidecar, { contractVersion: 'v2' }> = {
    ...v2Sidecar,
    drivers: {
      ...v2Sidecar.drivers,
      D00: { ...v2Sidecar.drivers.D00, lapDurationMs: [100, 200, null] },
    },
  }

  // Act
  render(<QualifyingClassificationPanel {...phase} snapshot={{ ...phase.snapshot, sessionTimeMs: 300 }} qualifyingSummary={noTimeSummary} lapSectorSidecar={noTimeSidecar} />)

  // Assert
  const row = qualifyingRowForDriver('D00')
  expect(within(row).getAllByRole('cell')[0].textContent).toBe('OUT')
  expect(within(row).getAllByRole('cell')[2].textContent).toBe('OUT')
  expect(row.querySelector('.live-leaderboard__gap--finished')).toBeNull()
  expect(row.querySelector('.live-leaderboard__qualifying-lap-time--finished')).toBeNull()
})

test('renders the active elimination cutoff for 20 and 22 driver Q1 fields only at the formula position', () => {
  for (const driverCount of [20, 22]) {
    cleanup()
    const phase = qualifyingFixture(driverCount, 'Q1')
    render(<QualifyingClassificationPanel {...phase} />)

    const cutoff = Math.floor(driverCount / 2) + 5
    expect(qualifyingRowForDriver(`D${String(cutoff - 1).padStart(2, '0')}`).className).toContain('live-leaderboard__row--qualifying-cutoff')
    expect(screen.getAllByRole('row').filter((row) => row.className.includes('live-leaderboard__row--qualifying-cutoff'))).toHaveLength(1)
  }
})

test('renders the Q2 cutoff at P10 and no elimination line in Q3', () => {
  const q2 = qualifyingFixture(20, 'Q2')
  render(<QualifyingClassificationPanel {...q2} />)
  expect(qualifyingRowForDriver('D09').className).toContain('live-leaderboard__row--qualifying-cutoff')

  cleanup()
  const q3 = qualifyingFixture(20, 'Q3')
  render(<QualifyingClassificationPanel {...q3} />)
  expect(screen.getAllByRole('row').some((row) => row.className.includes('live-leaderboard__row--qualifying-cutoff'))).toBe(false)
})

test('shows a parked marker only for a sampled pit-lane driver', () => {
  // Arrange
  const current = {
    ...snapshot,
    drivers: {
      HAM: sampledDriver({ isInPitLane: true }),
      VER: sampledDriver({ isInPitLane: false }),
    },
  }

  // Act
  render(<QualifyingClassificationPanel snapshot={current} drivers={drivers} sessionMode="qualifying" qualifyingSummary={summaryForDrivers()} />)

  // Assert
  expect(qualifyingRowForDriver('HAM').querySelector('.live-leaderboard__parked-indicator')?.textContent).toBe('P')
  expect(qualifyingRowForDriver('HAM').querySelector('.live-leaderboard__parked-indicator')?.getAttribute('aria-label')).toBe('Parked')
  expect(qualifyingRowForDriver('VER').querySelector('.live-leaderboard__parked-indicator')).toBeNull()
})

test('does not show a parked marker for moving or missing telemetry', () => {
  // Arrange
  const current = {
    ...snapshot,
    drivers: { HAM: sampledDriver({ isInPitLane: false }) },
  }

  // Act
  render(<QualifyingClassificationPanel snapshot={current} drivers={drivers} sessionMode="qualifying" qualifyingSummary={summaryForDrivers()} />)

  // Assert
  expect(qualifyingRowForDriver('HAM').querySelector('.live-leaderboard__parked-indicator')).toBeNull()
  expect(qualifyingRowForDriver('VER').querySelector('.live-leaderboard__parked-indicator')).toBeNull()
})

test('reuses race tyre icons and age semantics for every qualifying compound', () => {
  // Arrange
  const tyreCases = [
    { compound: 'SOFT', label: 'Soft tyre', age: 0, ageText: '0 laps' },
    { compound: 'MEDIUM', label: 'Medium tyre', age: 1, ageText: '1 lap' },
    { compound: 'HARD', label: 'Hard tyre', age: 2, ageText: '2 laps' },
    { compound: 'INTERMEDIATE', label: 'Intermediate tyre', age: 12, ageText: '12 laps' },
    { compound: 'WET', label: 'Wet tyre', age: 3, ageText: '3 laps' },
  ] as const
  const { rerender } = render(<QualifyingClassificationPanel snapshot={{ ...snapshot, drivers: { HAM: sampledDriver({ tyreCompound: tyreCases[0].compound, tyreAge: tyreCases[0].age }) } }} drivers={drivers} sessionMode="qualifying" qualifyingSummary={summaryForDrivers()} />)
  fireEvent.click(screen.getByRole('button', { name: 'Tyres' }))

  // Act and assert
  for (const tyreCase of tyreCases) {
    rerender(<QualifyingClassificationPanel snapshot={{ ...snapshot, drivers: { HAM: sampledDriver({ tyreCompound: tyreCase.compound, tyreAge: tyreCase.age }) } }} drivers={drivers} sessionMode="qualifying" qualifyingSummary={summaryForDrivers()} />)
    const row = qualifyingRowForDriver('HAM')
    expect(within(row).getByRole('img', { name: tyreCase.label })).toBeTruthy()
    expect(within(row).getByText(tyreCase.ageText)).toBeTruthy()
  }
})

function summaryForDrivers() {
  return {
    contractVersion: 'v2' as const,
    fixtureId: 'test',
    drivers: {
      HAM: { qualifyingPosition: [1], q1TimeMs: [90_000], q2TimeMs: [89_000], q3TimeMs: [88_000], bestLapNumber: [3], bestLapTimeMs: [88_000] },
      VER: { qualifyingPosition: [2], q1TimeMs: [91_000], q2TimeMs: [90_000], q3TimeMs: [89_000], bestLapNumber: [3], bestLapTimeMs: [89_000] },
    },
  }
}

function sampledDriver(overrides: Partial<ReplaySnapshot['drivers'][string]> = {}) {
  return {
    x: null,
    y: null,
    trackDistanceMeters: null,
    speed: null,
    throttle: null,
    brake: null,
    gapToLeaderMs: null,
    lap: null,
    position: null,
    gear: null,
    drs: null,
    tyreCompound: 'SOFT',
    tyreAge: 0,
    status: 'OnTrack',
    isInPitLane: null,
    ...overrides,
  }
}

function qualifyingRowForDriver(code: string): HTMLElement {
  const row = screen.getAllByRole('row').slice(1).find((candidate) => within(candidate).getByRole('rowheader').querySelector('button')?.textContent === code)
  if (row === undefined) throw new Error(`Missing qualifying row for ${code}`)
  return row
}

function qualifyingFixture(driverCount: number, phase: 'Q1' | 'Q2' | 'Q3') {
  const ids = Array.from({ length: driverCount }, (_, index) => `D${String(index).padStart(2, '0')}`)
  const metadata = ids.map((id, index) => ({ id, displayName: `Driver ${id}`, teamName: `Team ${index}`, colorHex: '#3671c6', carNumber: String(index + 1) }))
  const sidecarDrivers = Object.fromEntries(ids.map((id, index) => [id, {
    lapNumber: [1, 2, 3], lapStartMs: [0, 100, 200], lapEndMs: [50, 150, 250],
    lapDurationMs: [100 + index, 200 + index, 300 + index],
    sector1DurationMs: [null, null, null], sector2DurationMs: [null, null, null], sector3DurationMs: [null, null, null],
    sector1SessionTimeMs: [null, null, null], sector2SessionTimeMs: [null, null, null], sector3SessionTimeMs: [null, null, null],
     qualifyingPhase: ['Q1', 'Q2', 'Q3'] as const,
     lapKind: ['flying', 'flying', 'flying'] as const,
  }]))
  const summaryDrivers = Object.fromEntries(ids.map((id, index) => [id, {
    qualifyingPosition: [index + 1], q1TimeMs: [100 + index], q2TimeMs: [200 + index], q3TimeMs: [300 + index], bestLapNumber: [3], bestLapTimeMs: [300 + index],
  }]))
  const statusDrivers = Object.fromEntries(ids.map((id) => [id, {
    lapNumber: [1, 2, 3], lapStartMs: [0, 100, 200], lapEndMs: [50, 150, 250], status: ['valid', 'valid', 'valid'] as const, deletedReason: [null, null, null],
  }]))
  const sessionTimeMs = phase === 'Q1' ? 50 : phase === 'Q2' ? 150 : 220
  return {
    snapshot: {
      sessionTimeMs, leaderboardOrder: ids, trackStatusCode: null, weatherState: null, events: [],
      drivers: Object.fromEntries(ids.map((id) => [id, sampledDriver({ lap: phase === 'Q1' ? 1 : phase === 'Q2' ? 2 : 3 })])),
    } as ReplaySnapshot,
    drivers: metadata,
    sessionMode: 'qualifying' as const,
    qualifyingSummary: { contractVersion: 'v2' as const, fixtureId: 'test', drivers: summaryDrivers } as QualifyingSummary,
    qualifyingLapStatus: { contractVersion: 'v2' as const, fixtureId: 'test', drivers: statusDrivers, events: [] } as QualifyingLapStatusSidecar,
    lapSectorSidecar: { contractVersion: 'v2' as const, fixtureId: 'test', phaseBoundaries: [{ phase: 'Q1' as const, startMs: 0 }, { phase: 'Q2' as const, startMs: 100 }, { phase: 'Q3' as const, startMs: 200 }], drivers: sidecarDrivers } as LapSectorSidecar,
    replayEndMs: 300,
  }
}
