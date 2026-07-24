/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { DriverInfoPanel } from '../../../../src/features/replay/panels/DriverInfoPanel'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'

const drivers = [
  { id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' },
  { id: 'NOR', displayName: 'Lando Norris', teamName: 'McLaren', colorHex: '#ff8000', carNumber: '4' },
]

const snapshot: ReplaySnapshot = {
  sessionTimeMs: 0, leaderboardOrder: ['VER', 'NOR'], trackStatusCode: null, weatherState: null, events: [],
  drivers: {
    VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 0, lap: 4, position: 1, gear: null, drs: null, tyreCompound: null, status: 'OnTrack', isInPitLane: false },
    NOR: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 1_000, lap: 4, position: 2, gear: null, drs: null, tyreCompound: 'medium', status: 'Running', isInPitLane: true },
  },
}

afterEach(cleanup)

test('shows the selected driver in the compact detail-card format without a nested Driver region', () => {
  const { container } = render(<DriverInfoPanel drivers={drivers} selectedDriverId="NOR" snapshot={snapshot} />)

  expect(container.querySelector('.driver-info-panel__top')?.textContent).toContain('Lando Norris')
  expect(screen.getByText('McLaren')).toBeTruthy()
  expect(screen.getByText('#4')).toBeTruthy()
  expect(screen.getByLabelText('Current position 2')).toBeTruthy()
  expect(screen.getByText('PIT')).toBeTruthy()
  expect(screen.getByText('MEDIUM')).toBeTruthy()
  expect(screen.queryByRole('region', { name: 'Driver' })).toBeNull()
  expect(screen.queryByText(/throttle|brake|speed|gear|drs/i)).toBeNull()
})

test('uses the leaderboard neutral fallback for an invalid team colour', () => {
  const { container } = render(<DriverInfoPanel drivers={[{ ...drivers[0], colorHex: 'not-a-colour' }]} selectedDriverId="VER" snapshot={snapshot} />)

  expect(container.querySelector('.driver-info-panel')?.getAttribute('style')).toContain('--driver-info-team-color: #7a8794')
})
