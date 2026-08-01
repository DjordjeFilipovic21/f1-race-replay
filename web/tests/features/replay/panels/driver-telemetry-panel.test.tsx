/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { DriverTelemetryPanel, formatDrs } from '../../../../src/features/replay/panels/DriverTelemetryPanel'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'

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

  expect(screen.getByRole('heading', { name: 'Live telemetry' })).toBeTruthy()
  expect(screen.getByRole('img', { name: /Speed 287 kilometers per hour, RPM 11,450, Throttle 82%, Brake Applied, Gear 7, DRS Active/ })).toBeTruthy()
  expect(screen.queryByText('Max Verstappen')).toBeNull()
  expect(screen.queryByText('#1')).toBeNull()
  expect(screen.getByText('287')).toBeTruthy()
  expect(screen.getByText('11,450')).toBeTruthy()
  expect(screen.getByText('DRS')).toBeTruthy()
  expect(screen.getByText('Throttle').tagName.toLowerCase()).toBe('textpath')
  expect(screen.getByText('Brake').tagName.toLowerCase()).toBe('textpath')
  expect(screen.queryByText('Leader')).toBeNull()
  expect(screen.queryByText('Lap')).toBeNull()
  expect(screen.queryByText('Gap')).toBeNull()
})

test('shows an accessible empty state when no driver is selected', () => {
  render(<DriverTelemetryPanel drivers={drivers} selectedDriverId={null} snapshot={snapshot} />)

  expect(screen.getByRole('status').textContent).toContain('Driver telemetry is unavailable')
})

test('preserves absent RPM as unavailable rather than zero', () => {
  const legacySnapshot: ReplaySnapshot = { ...snapshot, drivers: { VER: { ...snapshot.drivers.VER, rpm: null } } }
  render(<DriverTelemetryPanel drivers={drivers} selectedDriverId="VER" snapshot={legacySnapshot} />)

  expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  expect(screen.getByRole('img', { name: /RPM Unavailable/ })).toBeTruthy()
  expect(screen.queryByText('0 RPM')).toBeNull()
})

test('highlights the DRS badge only while DRS is active', () => {
  const { rerender } = render(<DriverTelemetryPanel drivers={drivers} selectedDriverId="VER" snapshot={snapshot} />)

  expect(screen.getByText('DRS').closest('g')?.classList.contains('driver-telemetry-panel__drs--active')).toBe(true)

  const inactiveSnapshot: ReplaySnapshot = { ...snapshot, drivers: { VER: { ...snapshot.drivers.VER, drs: 0 } } }
  rerender(<DriverTelemetryPanel drivers={drivers} selectedDriverId="VER" snapshot={inactiveSnapshot} />)

  expect(screen.getByText('DRS').closest('g')?.classList.contains('driver-telemetry-panel__drs--active')).toBe(false)
  expect(screen.queryByText(/DRS Off/)).toBeNull()
})

test('maps only documented DRS codes and marks all other codes unknown', () => {
  expect(formatDrs(0)).toBe('Off')
  expect(formatDrs(1)).toBe('Off')
  expect(formatDrs(8)).toBe('Eligible')
  expect(formatDrs(10)).toBe('Active')
  expect(formatDrs(12)).toBe('Active')
  expect(formatDrs(14)).toBe('Active')
  expect(formatDrs(2)).toBe('Unknown')
  expect(formatDrs(null)).toBe('—')
})

test('renders 2026 DRS as not published instead of treating zero as Off', () => {
  const zeroDrsSnapshot: ReplaySnapshot = { ...snapshot, drivers: { VER: { ...snapshot.drivers.VER, drs: 0 } } }
  render(
    <DriverTelemetryPanel
      drivers={drivers}
      selectedDriverId="VER"
      seasonMetadata={{ year: 2026 }}
      telemetryCapabilities={{ drs: 'not-published', overtakeMode: 'not-published', activeAero: 'not-published', ersReplacement: 'not-published' }}
      snapshot={zeroDrsSnapshot}
    />,
  )

  expect(screen.getByRole('img', { name: /DRS \/ Overtake Mode Not published/ })).toBeTruthy()
  expect(screen.getByText('DRS').closest('g')?.classList.contains('driver-telemetry-panel__drs--unavailable')).toBe(true)
  const tooltipTrigger = screen.getByRole('button', { name: 'Why is DRS telemetry unavailable?' })
  expect(screen.queryByRole('tooltip')).toBeNull()
  fireEvent.mouseEnter(tooltipTrigger)
  expect(screen.getByRole('tooltip').textContent).toContain('Public telemetry does not contain that signal')
  fireEvent.mouseLeave(tooltipTrigger)
  expect(screen.queryByRole('tooltip')).toBeNull()
  expect(screen.queryByText('Off')).toBeNull()
})

test('uses the defensive 2026 status when capability metadata is absent', () => {
  const zeroDrsSnapshot: ReplaySnapshot = { ...snapshot, drivers: { VER: { ...snapshot.drivers.VER, drs: 0 } } }
  render(<DriverTelemetryPanel drivers={drivers} selectedDriverId="VER" seasonMetadata={{ year: 2026 }} snapshot={zeroDrsSnapshot} />)

  expect(screen.getByRole('img', { name: /DRS \/ Overtake Mode Not published/ })).toBeTruthy()
  expect(screen.getByText('DRS')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Why is DRS telemetry unavailable?' })).toBeTruthy()
  expect(screen.queryByText('Off')).toBeNull()
})

test('preserves DRS formatting when 2026 capability metadata says it is available', () => {
  const zeroDrsSnapshot: ReplaySnapshot = { ...snapshot, drivers: { VER: { ...snapshot.drivers.VER, drs: 0 } } }
  render(
    <DriverTelemetryPanel
      drivers={drivers}
      selectedDriverId="VER"
      seasonMetadata={{ year: 2026 }}
      telemetryCapabilities={{ drs: 'available', overtakeMode: 'available', activeAero: 'available', ersReplacement: 'available' }}
      snapshot={zeroDrsSnapshot}
    />,
  )

  expect(screen.getByRole('img', { name: /DRS Off/ })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Why is DRS telemetry unavailable?' })).toBeNull()
})

test('renders legacy DRS Off in the accessible label when capability metadata is absent', () => {
  const offSnapshot: ReplaySnapshot = { ...snapshot, drivers: { VER: { ...snapshot.drivers.VER, drs: 0 } } }
  render(<DriverTelemetryPanel drivers={drivers} selectedDriverId="VER" snapshot={offSnapshot} />)

  expect(screen.getByRole('img', { name: /DRS Off/ })).toBeTruthy()
  expect(screen.queryByRole('status')).toBeNull()
})

test('renders DRS Unavailable in the accessible label when the sampled DRS value is null', () => {
  const unavailableSnapshot: ReplaySnapshot = { ...snapshot, drivers: { VER: { ...snapshot.drivers.VER, drs: null } } }
  render(<DriverTelemetryPanel drivers={drivers} selectedDriverId="VER" snapshot={unavailableSnapshot} />)

  expect(screen.getByRole('img', { name: /DRS Unavailable/ })).toBeTruthy()
  expect(screen.queryByRole('status')).toBeNull()
})
