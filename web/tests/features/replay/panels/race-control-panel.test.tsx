/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import type { ReplayEvent } from '../../../../src/data/replay/types'
import type { ReplayControllerSnapshot } from '../../../../src/engine/replay'
import {
  formatRaceControlMessage,
  formatTrackStatus,
  formatWeatherState,
  RaceControlPanel,
} from '../../../../src/features/replay/panels/RaceControlPanel'

const event = (sessionTimeMs: number, eventType: string, description: string): ReplayEvent => ({ sessionTimeMs, eventType, description })

const baseSnapshot: ReplayControllerSnapshot = {
  status: 'ready', timeMs: 1_000, speed: 1, isPlaying: true, crossedEvents: [], error: null,
  replay: { sessionTimeMs: 1_000, leaderboardOrder: null, trackStatusCode: null, weatherState: null, events: [], drivers: {} },
}

afterEach(cleanup)

test('formats active state and event values without losing unavailable data', () => {
  expect(formatTrackStatus(4)).toBe('Safety Car')
  expect(formatTrackStatus(null)).toBe('Unavailable')
  expect(formatWeatherState(' clear ')).toBe('Clear')
  expect(formatWeatherState('RAIN')).toBe('Rain')
  expect(formatWeatherState(null)).toBe('Unavailable')
  expect(formatRaceControlMessage(event(1_250, 'yellow_flag', 'Turn 1 - Impeding - Noted'))).toEqual({
    headline: 'RACE CONTROL: YELLOW FLAG', detail: 'TURN 1 - IMPEDING - NOTED', isPenalty: false,
  })
})

test('formats FIA-style incident headings and identifies penalties', () => {
  expect(formatRaceControlMessage(event(1_250, 'incident', 'Verstappen, Norris incident - Turn 1 - Impeding - Noted'))).toEqual({
    headline: 'RACE CONTROL: VERSTAPPEN, NORRIS INCIDENT', detail: 'TURN 1 - IMPEDING - NOTED', isPenalty: false,
  })
  expect(formatRaceControlMessage(event(1_250, 'incident', 'Verstappen incident - Turn 1 - Causing a Collision - 10 Second Time Penalty'))).toMatchObject({
    headline: 'RACE CONTROL: VERSTAPPEN INCIDENT', isPenalty: true,
  })
})

test('renders an active transient race-control message without treating sampled events as messages', () => {
  const historical = event(500, 'flag', 'Historical event not crossed by this controller')
  const crossed = event(1_200, 'pass', 'Driver completed an overtake')
  render(<RaceControlPanel snapshot={{
    ...baseSnapshot,
    replay: { ...baseSnapshot.replay!, trackStatusCode: 4, weatherState: 'clear', events: [historical] },
  }} activeMessage={crossed} isMessageExiting={false} />)

  expect(screen.getByText('Safety Car')).toBeTruthy()
  expect(screen.getByText('Clear')).toBeTruthy()
  expect(screen.queryByText(historical.description)).toBeNull()
  expect(screen.getByText('DRIVER COMPLETED AN OVERTAKE')).toBeTruthy()
  expect(screen.getByText('RACE CONTROL: PASS')).toBeTruthy()
  expect(screen.getByRole('region', { name: 'Race control message' })).toBeTruthy()
})
