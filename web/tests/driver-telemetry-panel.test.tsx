/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { DriverTelemetryPanel, formatDrs } from '../src/replay-ui/DriverTelemetryPanel'
import type { ReplaySnapshot } from '../src/replay-engine/types'

const drivers = [{ id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' }]
const snapshot: ReplaySnapshot = {
  sessionTimeMs: 0, leaderboardOrder: ['VER'], trackStatusCode: null, weatherState: null, events: [],
  drivers: {
    VER: { x: null, y: null, trackDistanceMeters: null, speed: 287, rpm: 11_450, throttle: 82, brake: 1, gapToLeaderMs: 0, lap: 4, position: 1, gear: 7, drs: 14, tyreCompound: null, status: 'Running', isInPitLane: false },
  },
}

afterEach(cleanup)

test('renders telemetry for the selected driver with textual values', () => {
  render(<DriverTelemetryPanel drivers={drivers} selectedDriverId="VER" snapshot={snapshot} />)

  expect(screen.getByRole('heading', { name: /max verstappen/i })).toBeTruthy()
  expect(screen.getByText('287 km/h')).toBeTruthy()
  expect(screen.getByText('11,450 RPM')).toBeTruthy()
  expect(screen.getByText('Applied')).toBeTruthy()
  expect(screen.getByText('Active')).toBeTruthy()
  expect(screen.getByText('Leader')).toBeTruthy()
  expect(screen.queryByText(/last lap/i)).toBeNull()
})

test('shows an accessible empty state when no driver is selected', () => {
  render(<DriverTelemetryPanel drivers={drivers} selectedDriverId={null} snapshot={snapshot} />)

  expect(screen.getByRole('status').textContent).toContain('Driver telemetry is unavailable')
})

test('preserves absent RPM as unavailable rather than zero', () => {
  const legacySnapshot: ReplaySnapshot = { ...snapshot, drivers: { VER: { ...snapshot.drivers.VER, rpm: null } } }
  render(<DriverTelemetryPanel drivers={drivers} selectedDriverId="VER" snapshot={legacySnapshot} />)

  expect(screen.getAllByText('Unavailable').length).toBeGreaterThan(0)
  expect(screen.queryByText('0 RPM')).toBeNull()
})

test('maps only documented DRS codes and marks all other codes unknown', () => {
  expect(formatDrs(0)).toBe('Off')
  expect(formatDrs(1)).toBe('Off')
  expect(formatDrs(8)).toBe('Eligible')
  expect(formatDrs(10)).toBe('Active')
  expect(formatDrs(12)).toBe('Active')
  expect(formatDrs(14)).toBe('Active')
  expect(formatDrs(2)).toBe('Unknown')
  expect(formatDrs(null)).toBe('Unavailable')
})
