import { describe, expect, test } from 'vitest'
import type { LapSectorSidecar, QualifyingLapStatusSidecar, QualifyingSummary } from '../../../../src/data/replay/types'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'
import { selectQualifyingLiveState, selectQualifyingLiveStates } from '../../../../src/features/replay/selectors/qualifying-live-state-selectors'

const summary: QualifyingSummary = {
  contractVersion: 'v2', fixtureId: 'selector-fixture', drivers: {
    HAM: { qualifyingPosition: [1], q1TimeMs: [90_000], q2TimeMs: [89_000], q3TimeMs: [88_000], bestLapNumber: [3], bestLapTimeMs: [88_000] },
    VER: { qualifyingPosition: [2], q1TimeMs: [91_000], q2TimeMs: [null], q3TimeMs: [null], bestLapNumber: [2], bestLapTimeMs: [null] },
  },
}

const sidecar: LapSectorSidecar = {
  contractVersion: 'v2', fixtureId: 'selector-fixture', phaseBoundaries: [], drivers: {
    HAM: {
      lapNumber: [1, 2], lapStartMs: [0, 100], lapEndMs: [90, 190], lapDurationMs: [90, 90],
      sector1DurationMs: [30, 30], sector2DurationMs: [30, 30], sector3DurationMs: [30, 30],
      sector1SessionTimeMs: [30, 130], sector2SessionTimeMs: [60, 160], sector3SessionTimeMs: [90, 190],
      qualifyingPhase: [null, null],
    },
  },
}

const statusSidecar: QualifyingLapStatusSidecar = {
  contractVersion: 'v2', fixtureId: 'selector-fixture', drivers: {
    HAM: { lapNumber: [1, 2, 3], lapStartMs: [0, 100, 200], lapEndMs: [90, 190, 290], status: ['valid', 'valid', 'deleted'], deletedReason: [null, null, 'TRACK LIMITS'] },
  },
  events: [
    { driverId: 'HAM', lapNumber: 2, eventTimeMs: 180, status: 'deleted', reason: 'TRACK LIMITS', rawMessage: 'deleted' },
    { driverId: 'HAM', lapNumber: 2, eventTimeMs: 250, status: 'reinstated', reason: null, rawMessage: 'reinstated' },
  ],
}

const phasedSidecar: LapSectorSidecar = {
  ...sidecar,
  phaseBoundaries: [
    { phase: 'Q1', startMs: 0 },
    { phase: 'Q2', startMs: 180 },
  ],
  drivers: {
    HAM: { ...sidecar.drivers.HAM, qualifyingPhase: ['Q1', 'Q2'], lapKind: ['flying', 'flying'] },
  },
}

function snapshot(sessionTimeMs: number, lap: number | null = 2): ReplaySnapshot {
  return {
    sessionTimeMs, leaderboardOrder: ['HAM', 'VER'], trackStatusCode: null, weatherState: null, events: [], drivers: {
      HAM: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: null, lap, position: null, gear: null, drs: null, tyreCompound: 'SOFT', tyreAge: 4, status: 'OnTrack', isInPitLane: false },
      VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: null, lap: null, position: null, gear: null, drs: null, tyreCompound: null, isInPitLane: null, status: null },
    },
  }
}

describe('qualifying live-state selectors', () => {
  test('uses only causal lap and sector boundaries at the sampled cursor', () => {
    // Arrange
    const current = snapshot(155)

    // Act
    const state = selectQualifyingLiveState(current, 'HAM', summary, sidecar, statusSidecar)

    // Assert
    expect(state.causalLapEvidence.map(({ lapNumber, sectors }) => [lapNumber, sectors.length])).toEqual([[1, 3], [2, 1]])
    expect(state.currentLapEvidence?.lapEndMs).toBeNull()
    expect(state.currentLapEvidence?.lapDurationMs).toBeNull()
    expect(state.lapPhase).toBe('flying')
  })

  test('changes the active phase only at delivered phase boundaries', () => {
    // Arrange
    const beforePhaseChange = snapshot(155)
    const afterPhaseChange = snapshot(210)

    // Act
    const q1 = selectQualifyingLiveState(beforePhaseChange, 'HAM', summary, phasedSidecar, statusSidecar)
    const q2 = selectQualifyingLiveState(afterPhaseChange, 'HAM', summary, phasedSidecar, statusSidecar)

    // Assert
    expect(q1.activeQualifyingPhase).toBe('Q1')
    expect(q1.fastestCausalLapDurationMs).toBe(90)
    expect(q2.activeQualifyingPhase).toBe('Q2')
    expect(q2.fastestCausalLapDurationMs).toBeNull()
  })

  test('finishes an active Q2 driver while retaining the fastest posted Q2 time', () => {
    // Arrange
    const activeQ2Sidecar = {
      contractVersion: 'v2' as const,
      fixtureId: 'selector-fixture',
       phaseBoundaries: [{ phase: 'Q1' as const, startMs: 0 }, { phase: 'Q2' as const, startMs: 100 }, { phase: 'Q3' as const, startMs: 500 }],
      drivers: {
        COL: {
          lapNumber: [1, 2, 3, 4], lapStartMs: [0, 120, 250, 500], lapEndMs: [90, 240, 345, 700], lapDurationMs: [90, 80, 95, 85],
          sector1DurationMs: [30, 40, 30, 30], sector2DurationMs: [30, 40, 30, 25], sector3DurationMs: [30, 40, 35, 30],
          sector1SessionTimeMs: [30, 160, 280, 560], sector2SessionTimeMs: [60, 200, 315, 620], sector3SessionTimeMs: [90, 240, 345, 700], qualifyingPhase: ['Q1', 'Q2', 'Q2', 'Q3'] as const,
          lapKind: ['flying', 'flying', 'flying', 'flying'] as const,
        },
        NOR: {
          lapNumber: [1, 2, 3], lapStartMs: [0, 120, 260], lapEndMs: [90, 240, 400], lapDurationMs: [90, 120, 140],
          sector1DurationMs: [30, 40, 45], sector2DurationMs: [30, 40, 45], sector3DurationMs: [30, 40, 45],
          sector1SessionTimeMs: [30, 160, 305], sector2SessionTimeMs: [60, 200, 350], sector3SessionTimeMs: [90, 240, 400], qualifyingPhase: ['Q1', 'Q2', 'Q2'] as const,
          lapKind: ['flying', 'flying', 'flying'] as const,
        },
      },
    } satisfies Extract<LapSectorSidecar, { contractVersion: 'v2' }>
    const activeQ2Summary = {
      contractVersion: 'v2' as const,
      fixtureId: 'selector-fixture',
      drivers: {
        COL: { qualifyingPosition: [1], q1TimeMs: [90], q2TimeMs: [80], q3TimeMs: [null], bestLapNumber: [2], bestLapTimeMs: [80] },
        NOR: { qualifyingPosition: [2], q1TimeMs: [90], q2TimeMs: [120], q3TimeMs: [null], bestLapNumber: [2], bestLapTimeMs: [120] },
      },
    } satisfies QualifyingSummary

    // Act
    const states = selectQualifyingLiveStates(snapshot(360, 2), ['COL', 'NOR'], activeQ2Summary, activeQ2Sidecar)
    const afterNorLastLap = selectQualifyingLiveStates(snapshot(450, 3), ['COL', 'NOR'], activeQ2Summary, activeQ2Sidecar)
    const activeQ3 = selectQualifyingLiveStates(snapshot(750, 4), ['COL', 'NOR'], activeQ2Summary, activeQ2Sidecar)

    // Assert
    expect(states.find((state) => state.driverId === 'COL')).toMatchObject({ isFinished: true, finishedLapDurationMs: 80 })
    expect(states.find((state) => state.driverId === 'NOR')).toMatchObject({ isFinished: false, finishedLapDurationMs: null })
    expect(afterNorLastLap.find((state) => state.driverId === 'NOR')).toMatchObject({ isFinished: true, finishedLapDurationMs: 120 })
    expect(activeQ3.find((state) => state.driverId === 'COL')).toMatchObject({ activeQualifyingPhase: 'Q3', isFinished: true, finishedLapDurationMs: 85 })
  })

  test('ranks the active phase from causal lap times instead of final summary positions', () => {
    // Arrange: the final summary intentionally disagrees with the times posted
    // so far in Q1.
    const liveSidecar: LapSectorSidecar = {
      contractVersion: 'v2', fixtureId: 'selector-fixture', phaseBoundaries: [{ phase: 'Q1', startMs: 0 }], drivers: {
        HAM: { ...sidecar.drivers.HAM, lapNumber: [1], lapStartMs: [0], lapEndMs: [100], lapDurationMs: [100], qualifyingPhase: ['Q1'], lapKind: ['flying'] },
        VER: { ...sidecar.drivers.HAM, lapNumber: [1], lapStartMs: [0], lapEndMs: [100], lapDurationMs: [80], qualifyingPhase: ['Q1'], lapKind: ['flying'] },
      },
    }
    const liveStatus: QualifyingLapStatusSidecar = {
      contractVersion: 'v2', fixtureId: 'selector-fixture', drivers: {
        HAM: { lapNumber: [1], lapStartMs: [0], lapEndMs: [100], status: ['valid'], deletedReason: [null] },
        VER: { lapNumber: [1], lapStartMs: [0], lapEndMs: [100], status: ['valid'], deletedReason: [null] },
      }, events: [],
    }

    // Act
    const states = selectQualifyingLiveStates(snapshot(100), ['HAM', 'VER'], summary, liveSidecar, liveStatus)

    // Assert
    expect(states.map((state) => [state.driverId, state.qualifyingPosition])).toEqual([['HAM', 2], ['VER', 1]])
  })

  test('freezes Q1 and Q2 eliminations while later phases rank causally', () => {
    const driverIds = Array.from({ length: 20 }, (_, index) => `D${String(index).padStart(2, '0')}`)
    const summaryDrivers: Record<string, QualifyingSummary['drivers'][string]> = {}
    const sidecarDrivers: Record<string, NonNullable<Extract<LapSectorSidecar, { contractVersion: 'v2' }>['drivers'][string]>> = {}
    const statusDrivers: Record<string, QualifyingLapStatusSidecar['drivers'][string]> = {}
    for (const [index, id] of driverIds.entries()) {
      const q1Duration = 100 + index
      const q2Duration = 200 + index
      const q3Duration = 300 + index
      const lapNumber = [1, ...(index < 15 ? [2] : []), ...(index < 10 ? [3] : [])]
      const lapStartMs = [0, ...(index < 15 ? [2_000] : []), ...(index < 10 ? [4_000] : [])]
      const lapDurationMs = [q1Duration, ...(index < 15 ? [q2Duration] : []), ...(index < 10 ? [q3Duration] : [])]
      const lapEndMs = lapStartMs.map((start, lapIndex) => start + (lapDurationMs[lapIndex] ?? 0))
      sidecarDrivers[id] = {
        lapNumber, lapStartMs, lapEndMs, lapDurationMs,
        sector1DurationMs: lapNumber.map(() => null), sector2DurationMs: lapNumber.map(() => null), sector3DurationMs: lapNumber.map(() => null),
        sector1SessionTimeMs: lapNumber.map(() => null), sector2SessionTimeMs: lapNumber.map(() => null), sector3SessionTimeMs: lapNumber.map(() => null),
        qualifyingPhase: ['Q1', ...(index < 15 ? ['Q2' as const] : []), ...(index < 10 ? ['Q3' as const] : [])],
        lapKind: lapNumber.map(() => 'flying' as const),
      }
      statusDrivers[id] = { lapNumber, lapStartMs, lapEndMs, status: lapNumber.map(() => 'valid' as const), deletedReason: lapNumber.map(() => null) }
      summaryDrivers[id] = { qualifyingPosition: [index + 1], q1TimeMs: [q1Duration], q2TimeMs: [index < 15 ? q2Duration : null], q3TimeMs: [index < 10 ? q3Duration : null], bestLapNumber: [3], bestLapTimeMs: [q3Duration] }
    }
    const phasedSidecar = { contractVersion: 'v2' as const, fixtureId: 'selector-fixture', phaseBoundaries: [{ phase: 'Q1' as const, startMs: 0 }, { phase: 'Q2' as const, startMs: 2_000 }, { phase: 'Q3' as const, startMs: 4_000 }], drivers: sidecarDrivers }
    const phasedStatus = {
      contractVersion: 'v2' as const,
      fixtureId: 'selector-fixture',
      drivers: statusDrivers,
      events: [
        { driverId: 'D16', lapNumber: 1, eventTimeMs: 5_000, status: 'deleted' as const, reason: 'late deletion', rawMessage: 'late deletion' },
        { driverId: 'D10', lapNumber: 2, eventTimeMs: 5_000, status: 'deleted' as const, reason: 'late deletion', rawMessage: 'late deletion' },
        { driverId: 'D00', lapNumber: 3, eventTimeMs: 7_000, status: 'deleted' as const, reason: 'post replay deletion', rawMessage: 'post replay deletion' },
      ],
    }
    const phasedSummary = { contractVersion: 'v2' as const, fixtureId: 'selector-fixture', drivers: summaryDrivers }

    const duringQ2 = selectQualifyingLiveStates(snapshot(2_500), driverIds, phasedSummary, phasedSidecar, phasedStatus)
    const duringQ3 = selectQualifyingLiveStates(snapshot(6_000), driverIds, phasedSummary, phasedSidecar, phasedStatus, 6_000)

    expect(duringQ2.find((state) => state.driverId === 'D15')).toMatchObject({ qualifyingPosition: 16, isOut: true })
    expect(duringQ2.find((state) => state.driverId === 'D00')).toMatchObject({ qualifyingPosition: 1, isOut: false })
    expect(duringQ3.find((state) => state.driverId === 'D10')).toMatchObject({ qualifyingPosition: 11, isOut: true })
    expect(duringQ3.find((state) => state.driverId === 'D15')).toMatchObject({ qualifyingPosition: 16, isOut: true })
    expect(duringQ3.find((state) => state.driverId === 'D00')).toMatchObject({ qualifyingPosition: 1, isOut: false })
    expect(duringQ3.find((state) => state.driverId === 'D16')).toMatchObject({ terminalLapDurationMs: 116, finishedLapDurationMs: null, isFinished: false })
    expect(duringQ3.find((state) => state.driverId === 'D10')).toMatchObject({ terminalLapDurationMs: 210, finishedLapDurationMs: null, isFinished: false })

    const withoutStatus = selectQualifyingLiveStates(snapshot(6_000), driverIds, phasedSummary, phasedSidecar, undefined, 6_000)
    expect(withoutStatus.find((state) => state.driverId === 'D16')).toMatchObject({ terminalLapDurationMs: 116, finishedLapDurationMs: null, isFinished: false })

    const afterReplayEnd = selectQualifyingLiveStates(snapshot(8_000), driverIds, phasedSummary, phasedSidecar, phasedStatus, 6_000)
    expect(afterReplayEnd.find((state) => state.driverId === 'D00')).toMatchObject({ qualifyingPosition: 1, terminalLapDurationMs: null, finishedLapDurationMs: null, isFinished: false })
  })

  test('does not invent a phase or a time when phase data is missing', () => {
    // Arrange
    const current = snapshot(155)

    // Act
    const state = selectQualifyingLiveState(current, 'HAM', summary, sidecar, statusSidecar)

    // Assert
    expect(state.activeQualifyingPhase).toBeNull()
    expect(state.fastestCausalLapDurationMs).toBeNull()
  })

  test('excludes a deleted lap and restores it only after causal reinstatement', () => {
    // Arrange
    const deleted = snapshot(210)
    const reinstated = snapshot(260)

    // Act
    const deletedState = selectQualifyingLiveState(deleted, 'HAM', summary, phasedSidecar, statusSidecar)
    const reinstatedState = selectQualifyingLiveState(reinstated, 'HAM', summary, phasedSidecar, statusSidecar)

    // Assert
    expect(deletedState.currentLapEvidence?.status).toBe('deleted')
    expect(deletedState.fastestCausalLapDurationMs).toBeNull()
    expect(reinstatedState.currentLapEvidence?.status).toBe('valid')
    expect(reinstatedState.fastestCausalLapDurationMs).toBe(90)
  })

  test('returns unknown for deleted or missing laps instead of treating them as valid', () => {
    // Arrange
    const current = snapshot(210, 2)

    // Act
    const deleted = selectQualifyingLiveState(current, 'HAM', summary, sidecar, statusSidecar)
    const unknown = selectQualifyingLiveState(snapshot(210, 99), 'HAM', summary, sidecar, statusSidecar)

    // Assert
    expect(deleted.currentLapEvidence?.status).toBe('deleted')
    expect(deleted.currentLapEvidence?.sectors).toEqual([])
    expect(deleted.causalLapEvidence).toHaveLength(2)
    expect(unknown.currentLapEvidence).toBeNull()
    expect(unknown.lapPhase).toBe('unknown')
  })

  test('keeps absent optional artifacts explicitly unavailable', () => {
    // Arrange
    const current = snapshot(50, 1)

    // Act
    const state = selectQualifyingLiveState(current, 'HAM', summary, undefined, undefined)

    // Assert
    expect(state.causalLapEvidence).toEqual([])
    expect(state.currentLapEvidence).toBeNull()
    expect(state.lapPhase).toBe('unknown')
  })

  test('classifies the current lap phase from the delivery lapKind column', () => {
    // Arrange
    const kindSidecar: LapSectorSidecar = {
      contractVersion: 'v2', fixtureId: 'selector-fixture', phaseBoundaries: [{ phase: 'Q1', startMs: 0 }], drivers: {
        HAM: {
          lapNumber: [1, 2, 3], lapStartMs: [0, 100, 200], lapEndMs: [90, 190, 290], lapDurationMs: [90, 90, 90],
          sector1DurationMs: [30, 30, 30], sector2DurationMs: [30, 30, 30], sector3DurationMs: [30, 30, 30],
          sector1SessionTimeMs: [30, 130, 230], sector2SessionTimeMs: [60, 160, 260], sector3SessionTimeMs: [90, 190, 290],
          qualifyingPhase: ['Q1', 'Q1', 'Q1'],
          lapKind: ['outlap', 'flying', 'inlap'],
        },
      },
    }

    // Act
    const outlap = selectQualifyingLiveState(snapshot(100, 1), 'HAM', summary, kindSidecar)
    const flying = selectQualifyingLiveState(snapshot(200, 2), 'HAM', summary, kindSidecar)
    const inlap = selectQualifyingLiveState(snapshot(300, 3), 'HAM', summary, kindSidecar)
    const unknown = selectQualifyingLiveState(snapshot(155, 7), 'HAM', summary, kindSidecar)

    // Assert
    expect(outlap.lapPhase).toBe('outlap')
    expect(flying.lapPhase).toBe('flying')
    expect(inlap.lapPhase).toBe('inlap')
    expect(unknown.lapPhase).toBe('unknown')
  })

  test('counts only explicit flying laps toward fastest and live timing', () => {
    // Arrange: outlap, inlap, and unknown rows all carry durations that must
    // not contribute to qualifying best/fastest timing.
    const flyingOnlySidecar: LapSectorSidecar = {
      contractVersion: 'v2', fixtureId: 'selector-fixture', phaseBoundaries: [{ phase: 'Q1', startMs: 0 }], drivers: {
        HAM: {
          lapNumber: [1, 2, 3, 4], lapStartMs: [0, 100, 200, 300], lapEndMs: [90, 190, 290, 390], lapDurationMs: [90, 85, 80, 75],
          sector1DurationMs: [30, 30, 30, 30], sector2DurationMs: [30, 30, 30, 30], sector3DurationMs: [30, 30, 30, 30],
          sector1SessionTimeMs: [30, 130, 230, 330], sector2SessionTimeMs: [60, 160, 260, 360], sector3SessionTimeMs: [90, 190, 290, 390],
          qualifyingPhase: ['Q1', 'Q1', 'Q1', 'Q1'],
          lapKind: ['flying', 'outlap', 'inlap', 'unknown'],
        },
      },
    }

    // Act
    const state = selectQualifyingLiveState(snapshot(400, 4), 'HAM', summary, flyingOnlySidecar)

    // Assert
    expect(state.fastestCausalLapDurationMs).toBe(90)
    expect(state.causalLapEvidence.map(({ lapNumber, lapDurationMs, sectors }) => [lapNumber, lapDurationMs, sectors.length])).toEqual([
      [1, 90, 3], [2, null, 0], [3, null, 0], [4, null, 0],
    ])
    expect(state.lapPhase).toBe('unknown')
  })

  test('finishes at the last flying lap in the active phase and ignores later cooldown rows', () => {
    // Arrange: Q2 lap 2 is COL's last Q2 flying lap; lap 3 is a cooldown
    // (unknown) and lap 4 is a pit-in (inlap). Neither may delay finish or
    // replace the displayed fastest Q2 time.
    const cooldownSidecar: LapSectorSidecar = {
      contractVersion: 'v2', fixtureId: 'selector-fixture',
      phaseBoundaries: [{ phase: 'Q1', startMs: 0 }, { phase: 'Q2', startMs: 500 }],
      drivers: {
        COL: {
          lapNumber: [1, 2, 3, 4], lapStartMs: [0, 520, 700, 800], lapEndMs: [90, 640, 790, 890], lapDurationMs: [90, 80, 60, 70],
          sector1DurationMs: [30, 40, 30, 30], sector2DurationMs: [30, 40, 30, 25], sector3DurationMs: [30, 40, 35, 30],
          sector1SessionTimeMs: [30, 560, 730, 830], sector2SessionTimeMs: [60, 600, 760, 860], sector3SessionTimeMs: [90, 640, 790, 890],
          qualifyingPhase: ['Q1', 'Q2', 'Q2', 'Q2'],
          lapKind: ['flying', 'flying', 'unknown', 'inlap'],
        },
      },
    }

    // Act
    const beforeFlyingLapEnds = selectQualifyingLiveState(snapshot(630, 2), 'COL', summary, cooldownSidecar)
    const afterLastFlyingLapEnds = selectQualifyingLiveState(snapshot(650, 2), 'COL', summary, cooldownSidecar)
    const afterCooldownEnds = selectQualifyingLiveState(snapshot(800, 3), 'COL', summary, cooldownSidecar)
    const afterPitInEnds = selectQualifyingLiveState(snapshot(900, 4), 'COL', summary, cooldownSidecar)

    // Assert
    expect(beforeFlyingLapEnds.isFinished).toBe(false)
    expect(afterLastFlyingLapEnds.isFinished).toBe(true)
    expect(afterLastFlyingLapEnds.finishedLapDurationMs).toBe(80)
    expect(afterCooldownEnds.isFinished).toBe(true)
    expect(afterCooldownEnds.finishedLapDurationMs).toBe(80)
    expect(afterPitInEnds.isFinished).toBe(true)
    expect(afterPitInEnds.finishedLapDurationMs).toBe(80)
  })

  test('fails closed when a qualifying-like sidecar has no lapKind column', () => {
    // Arrange: an old v2 qualifying sidecar with phase structure but no
    // lapKind column must not treat any lap as flying.
    const legacyQualifyingSidecar: LapSectorSidecar = {
      contractVersion: 'v2', fixtureId: 'selector-fixture', phaseBoundaries: [{ phase: 'Q1', startMs: 0 }], drivers: {
        HAM: {
          lapNumber: [1], lapStartMs: [0], lapEndMs: [90], lapDurationMs: [90],
          sector1DurationMs: [30], sector2DurationMs: [30], sector3DurationMs: [30],
          sector1SessionTimeMs: [30], sector2SessionTimeMs: [60], sector3SessionTimeMs: [90],
          qualifyingPhase: ['Q1'],
        },
      },
    }

    // Act
    const state = selectQualifyingLiveState(snapshot(100, 1), 'HAM', summary, legacyQualifyingSidecar)

    // Assert
    expect(state.causalLapEvidence).toHaveLength(1)
    expect(state.causalLapEvidence[0]).toMatchObject({ lapKind: null, lapDurationMs: null, sectors: [] })
    expect(state.fastestCausalLapDurationMs).toBeNull()
    expect(state.lapPhase).toBe('unknown')
    expect(state.isFinished).toBe(false)
  })

  test('never finishes from a deleted flying lap in the active phase', () => {
    // Arrange: lap 2 is a Q2 flying lap that the status sidecar deletes at 180
    // and reinstates at 250.
    const deleted = snapshot(210)
    const reinstated = snapshot(260)

    // Act
    const deletedState = selectQualifyingLiveState(deleted, 'HAM', summary, phasedSidecar, statusSidecar)
    const reinstatedState = selectQualifyingLiveState(reinstated, 'HAM', summary, phasedSidecar, statusSidecar)

    // Assert
    expect(deletedState.fastestCausalLapDurationMs).toBeNull()
    expect(deletedState.isFinished).toBe(false)
    expect(deletedState.finishedLapDurationMs).toBeNull()
    expect(reinstatedState.fastestCausalLapDurationMs).toBe(90)
  })

  test('does not use final summary Q3 absence as an early OUT decision', () => {
    // Arrange
    const current = snapshot(155)

    // Act
    const eliminated = selectQualifyingLiveState(current, 'VER', summary)
    const unavailable = selectQualifyingLiveState(current, 'VER', undefined)

    // Assert
    expect(eliminated.classification).toBe('classified')
    expect(eliminated.isOut).toBe(false)
    expect(eliminated.qualifyingPosition).toBeNull()
    expect(unavailable.classification).toBe('unavailable')
    expect(unavailable.isOut).toBe(false)
  })

  test('preserves raw sampled status and tyre data without mutating the result inputs', () => {
    // Arrange
    const current = snapshot(155)

    // Act
    const state = selectQualifyingLiveState(current, 'HAM', summary, sidecar)
    const states = selectQualifyingLiveStates(current, ['HAM'], summary, sidecar)

    // Assert
    expect(state.rawSampledStatus).toBe('OnTrack')
    expect(state.sampledStatus).toBe('OnTrack')
    expect(state.tyre).toEqual({ compound: 'SOFT', age: 4 })
    expect(Object.isFrozen(state)).toBe(true)
    expect(Object.isFrozen(state.tyre)).toBe(true)
    expect(Object.isFrozen(state.causalLapEvidence)).toBe(true)
    expect(Object.isFrozen(states)).toBe(true)
    expect(current.drivers.HAM.status).toBe('OnTrack')
  })

  test('retains an overlapping Brazil Q1 lap and withholds future phase results', () => {
    // Arrange
    const brazilDriverIds = ['NOR', ...Array.from({ length: 19 }, (_, index) => `D${String(index + 1).padStart(2, '0')}`)]
    const brazilSummary: QualifyingSummary = {
      contractVersion: 'v2',
      fixtureId: 'brazil-2024-qualifying',
      drivers: Object.fromEntries(brazilDriverIds.map((driverId, index) => {
        const q1Duration = index === 0 ? 90_944 : index <= 14 ? 90_000 + index : 91_000 + index
        const isNorris = driverId === 'NOR'
        return [driverId, {
          qualifyingPosition: [isNorris ? 1 : index + 2],
          q1TimeMs: [q1Duration],
          q2TimeMs: [isNorris ? 90_000 : null],
          q3TimeMs: [isNorris ? 88_000 : null],
          bestLapNumber: [isNorris ? 3 : 1],
          bestLapTimeMs: [isNorris ? 88_000 : q1Duration],
        }]
      })),
    }
    const brazilSidecar: LapSectorSidecar = {
      contractVersion: 'v2',
      fixtureId: 'brazil-2024-qualifying',
      phaseBoundaries: [
        { phase: 'Q1', startMs: 901_903 },
        { phase: 'Q2', startMs: 2_469_918 },
        { phase: 'Q3', startMs: 4_921_708 },
      ],
      drivers: Object.fromEntries(brazilDriverIds.map((driverId, index) => {
        const isNorris = driverId === 'NOR'
        const lapNumber = isNorris ? [1, 2, 3] : [1]
        const lapStartMs = isNorris ? [2_417_357, 3_000_000, 5_000_000] : [1_000_000 + index * 100]
        const lapDurationMs = isNorris ? [90_944, 90_000, 88_000] : [index <= 14 ? 90_000 + index : 91_000 + index]
        const lapEndMs = lapStartMs.map((startMs, lapIndex) => startMs + lapDurationMs[lapIndex])
        const emptySectors = Array<null>(lapNumber.length).fill(null)
        return [driverId, {
          lapNumber,
          lapStartMs,
          lapEndMs,
          lapDurationMs,
          sector1DurationMs: emptySectors,
          sector2DurationMs: emptySectors,
          sector3DurationMs: emptySectors,
          sector1SessionTimeMs: emptySectors,
          sector2SessionTimeMs: emptySectors,
          sector3SessionTimeMs: emptySectors,
          qualifyingPhase: isNorris ? ['Q1', 'Q2', 'Q3'] as const : ['Q1'] as const,
          lapKind: isNorris ? ['flying', 'flying', 'flying'] as const : ['flying'] as const,
        }]
      })),
    }

    // Act
    const atQ2Start = selectQualifyingLiveState(
      snapshot(2_469_918), 'NOR', brazilSummary, brazilSidecar, undefined, undefined, brazilDriverIds,
    )
    const beforeQ1LapEnds = selectQualifyingLiveState(
      snapshot(2_508_300), 'NOR', brazilSummary, brazilSidecar, undefined, undefined, brazilDriverIds,
    )
    const afterQ1LapEnds = selectQualifyingLiveState(
      snapshot(2_508_301), 'NOR', brazilSummary, brazilSidecar, undefined, undefined, brazilDriverIds,
    )
    const atQ3Start = selectQualifyingLiveState(
      snapshot(4_921_708), 'NOR', brazilSummary, brazilSidecar, undefined, undefined, brazilDriverIds,
    )

    // Assert
    expect(atQ2Start).toMatchObject({ activeQualifyingPhase: 'Q2', isOut: false, fastestCausalLapDurationMs: null, finishedLapDurationMs: null })
    expect(atQ2Start.causalLapEvidence.find((lap) => lap.lapNumber === 1)).toMatchObject({ lapEndMs: null, lapDurationMs: null })
    expect(beforeQ1LapEnds).toMatchObject({ activeQualifyingPhase: 'Q2', isOut: false, classification: 'classified' })
    expect(beforeQ1LapEnds.causalLapEvidence.find((lap) => lap.lapNumber === 1)).toMatchObject({ lapEndMs: null, lapDurationMs: null })
    expect(afterQ1LapEnds.causalLapEvidence.find((lap) => lap.lapNumber === 1)).toMatchObject({
      lapStartMs: 2_417_357, lapEndMs: 2_508_301, lapDurationMs: 90_944,
    })
    expect(afterQ1LapEnds).toMatchObject({ activeQualifyingPhase: 'Q2', isOut: false, classification: 'classified' })
    expect(atQ3Start).toMatchObject({ activeQualifyingPhase: 'Q3', isOut: false, classification: 'classified', fastestCausalLapDurationMs: null, finishedLapDurationMs: null })
    expect(atQ3Start.causalLapEvidence.some((lap) => lap.lapNumber === 3)).toBe(false)
  })
})
