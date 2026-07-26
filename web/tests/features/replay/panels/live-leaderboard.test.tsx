/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { LiveLeaderboard } from '../../../../src/features/replay/panels/LiveLeaderboard'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'
import type { LapSectorSidecar } from '../../../../src/data/replay/types'

const drivers = Object.freeze([
  Object.freeze({ id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' }),
  Object.freeze({ id: 'NOR', displayName: 'Lando Norris', teamName: 'McLaren', colorHex: '#ff8000', carNumber: '4' }),
  Object.freeze({ id: 'HAM', displayName: 'Lewis Hamilton', teamName: 'Ferrari', colorHex: '#e8002d', carNumber: '44' }),
])

afterEach(cleanup)

function snapshot(overrides: Partial<ReplaySnapshot> = {}): ReplaySnapshot {
  return {
    sessionTimeMs: 0, leaderboardOrder: ['VER', 'NOR'], trackStatusCode: null, weatherState: null, events: [],
    drivers: {
      VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 0, lap: null, position: 1, gear: null, drs: null, tyreCompound: 'SOFT', status: 'OnTrack', isInPitLane: false },
      NOR: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 1_234, lap: null, position: 2, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'on_track', isInPitLane: true },
    },
    ...overrides,
  }
}

test('renders sampled order with four broadcast zones and accessible driver identities', () => {
  render(<LiveLeaderboard snapshot={snapshot({ leaderboardOrder: ['NOR', 'VER'] })} drivers={drivers} />)

  const rows = screen.getAllByRole('row').slice(1)
  const table = screen.getByRole('table', { name: 'Live race leaderboard' })
  const columns = Array.from(table.querySelectorAll('col'))
  expect(rows.map((row) => within(row).getByRole('rowheader').textContent)).toEqual(['NOR', 'VER', 'HAM'])
  expect(within(rows[0]).getByRole('rowheader', { name: 'Lando Norris' }).getAttribute('title')).toBe('Lando Norris')
  expect(within(rows[0]).getAllByRole('cell')).toHaveLength(3)
  expect(columns.map((column) => column.className)).toEqual([
    'live-leaderboard__column--position',
    'live-leaderboard__column--team-accent',
    'live-leaderboard__column--driver',
    'live-leaderboard__column--metric',
  ])
  expect(within(rows[0]).getAllByRole('cell')[0].className).toBe('live-leaderboard__position')
  expect(within(rows[0]).getAllByRole('cell')[1].className).toBe('live-leaderboard__team-accent')
  expect(rows[0].textContent).toContain('PIT')
  expect(rows[1].textContent).toContain('Leader')
})

test('uses a meaningful raw status as the metric override while RUNNING remains a timing state', () => {
  render(<LiveLeaderboard snapshot={snapshot({ drivers: { VER: { ...snapshot().drivers.VER, status: 'STOPPED', isInPitLane: false, tyreCompound: null }, NOR: { ...snapshot().drivers.NOR, status: 'RUNNING', isInPitLane: null, gapToLeaderMs: null, position: null } } })} drivers={drivers} />)

  const rows = screen.getAllByRole('row').slice(1)
  expect(rows[0].textContent).toContain('STOPPED')
  expect(rows[1].textContent).toContain('—')
  expect(screen.queryByText('OUT')).toBeNull()
})

test('shows terminal OUT in the position and metric cells', () => {
  render(<LiveLeaderboard snapshot={snapshot({ drivers: { VER: { ...snapshot().drivers.VER, position: null, status: 'OUT', isInPitLane: true }, NOR: snapshot().drivers.NOR } })} drivers={drivers} />)

  const cells = within(rowForDriver('VER')).getAllByRole('cell')
  expect(cells[0].textContent).toBe('OUT')
  expect(cells[2].textContent).toBe('OUT')

  fireEvent.click(screen.getByRole('button', { name: 'Interval' }))

  expect(within(rowForDriver('VER')).getAllByRole('cell')[2].textContent).toBe('OUT')
  expect(within(rowForDriver('NOR')).getAllByRole('cell')[2].textContent).toBe('PIT')
})

test('stably moves every OUT driver behind all non-terminal drivers', () => {
  const current = snapshot({
    leaderboardOrder: ['VER', 'NOR', 'HAM'],
    drivers: {
      ...snapshot().drivers,
      VER: { ...snapshot().drivers.VER, position: null, status: 'OUT' },
      NOR: { ...snapshot().drivers.NOR, isInPitLane: false },
      HAM: { ...snapshot().drivers.NOR, position: null, status: 'out', isInPitLane: false },
    },
  })

  render(<LiveLeaderboard snapshot={current} drivers={drivers} />)

  const codes = screen.getAllByRole('row').slice(1).map((row) => within(row).getByRole('rowheader').textContent)
  expect(codes).toEqual(['NOR', 'VER', 'HAM'])
})

test('keeps legacy null-only rows in immutable manifest order with unavailable values', () => {
  const legacy = snapshot({ leaderboardOrder: null, drivers: {} })
  render(<LiveLeaderboard snapshot={legacy} drivers={drivers} />)

  expect(screen.getAllByRole('row').slice(1).map((row) => within(row).getByRole('rowheader').textContent)).toEqual(['VER', 'NOR', 'HAM'])
  expect(Object.isFrozen(drivers)).toBe(true)
  expect(legacy.leaderboardOrder).toBeNull()
  expect(screen.getAllByText('—')).toHaveLength(6)
})

test('announces unavailable loading state and exposes labelled semantic table when a snapshot is present', () => {
  const { rerender } = render(<LiveLeaderboard snapshot={null} drivers={drivers} />)
  expect(screen.getByRole('status').textContent).toContain('unavailable')

  rerender(<LiveLeaderboard snapshot={snapshot()} drivers={drivers} />)
  expect(screen.getByRole('region', { name: 'Leaderboard' })).toBeTruthy()
  expect(screen.getByRole('table', { name: 'Live race leaderboard' })).toBeTruthy()
  expect(screen.getAllByRole('columnheader').map((header) => header.textContent)).toEqual(['Position', 'Team colour', 'Driver', 'Leader gap'])
})

test('does not create a leaderboard row for an active safety-car track status', () => {
  render(<LiveLeaderboard snapshot={snapshot({ trackStatusCode: 4 })} drivers={drivers} />)

  const rows = screen.getAllByRole('row').slice(1)
  expect(rows).toHaveLength(3)
  expect(rows.map((row) => within(row).getByRole('rowheader').textContent)).toEqual(['VER', 'NOR', 'HAM'])
  expect(screen.queryByRole('rowheader', { name: 'Safety Car' })).toBeNull()
})

test('switches from cumulative leader gaps to intervals between adjacent positions', () => {
  const current = snapshot({
    leaderboardOrder: ['VER', 'NOR', 'HAM'],
    drivers: {
      ...snapshot().drivers,
      NOR: { ...snapshot().drivers.NOR, status: 'on_track', isInPitLane: false },
      HAM: { ...snapshot().drivers.NOR, status: 'ON TRACK', gapToLeaderMs: 3_000, position: 3, isInPitLane: false },
    },
  })
  render(<LiveLeaderboard snapshot={current} drivers={drivers} />)

  fireEvent.click(screen.getByRole('button', { name: 'Interval' }))

  const rows = screen.getAllByRole('row').slice(1)
  expect(screen.getByRole('button', { name: 'Interval' }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.getAllByRole('columnheader').at(-1)?.textContent).toBe('Interval')
  expect(rows[0].textContent).toContain('Interval')
  expect(rows[1].textContent).toContain('+1.234')
  expect(rows[2].textContent).toContain('+1.766')
})

test('renders the selected tyre image and sampled tyre age in Tyres mode', () => {
  const current = snapshot({
    drivers: {
      ...snapshot().drivers,
      VER: { ...snapshot().drivers.VER, tyreCompound: 'soft', tyreAge: 12 },
      NOR: { ...snapshot().drivers.NOR, tyreCompound: 'MEDIUM', tyreAge: 3 },
    },
  })
  render(<LiveLeaderboard snapshot={current} drivers={drivers} />)

  fireEvent.click(screen.getByRole('button', { name: 'Tyres' }))

  expect(screen.getAllByRole('columnheader').at(-1)?.textContent).toBe('Tyres')
  expect(within(rowForDriver('VER')).getByRole('img', { name: 'Soft tyre' })).toBeTruthy()
  expect(within(rowForDriver('VER')).getByText('12 laps')).toBeTruthy()
  expect(within(rowForDriver('NOR')).getByRole('img', { name: 'Medium tyre' })).toBeTruthy()
  expect(within(rowForDriver('NOR')).getByText('3 laps')).toBeTruthy()
})

test('shows an explicit unavailable fallback for missing or unrecognized tyre data', () => {
  const current = snapshot({
    drivers: {
      ...snapshot().drivers,
      VER: { ...snapshot().drivers.VER, tyreCompound: 'UNKNOWN', tyreAge: 4 },
      NOR: { ...snapshot().drivers.NOR, tyreCompound: 'MEDIUM', tyreAge: null },
    },
  })
  render(<LiveLeaderboard snapshot={current} drivers={drivers} />)

  fireEvent.click(screen.getByRole('button', { name: 'Tyres' }))

  expect(within(rowForDriver('VER')).getByRole('cell', { name: 'Tyres unavailable' }).textContent).toBe('Unavailable')
  expect(within(rowForDriver('VER')).queryByRole('img')).toBeNull()
  expect(within(rowForDriver('NOR')).getByRole('cell', { name: 'Tyres unavailable' }).textContent).toBe('Unavailable')
  expect(within(rowForDriver('NOR')).queryByRole('img')).toBeNull()
})

test('shows OUT instead of tyre or sector data for terminal drivers', () => {
  const current = sectorSnapshot()
  const withTerminalDriver: ReplaySnapshot = {
    ...current,
    drivers: {
      ...current.drivers,
      NOR: { ...current.drivers.NOR, status: 'OUT', position: null, tyreCompound: null, tyreAge: null },
    },
  }
  render(<LiveLeaderboard snapshot={withTerminalDriver} drivers={drivers} lapSectorSidecar={buildSectorSidecar()} />)

  fireEvent.click(screen.getByRole('button', { name: 'Tyres' }))
  expect(within(rowForDriver('NOR')).getAllByRole('cell')[2].textContent).toBe('OUT')
  expect(within(rowForDriver('NOR')).queryByText('Unavailable')).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Sectors' }))
  const terminalSectorCell = within(rowForDriver('NOR')).getAllByRole('cell')[2]
  expect(terminalSectorCell.textContent).toBe('OUT')
  expect(terminalSectorCell.className).not.toContain('live-leaderboard__gap--sectors')
  expect(rowForDriver('NOR').querySelectorAll('.live-leaderboard__sector')).toHaveLength(0)
})

test('renders an accessible finish flag instead of timing, PIT, or raw status in both gap modes', () => {
  const current = snapshot({
    drivers: {
      ...snapshot().drivers,
      VER: { ...snapshot().drivers.VER, isFinished: true, status: 'STOPPED', isInPitLane: true, gapToLeaderMs: 0, position: 1 },
      NOR: { ...snapshot().drivers.NOR, status: 'on_track', isInPitLane: false },
    },
  })
  render(<LiveLeaderboard snapshot={current} drivers={drivers} />)

  const metric = () => within(rowForDriver('VER')).getAllByRole('cell')[2]
  expect(within(metric()).getByRole('img', { name: 'Finished' }).getAttribute('aria-label')).toBe('Finished')

  fireEvent.click(screen.getByRole('button', { name: 'Interval' }))

  expect(within(metric()).getByRole('img', { name: 'Finished' })).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: 'Tyres' }))

  expect(within(metric()).getByRole('img', { name: 'Finished' })).toBeTruthy()
})

test('keeps a finished P1 leader distinct from OUT rows and in sampled order', () => {
  const current = snapshot({
    leaderboardOrder: ['VER', 'NOR', 'HAM'],
    drivers: {
      ...snapshot().drivers,
      VER: { ...snapshot().drivers.VER, isFinished: true, status: 'OUT', position: 1, isInPitLane: false },
      NOR: { ...snapshot().drivers.NOR, status: 'on_track', isInPitLane: false, position: 2 },
      HAM: { ...snapshot().drivers.NOR, status: 'OUT', isInPitLane: false, position: null },
    },
  })
  render(<LiveLeaderboard snapshot={current} drivers={drivers} />)

  const rows = screen.getAllByRole('row').slice(1)
  expect(rows.map((row) => within(row).getByRole('rowheader').textContent)).toEqual(['VER', 'NOR', 'HAM'])
  expect(within(rowForDriver('VER')).getAllByRole('cell')[0].textContent).toBe('1')
  expect(within(rowForDriver('VER')).getByRole('img', { name: 'Finished' })).toBeTruthy()
  expect(rowForDriver('VER').className).not.toContain('live-leaderboard__row--terminal')
  expect(within(rowForDriver('HAM')).getAllByRole('cell')[0].textContent).toBe('OUT')
  expect(within(rowForDriver('HAM')).getAllByRole('cell')[2].textContent).toBe('OUT')
})

test('shows interval as unavailable when adjacent cumulative gaps cannot produce a valid delta', () => {
  const current = snapshot({
    leaderboardOrder: ['VER', 'NOR', 'HAM'],
    drivers: {
      ...snapshot().drivers,
      HAM: { ...snapshot().drivers.NOR, gapToLeaderMs: 1_000, position: 3, isInPitLane: false },
    },
  })
  render(<LiveLeaderboard snapshot={current} drivers={drivers} />)

  fireEvent.click(screen.getByRole('button', { name: 'Interval' }))

  expect(screen.getAllByRole('row')[3].textContent).toContain('—')
})

test('uses a validated dedicated team-colour cell and subdues terminal rows without changing their table semantics', () => {
  render(<LiveLeaderboard snapshot={snapshot({ drivers: { VER: { ...snapshot().drivers.VER, status: 'OUT' }, NOR: snapshot().drivers.NOR } })} drivers={[{ ...drivers[0], colorHex: 'invalid-colour' }, ...drivers.slice(1)]} />)

  const terminalRow = rowForDriver('VER')
  const activeRow = rowForDriver('NOR')
  expect(terminalRow.className).toContain('live-leaderboard__row--terminal')
  expect(terminalRow.getAttribute('style')).toContain('--live-leaderboard-team-color: #7a8794')
  expect(activeRow.getAttribute('style')).toContain('--live-leaderboard-team-color: #ff8000')
  expect(within(terminalRow).getAllByRole('cell')[1].className).toContain('live-leaderboard__team-accent')
})

test('selects a driver through its accessible identity control and highlights the row', () => {
  const onDriverSelect = vi.fn()
  render(<LiveLeaderboard snapshot={snapshot()} drivers={drivers} selectedDriverId="NOR" onDriverSelect={onDriverSelect} />)

  const selected = screen.getByRole('button', { name: 'Select Lando Norris' })
  expect(selected.getAttribute('aria-pressed')).toBe('true')
  expect(rowForDriver('NOR').className).toContain('live-leaderboard__row--selected')
  fireEvent.click(selected)
  expect(onDriverSelect).toHaveBeenCalledWith('NOR')
})

test('adds a Sectors toggle button with keyboard-accessible aria-pressed state and switches the metric header', () => {
  render(<LiveLeaderboard snapshot={snapshot()} drivers={drivers} />)

  const sectorsButton = screen.getByRole('button', { name: 'Sectors' })
  expect(sectorsButton.getAttribute('aria-pressed')).toBe('false')
  expect(screen.getAllByRole('columnheader').at(-1)?.textContent).toBe('Leader gap')

  fireEvent.click(sectorsButton)

  expect(sectorsButton.getAttribute('aria-pressed')).toBe('true')
  expect(screen.getByRole('button', { name: 'Leader' }).getAttribute('aria-pressed')).toBe('false')
  expect(screen.getAllByRole('columnheader').at(-1)?.textContent).toBe('Sectors')

  fireEvent.click(screen.getByRole('button', { name: 'Interval' }))

  expect(sectorsButton.getAttribute('aria-pressed')).toBe('false')
  expect(screen.getByRole('button', { name: 'Interval' }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.getAllByRole('columnheader').at(-1)?.textContent).toBe('Interval')
})

test('renders all four causal sector colour states with accessible labels and formatted times', () => {
  render(<LiveLeaderboard snapshot={sectorSnapshot()} drivers={drivers} lapSectorSidecar={buildSectorSidecar()} />)

  fireEvent.click(screen.getByRole('button', { name: 'Sectors' }))

  const verSectorElements = rowForDriver('VER').querySelectorAll('.live-leaderboard__sector')
  expect(verSectorElements).toHaveLength(3)
  expect(verSectorElements[0].getAttribute('aria-label')).toBe('S1 26.500')
  expect(verSectorElements[0].className).toContain('live-leaderboard__sector--session-best')
  expect(verSectorElements[0].textContent).toContain('S1')
  expect(verSectorElements[0].textContent).toContain('26.500')
  expect(verSectorElements[1].getAttribute('aria-label')).toBe('S2 unavailable')
  expect(verSectorElements[1].className).toContain('live-leaderboard__sector--unavailable')
  expect(verSectorElements[1].textContent).toContain('—')
  expect(verSectorElements[2].getAttribute('aria-label')).toBe('S3 unavailable')
  expect(verSectorElements[2].className).toContain('live-leaderboard__sector--unavailable')

  const norSectorElements = rowForDriver('NOR').querySelectorAll('.live-leaderboard__sector')
  expect(norSectorElements).toHaveLength(3)
  expect(norSectorElements[0].getAttribute('aria-label')).toBe('S1 28.000')
  expect(norSectorElements[0].className).toContain('live-leaderboard__sector--slower')
  expect(norSectorElements[0].textContent).toContain('28.000')
  expect(norSectorElements[1].getAttribute('aria-label')).toBe('S2 31.500')
  expect(norSectorElements[1].className).toContain('live-leaderboard__sector--personal-best')
  expect(norSectorElements[1].textContent).toContain('31.500')
  expect(norSectorElements[2].getAttribute('aria-label')).toBe('S3 unavailable')
  expect(norSectorElements[2].className).toContain('live-leaderboard__sector--unavailable')
  expect(norSectorElements[2].textContent).toContain('—')

  const hamSectorElements = rowForDriver('HAM').querySelectorAll('.live-leaderboard__sector')
  expect(hamSectorElements).toHaveLength(3)
  expect(hamSectorElements[0].getAttribute('aria-label')).toBe('S1 unavailable')
  expect(hamSectorElements[0].className).toContain('live-leaderboard__sector--unavailable')
  expect(hamSectorElements[1].getAttribute('aria-label')).toBe('S2 unavailable')
  expect(hamSectorElements[1].className).toContain('live-leaderboard__sector--unavailable')
  expect(hamSectorElements[2].getAttribute('aria-label')).toBe('S3 unavailable')
  expect(hamSectorElements[2].className).toContain('live-leaderboard__sector--unavailable')
})

test('enforces the causal boundary: sectors completed after the cursor are unavailable', () => {
  const earlySnapshot = sectorSnapshot(85_000)
  render(<LiveLeaderboard snapshot={earlySnapshot} drivers={drivers} lapSectorSidecar={buildSectorSidecar()} />)

  fireEvent.click(screen.getByRole('button', { name: 'Sectors' }))

  const verSectorElements = rowForDriver('VER').querySelectorAll('.live-leaderboard__sector')
  expect(verSectorElements).toHaveLength(3)
  expect(verSectorElements[0].getAttribute('aria-label')).toBe('S1 28.000')
  expect(verSectorElements[1].getAttribute('aria-label')).toBe('S2 32.000')
  expect(verSectorElements[2].getAttribute('aria-label')).toBe('S3 25.000')

  const norSectorElements = rowForDriver('NOR').querySelectorAll('.live-leaderboard__sector')
  expect(norSectorElements).toHaveLength(3)
  expect(norSectorElements[0].getAttribute('aria-label')).toBe('S1 27.500')
  expect(norSectorElements[1].getAttribute('aria-label')).toBe('S2 33.000')
  expect(norSectorElements[2].getAttribute('aria-label')).toBe('S3 unavailable')
  expect(norSectorElements[2].textContent).toContain('—')

  const hamSectorElements = rowForDriver('HAM').querySelectorAll('.live-leaderboard__sector')
  expect(hamSectorElements[2].getAttribute('aria-label')).toBe('S3 unavailable')
})

test('shows all sectors as unavailable when no sidecar data is provided', () => {
  render(<LiveLeaderboard snapshot={sectorSnapshot()} drivers={drivers} />)

  fireEvent.click(screen.getByRole('button', { name: 'Sectors' }))

  for (const code of ['VER', 'NOR', 'HAM']) {
    const sectorElements = rowForDriver(code).querySelectorAll('.live-leaderboard__sector')
    expect(sectorElements).toHaveLength(3)
    for (const element of sectorElements) {
      expect(element.getAttribute('aria-label')).toMatch(/S[123] unavailable/)
      expect(element.className).toContain('live-leaderboard__sector--unavailable')
    }
  }
})

test('shows all sectors as unavailable when sidecar is explicitly null', () => {
  render(<LiveLeaderboard snapshot={sectorSnapshot()} drivers={drivers} lapSectorSidecar={null} />)

  fireEvent.click(screen.getByRole('button', { name: 'Sectors' }))

  const verSectorElements = rowForDriver('VER').querySelectorAll('.live-leaderboard__sector')
  expect(verSectorElements).toHaveLength(3)
  for (const element of verSectorElements) {
    expect(element.className).toContain('live-leaderboard__sector--unavailable')
  }
})

test('removes future sector times when the replay cursor moves backward in Sectors mode', () => {
  const { rerender } = render(<LiveLeaderboard snapshot={sectorSnapshot(200_000)} drivers={drivers} lapSectorSidecar={buildSectorSidecar()} />)

  fireEvent.click(screen.getByRole('button', { name: 'Sectors' }))

  // At session 200_000: VER lap 3 S1=26.500 is session-best; NOR S3 unavailable (lap 2 incomplete)
  const verSectorsEarly = rowForDriver('VER').querySelectorAll('.live-leaderboard__sector')
  expect(verSectorsEarly[0].getAttribute('aria-label')).toBe('S1 26.500')
  expect(verSectorsEarly[0].className).toContain('live-leaderboard__sector--session-best')

  const norSectorsEarly = rowForDriver('NOR').querySelectorAll('.live-leaderboard__sector')
  expect(norSectorsEarly[0].className).toContain('live-leaderboard__sector--slower')

  // Rewind to session 85_000: VER loses laps 2-3, NOR loses lap 2
  rerender(<LiveLeaderboard snapshot={sectorSnapshot(85_000)} drivers={drivers} lapSectorSidecar={buildSectorSidecar()} />)

  // VER S1 drops from session-best (26.500) to personal-best (28.000) because NOR S1=27.500 is now the session best
  const verSectorsLate = rowForDriver('VER').querySelectorAll('.live-leaderboard__sector')
  expect(verSectorsLate[0].getAttribute('aria-label')).toBe('S1 28.000')
  expect(verSectorsLate[0].className).not.toContain('live-leaderboard__sector--session-best')
  expect(verSectorsLate[0].className).toContain('live-leaderboard__sector--personal-best')

  // NOR S1 gains session-best (27.500) because VER's faster laps are no longer causal
  const norSectorsLate = rowForDriver('NOR').querySelectorAll('.live-leaderboard__sector')
  expect(norSectorsLate[0].getAttribute('aria-label')).toBe('S1 27.500')
  expect(norSectorsLate[0].className).toContain('live-leaderboard__sector--session-best')
  expect(norSectorsLate[2].getAttribute('aria-label')).toBe('S3 unavailable')
  expect(norSectorsLate[2].className).toContain('live-leaderboard__sector--unavailable')
})

function sectorSnapshot(sessionTimeMs = 200_000): ReplaySnapshot {
  const driver = (position: number, gapToLeaderMs: number) => ({
    x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null,
    gapToLeaderMs, lap: null, position, gear: null, drs: null, tyreCompound: null, status: 'OnTrack', isInPitLane: false,
  })
  return {
    sessionTimeMs,
    leaderboardOrder: ['VER', 'NOR', 'HAM'],
    trackStatusCode: null,
    weatherState: null,
    events: [],
    drivers: { VER: driver(1, 0), NOR: driver(2, 5_000), HAM: driver(3, 10_000) },
  }
}

function buildSectorSidecar(): LapSectorSidecar {
  return {
    contractVersion: 'v1',
    fixtureId: 'test',
    drivers: {
      VER: {
        lapNumber: [1, 2, 3],
        lapStartMs: [0, 85_000, 168_000],
        lapEndMs: [85_000, 168_000, 255_000],
        lapDurationMs: [85_000, 83_000, null],
        sector1DurationMs: [28_000, 27_000, 26_500],
        sector2DurationMs: [32_000, 31_000, null],
        sector3DurationMs: [25_000, 24_000, null],
        sector1SessionTimeMs: [28_000, 112_000, 195_000],
        sector2SessionTimeMs: [60_000, 143_000, null],
        sector3SessionTimeMs: [85_000, 168_000, null],
      },
      NOR: {
        lapNumber: [1, 2],
        lapStartMs: [0, 88_000],
        lapEndMs: [88_000, 250_000],
        lapDurationMs: [88_000, null],
        sector1DurationMs: [27_500, 28_000],
        sector2DurationMs: [33_000, 31_500],
        sector3DurationMs: [26_000, null],
        sector1SessionTimeMs: [27_500, 116_000],
        sector2SessionTimeMs: [62_000, 147_500],
        sector3SessionTimeMs: [88_000, null],
      },
    },
  }
}

function rowForDriver(code: string): HTMLElement {
  const row = screen.getAllByRole('row').slice(1).find((candidate) => within(candidate).getByRole('rowheader').textContent === code)
  if (row === undefined) throw new Error(`Missing leaderboard row for ${code}`)
  return row
}
