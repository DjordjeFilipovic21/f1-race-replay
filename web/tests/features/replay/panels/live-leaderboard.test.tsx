/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { LiveLeaderboard } from '../../../../src/features/replay/panels/LiveLeaderboard'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'

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

function rowForDriver(code: string): HTMLElement {
  const row = screen.getAllByRole('row').slice(1).find((candidate) => within(candidate).getByRole('rowheader').textContent === code)
  if (row === undefined) throw new Error(`Missing leaderboard row for ${code}`)
  return row
}
