/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { LiveLeaderboard } from '../src/replay-ui/LiveLeaderboard'
import type { ReplaySnapshot } from '../src/replay-engine/types'

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
      VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 0, lap: null, position: 1, gear: null, drs: null, tyreCompound: 'SOFT', status: 'RUNNING', isInPitLane: false },
      NOR: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 1_234, lap: null, position: 2, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'RUNNING', isInPitLane: true },
    },
    ...overrides,
  }
}

test('renders dynamic sampled order, leader and follower gaps, metadata, status, and tyre', () => {
  render(<LiveLeaderboard snapshot={snapshot({ leaderboardOrder: ['NOR', 'VER'] })} drivers={drivers} />)

  const rows = screen.getAllByRole('row').slice(1)
  expect(rows.map((row) => within(row).getByRole('rowheader').textContent)).toEqual(['Lando NorrisNOR', 'Max VerstappenVER', 'Lewis HamiltonHAM'])
  expect(rows[0].textContent).toContain('+1.234')
  expect(rows[0].textContent).toContain('PIT')
  expect(rows[0].textContent).toContain('MEDIUM')
  expect(rows[1].textContent).toContain('Leader')
})

test('uses exact raw status and unavailable text without fabricating a retired state', () => {
  render(<LiveLeaderboard snapshot={snapshot({ drivers: { VER: { ...snapshot().drivers.VER, status: 'STOPPED', isInPitLane: false, tyreCompound: null }, NOR: { ...snapshot().drivers.NOR, status: null, isInPitLane: null, gapToLeaderMs: null, position: null } } })} drivers={drivers} />)

  const rows = screen.getAllByRole('row').slice(1)
  expect(rows[0].textContent).toContain('STOPPED')
  expect(rows[0].textContent).toContain('—')
  expect(rows[1].textContent).toContain('—')
  expect(screen.queryByText('OUT')).toBeNull()
})

test('keeps legacy null-only rows in immutable manifest order with unavailable values', () => {
  const legacy = snapshot({ leaderboardOrder: null, drivers: {} })
  render(<LiveLeaderboard snapshot={legacy} drivers={drivers} />)

  expect(screen.getAllByRole('row').slice(1).map((row) => within(row).getByRole('rowheader').textContent)).toEqual(['Max VerstappenVER', 'Lando NorrisNOR', 'Lewis HamiltonHAM'])
  expect(Object.isFrozen(drivers)).toBe(true)
  expect(legacy.leaderboardOrder).toBeNull()
  expect(screen.getAllByText('—')).toHaveLength(12)
})

test('announces unavailable loading state and exposes labelled semantic table when a snapshot is present', () => {
  const { rerender } = render(<LiveLeaderboard snapshot={null} drivers={drivers} />)
  expect(screen.getByRole('status').textContent).toContain('unavailable')

  rerender(<LiveLeaderboard snapshot={snapshot()} drivers={drivers} />)
  expect(screen.getByRole('region', { name: 'Leaderboard' })).toBeTruthy()
  expect(screen.getByRole('table', { name: 'Live race leaderboard' })).toBeTruthy()
  expect(screen.getAllByRole('columnheader').map((header) => header.textContent)).toEqual(['Pos', 'Driver', 'Team', 'Status', 'Tyre', 'Leader gap'])
})

test('switches from cumulative leader gaps to intervals between adjacent positions', () => {
  const current = snapshot({
    leaderboardOrder: ['VER', 'NOR', 'HAM'],
    drivers: {
      ...snapshot().drivers,
      HAM: { ...snapshot().drivers.NOR, gapToLeaderMs: 3_000, position: 3, isInPitLane: false },
    },
  })
  render(<LiveLeaderboard snapshot={current} drivers={drivers} />)

  fireEvent.click(screen.getByRole('button', { name: 'Interval' }))

  const rows = screen.getAllByRole('row').slice(1)
  expect(screen.getByRole('button', { name: 'Interval' }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.getAllByRole('columnheader').at(-1)?.textContent).toBe('Interval')
  expect(rows[0].textContent).toContain('Leader')
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
