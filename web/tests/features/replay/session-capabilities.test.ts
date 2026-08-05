import { describe, expect, test } from 'vitest'
import { createSessionCapabilities, getSessionLabel } from '../../../src/features/replay/session-capabilities'

describe('session capabilities', () => {
  test('keeps every V2 mode distinct and prevents non-race modes from gaining race panels', () => {
    const modes = ['practice', 'qualifying', 'race', 'sprint', 'sprint-qualifying', 'sprint-shootout', 'testing'] as const

    expect(modes.map(getSessionLabel)).toEqual(['Practice', 'Qualifying', 'Race', 'Sprint', 'Sprint qualifying', 'Sprint shootout', 'Testing'])
    expect(createSessionCapabilities('practice', { stintSummary: {} as never, pitLossModel: {} as never }).canShowTyreStrategy).toBe(false)
    expect(createSessionCapabilities('testing').canShowRaceOrder).toBe(false)
  })

  test('gates optional panels on the truthful mode and delivered artifacts', () => {
    const race = createSessionCapabilities('race', { timelineSummary: {} as never, stintSummary: {} as never, pitLossModel: {} as never })
    const qualifying = createSessionCapabilities('qualifying', { qualifyingSummary: {} as never, qualifyingLapStatus: {} as never })

    expect(race.canShowRaceTimeline).toBe(true)
    expect(race.canShowTyreStrategy).toBe(true)
    expect(race.canShowPitLoss).toBe(true)
    expect(qualifying.canShowQualifyingClassification).toBe(true)
    expect(qualifying.canFilterQualifyingLapStatus).toBe(true)
    expect(qualifying.canShowRaceOrder).toBe(false)
  })
})
