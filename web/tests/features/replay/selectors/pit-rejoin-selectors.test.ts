import { describe, expect, test } from 'vitest'
import { selectPitRejoinProjection } from '../../../../src/features/replay/selectors/pit-rejoin-selectors'
import type { PitLossSelection } from '../../../../src/features/replay/selectors/pit-loss-selectors'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'

function createSnapshot(overrides: {
  drivers?: Record<string, {
    gapToLeaderMs?: number | null
    status?: string | null
    isInPitLane?: boolean | null
    isFinished?: boolean | null
    lap?: number | null
  }>
  sessionTimeMs?: number
} = {}): ReplaySnapshot {
  const defaultDrivers: Record<string, ReplaySnapshot['drivers'][string]> = {
    VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 0, lap: 10, position: 1, gear: null, drs: null, tyreCompound: 'SOFT', status: 'Running', isInPitLane: false, rpm: null, isFinished: false },
    NOR: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 2_000, lap: 10, position: 2, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'Running', isInPitLane: false, rpm: null, isFinished: false },
    HAM: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 5_000, lap: 10, position: 3, gear: null, drs: null, tyreCompound: 'HARD', status: 'Running', isInPitLane: false, rpm: null, isFinished: false },
  }
  const mergedDrivers: Record<string, ReplaySnapshot['drivers'][string]> = {}
  for (const [id, base] of Object.entries(defaultDrivers)) {
    const over = overrides.drivers?.[id]
    mergedDrivers[id] = over === undefined ? base : { ...base, ...over }
  }
  return {
    sessionTimeMs: overrides.sessionTimeMs ?? 120_000,
    leaderboardOrder: ['VER', 'NOR', 'HAM'],
    trackStatusCode: null,
    weatherState: null,
    events: [],
    drivers: mergedDrivers,
  }
}

function createPitLoss(estimatedLossMs: number): PitLossSelection {
  return Object.freeze({ timeMs: 0, estimatedLossMs, observedSampleCount: 3, isBaseline: false })
}

describe('selectPitRejoinProjection', () => {
  test('returns null when snapshot is null', () => {
    expect(selectPitRejoinProjection(null, 'VER', createPitLoss(22_000))).toBeNull()
  })

  test('returns null when selected driver ID is null', () => {
    expect(selectPitRejoinProjection(createSnapshot(), null, createPitLoss(22_000))).toBeNull()
  })

  test('returns null when pit-loss estimate is null', () => {
    expect(selectPitRejoinProjection(createSnapshot(), 'VER', null)).toBeNull()
  })

  test('returns null when pit-loss estimate is not finite', () => {
    expect(selectPitRejoinProjection(createSnapshot(), 'VER', Object.freeze({ timeMs: 0, estimatedLossMs: NaN, observedSampleCount: 0, isBaseline: true }))).toBeNull()
  })

  test('returns null when selected driver is missing from snapshot', () => {
    expect(selectPitRejoinProjection(createSnapshot(), 'UNKNOWN', createPitLoss(22_000))).toBeNull()
  })

  test('returns null when selected driver is finished', () => {
    const snapshot = createSnapshot({ drivers: { VER: { isFinished: true } } })
    expect(selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))).toBeNull()
  })

  test('returns null when selected driver has terminal status', () => {
    const snapshot = createSnapshot({ drivers: { VER: { status: 'Out' } } })
    expect(selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))).toBeNull()
  })

  test('returns null when selected driver is already in pit lane', () => {
    const snapshot = createSnapshot({ drivers: { VER: { isInPitLane: true } } })
    expect(selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))).toBeNull()
  })

  test('returns null when selected driver gap is null', () => {
    const snapshot = createSnapshot({ drivers: { VER: { gapToLeaderMs: null } } })
    expect(selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))).toBeNull()
  })

  test('returns null when no comparable active drivers exist', () => {
    const snapshot = createSnapshot({
      drivers: {
        NOR: { status: 'Out' },
        HAM: { isFinished: true },
      },
    })
    expect(selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))).toBeNull()
  })

  test('projects gap and computes signed gap vs nearest (positive = behind)', () => {
    // VER: gap=0, pit loss=22s → projected=+22s
    // NOR: gap=20s, HAM: gap=5s
    // signed vs NOR: 22000-20000 = +2000 → behind NOR
    // signed vs HAM: 22000-5000 = +17000 → behind HAM
    // nearest = NOR (abs diff = 2000)
    const snapshot = createSnapshot({
      drivers: {
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 5_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(result!.projectedGapToLeaderMs).toBe(22_000)
    expect(result!.nearestDriverId).toBe('NOR')
    expect(result!.signedGapVsNearestMs).toBe(2_000)
  })

  test('signed gap is negative when selected would rejoin ahead', () => {
    // VER: gap=0, pit loss=3s → projected=+3s
    // NOR: gap=20s → signed vs NOR: 3000-20000 = -17000 → ahead of NOR
    const snapshot = createSnapshot({
      drivers: {
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 30_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(3_000))
    expect(result).not.toBeNull()
    expect(result!.nearestDriverId).toBe('NOR')
    expect(result!.signedGapVsNearestMs).toBe(-17_000)
  })

  test('computes projected position correctly', () => {
    // VER: gap=0, pit loss=22s → projected=22s
    // NOR: gap=20s → behind NOR
    // HAM: gap=25s → ahead of HAM
    // Sorted: NOR(20s), VER(22s), HAM(25s) → position 2
    const snapshot = createSnapshot({
      drivers: {
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 25_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(result!.projectedPosition).toBe(2)
  })

  test('excludes non-selected drivers with null or non-finite gaps', () => {
    const snapshot = createSnapshot({
      drivers: {
        NOR: { gapToLeaderMs: null },
        HAM: { gapToLeaderMs: 5_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(3_000))
    expect(result).not.toBeNull()
    expect(result!.nearestDriverId).toBe('HAM')
  })

  test('result is frozen', () => {
    const snapshot = createSnapshot()
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(Object.isFrozen(result)).toBe(true)
  })

  test('handles leader gap projection exactly per spec example', () => {
    // Selected driver 20s ahead of comparator → comparator.gap = selected.gap + 20000
    // selected.gap = 0, comparator.gap = 20000
    // pit loss = 22000
    // projected selected = 0 + 22000 = 22000
    // signed = 22000 - 20000 = +2000 → behind comparator
    const snapshot = createSnapshot({
      drivers: {
        VER: { gapToLeaderMs: 0 },
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 25_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(result!.signedGapVsNearestMs).toBe(2_000)
    expect(result!.nearestDriverId).toBe('NOR')
    expect(result!.projectedPosition).toBe(2)
  })

  test('signed gap is zero when projected gap exactly equals nearest comparator gap', () => {
    // VER: gap=0, pit loss=20s → projected=20000
    // NOR: gap=20000 → signed = 20000 - 20000 = 0
    const snapshot = createSnapshot({
      drivers: {
        VER: { gapToLeaderMs: 0 },
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 50_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(20_000))
    expect(result).not.toBeNull()
    expect(result!.nearestDriverId).toBe('NOR')
    expect(result!.signedGapVsNearestMs).toBe(0)
    expect(result!.projectedPosition).toBe(2)
  })
})
