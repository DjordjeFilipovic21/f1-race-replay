import { describe, expect, test } from 'vitest'
import { selectPitRejoinProjection } from '../../../../src/features/replay/selectors/pit-rejoin-selectors'
import type { PitLossSelection } from '../../../../src/features/replay/selectors/pit-loss-selectors'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'

function createSnapshot(overrides: {
  drivers?: Record<string, Partial<ReplaySnapshot['drivers'][string]>>
  sessionTimeMs?: number
} = {}): ReplaySnapshot {
  const defaultDrivers: Record<string, ReplaySnapshot['drivers'][string]> = {
    VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 0, lap: 10, position: 1, gear: null, drs: null, tyreCompound: 'SOFT', status: 'Running', isInPitLane: false, rpm: null, isFinished: false },
    NOR: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 20_000, lap: 10, position: 2, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'Running', isInPitLane: false, rpm: null, isFinished: false },
    HAM: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 25_000, lap: 10, position: 3, gear: null, drs: null, tyreCompound: 'HARD', status: 'Running', isInPitLane: false, rpm: null, isFinished: false },
    LEC: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 30_000, lap: 10, position: 4, gear: null, drs: null, tyreCompound: 'SOFT', status: 'Running', isInPitLane: false, rpm: null, isFinished: false },
  }
  const mergedDrivers: Record<string, ReplaySnapshot['drivers'][string]> = {}
  // Merge default drivers
  for (const [id, base] of Object.entries(defaultDrivers)) {
    const over = overrides.drivers?.[id]
    mergedDrivers[id] = over === undefined ? base : { ...base, ...over }
  }
  // Add any additional drivers from overrides that aren't in defaultDrivers
  if (overrides.drivers) {
    for (const [id, over] of Object.entries(overrides.drivers)) {
      if (!(id in defaultDrivers)) {
        mergedDrivers[id] = { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: null, lap: 10, position: 5, gear: null, drs: null, tyreCompound: 'SOFT', status: 'Running', isInPitLane: false, rpm: null, isFinished: false, ...over }
      }
    }
  }
  return {
    sessionTimeMs: overrides.sessionTimeMs ?? 120_000,
    leaderboardOrder: ['VER', 'NOR', 'HAM', 'LEC'],
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
        LEC: { isInPitLane: true },
      },
    })
    expect(selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))).toBeNull()
  })

  test('exposes comparator on both sides when projected gap falls between drivers', () => {
    // VER: gap=0, pit loss=22s → projected=22000
    // NOR: gap=20000 (ahead, signedGap = 22000-20000 = 2000)
    // HAM: gap=25000 (behind, signedGap = 22000-25000 = -3000)
    const snapshot = createSnapshot({
      drivers: {
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 25_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(result!.aheadComparator).not.toBeNull()
    expect(result!.aheadComparator!.driverId).toBe('NOR')
    expect(result!.aheadComparator!.signedGapMs).toBe(2_000)
    expect(result!.behindComparator).not.toBeNull()
    expect(result!.behindComparator!.driverId).toBe('HAM')
    expect(result!.behindComparator!.signedGapMs).toBe(-3_000)
  })

  test('exposes comparator on one side only when projected gap is ahead of all', () => {
    // VER: gap=0, pit loss=3s → projected=3000
    // NOR: gap=20000 (ahead, signedGap = 3000-20000 = -17000 → selected is ahead of NOR)
    // HAM: gap=25000 (ahead, signedGap = 3000-25000 = -22000 → selected is ahead of HAM)
    // Closest behind = NOR (largest negative signedGap = -17000)
    const snapshot = createSnapshot({
      drivers: {
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 25_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(3_000))
    expect(result).not.toBeNull()
    expect(result!.aheadComparator).toBeNull()
    expect(result!.behindComparator).not.toBeNull()
    expect(result!.behindComparator!.driverId).toBe('NOR')
    expect(result!.behindComparator!.signedGapMs).toBe(-17_000)
  })

  test('exposes comparator on one side only when projected gap is behind all', () => {
    // VER: gap=0, pit loss=40s → projected=40000
    // NOR: gap=20000 (behind, signedGap = 40000-20000 = 20000 → selected is behind NOR)
    // HAM: gap=25000 (behind, signedGap = 40000-25000 = 15000 → selected is behind HAM)
    // LEC: excluded to make HAM the closest ahead
    // Closest ahead = HAM (smallest positive signedGap = 15000)
    const snapshot = createSnapshot({
      drivers: {
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 25_000 },
        LEC: { status: 'Out' },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(40_000))
    expect(result).not.toBeNull()
    expect(result!.aheadComparator).not.toBeNull()
    expect(result!.aheadComparator!.driverId).toBe('HAM')
    expect(result!.aheadComparator!.signedGapMs).toBe(15_000)
    expect(result!.behindComparator).toBeNull()
  })

  test('uses deterministic tie-breaking by driver ID', () => {
    // VER: gap=0, pit loss=22s → projected=22000
    // NOR: gap=20000 (signedGap = 2000)
    // HAM: gap=20000 (signedGap = 2000) — tie for ahead
    // LEC: gap=24000 (signedGap = -2000)
    // ALO: gap=24000 (signedGap = -2000) — tie for behind
    // Ahead tie: HAM < NOR lexicographically → HAM wins
    // Behind tie: ALO < LEC lexicographically → ALO wins
    const snapshot = createSnapshot({
      drivers: {
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 20_000 },
        LEC: { gapToLeaderMs: 24_000 },
        ALO: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 24_000, lap: 10, position: 5, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'Running', isInPitLane: false, rpm: null, isFinished: false },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(result!.aheadComparator!.driverId).toBe('HAM')
    expect(result!.behindComparator!.driverId).toBe('ALO')
  })

  test('excludes drivers with null or non-finite gaps', () => {
    const snapshot = createSnapshot({
      drivers: {
        NOR: { gapToLeaderMs: null },
        HAM: { gapToLeaderMs: 25_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(result!.aheadComparator).toBeNull()
    expect(result!.behindComparator).not.toBeNull()
    expect(result!.behindComparator!.driverId).toBe('HAM')
  })

  test('excludes finished, in-pit-lane, and terminal-status drivers', () => {
    const snapshot = createSnapshot({
      drivers: {
        NOR: { isFinished: true },
        HAM: { isInPitLane: true },
        LEC: { status: 'Out' },
        ALO: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 25_000, lap: 10, position: 5, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'Running', isInPitLane: false, rpm: null, isFinished: false },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(result!.aheadComparator).toBeNull()
    expect(result!.behindComparator!.driverId).toBe('ALO')
  })

  test('preserves projected position computation', () => {
    // VER: gap=0, pit loss=22s → projected=22000
    // NOR: gap=20000 → behind NOR
    // HAM: gap=25000 → ahead of HAM
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

  test('exposes current position from the selected driver snapshot', () => {
    const snapshot = createSnapshot({
      drivers: {
        VER: { position: 1 },
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 25_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(result!.currentPosition).toBe(1)
  })

  test('current position is null when the driver position is null', () => {
    const snapshot = createSnapshot({
      drivers: {
        VER: { position: null },
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 25_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(result!.currentPosition).toBeNull()
  })

  test('current position is null when the driver position is not a positive integer', () => {
    const snapshot = createSnapshot({
      drivers: {
        VER: { position: 0 },
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 25_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(result!.currentPosition).toBeNull()
  })

  test('positions lost is positive when projected position is worse than current', () => {
    // VER: current position 1, gap=0, pit loss=22s → projected=22000
    // NOR: gap=20000 → projected position 2
    const snapshot = createSnapshot({
      drivers: {
        VER: { position: 1 },
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 25_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(result!.projectedPosition).toBe(2)
    expect(result!.currentPosition).toBe(1)
    // positionsLost = 2 - 1 = 1
  })

  test('positions lost is zero when projected position equals current', () => {
    // VER: current position 2, gap=20000, pit loss=3s → projected=23000
    // NOR: gap=0 → ahead (signedGap = 23000-0 = 23000)
    // HAM: gap=25000 → behind (signedGap = 23000-25000 = -2000)
    // Projected position: 1 (ahead of HAM, behind NOR)
    const snapshot = createSnapshot({
      drivers: {
        NOR: { gapToLeaderMs: 0, position: 1 },
        VER: { gapToLeaderMs: 20_000, position: 2 },
        HAM: { gapToLeaderMs: 25_000, position: 3 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(3_000))
    expect(result).not.toBeNull()
    expect(result!.projectedPosition).toBe(2)
    expect(result!.currentPosition).toBe(2)
  })

  test('result is frozen', () => {
    const snapshot = createSnapshot()
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(Object.isFrozen(result)).toBe(true)
    if (result!.aheadComparator) {
      expect(Object.isFrozen(result!.aheadComparator)).toBe(true)
    }
    if (result!.behindComparator) {
      expect(Object.isFrozen(result!.behindComparator)).toBe(true)
    }
  })

  test('signed gap is zero when projected gap exactly equals comparator gap', () => {
    // VER: gap=0, pit loss=20s → projected=20000
    // NOR: gap=20000 → signed = 20000-20000 = 0
    // HAM: gap=30000 → signed = 20000-30000 = -10000 (behind)
    // Equal-gap driver (NOR) is assigned to ahead side because NOR < VER lexicographically
    const snapshot = createSnapshot({
      drivers: {
        VER: { gapToLeaderMs: 0 },
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 30_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(20_000))
    expect(result).not.toBeNull()
    expect(result!.aheadComparator).not.toBeNull()
    expect(result!.aheadComparator!.driverId).toBe('NOR')
    expect(result!.aheadComparator!.signedGapMs).toBe(0)
    expect(result!.behindComparator).not.toBeNull()
    expect(result!.behindComparator!.driverId).toBe('HAM')
    expect(result!.behindComparator!.signedGapMs).toBe(-10_000)
  })

  test('equal-gap comparator assigned to behind side when comparator ID > selected ID', () => {
    // NOR: gap=0, pit loss=20s → projected=20000
    // VER: gap=20000 → signed = 20000-20000 = 0
    // HAM: gap=30000 → signed = 20000-30000 = -10000 (behind)
    // Equal-gap driver (VER) is assigned behind because VER > NOR lexicographically
    const snapshot = createSnapshot({
      drivers: {
        NOR: { gapToLeaderMs: 0 },
        VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 20_000, lap: 10, position: 2, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'Running', isInPitLane: false, rpm: null, isFinished: false },
        HAM: { gapToLeaderMs: 30_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'NOR', createPitLoss(20_000))
    expect(result).not.toBeNull()
    expect(result!.aheadComparator).toBeNull()
    expect(result!.behindComparator).not.toBeNull()
    expect(result!.behindComparator!.driverId).toBe('VER')
    expect(result!.behindComparator!.signedGapMs).toBe(0)
  })

  test('handles spec example: selected 20s behind comparator', () => {
    // VER: gap=0, pit loss=22s → projected=22000
    // NOR: gap=20000 → signed = 22000-20000 = 2000 → behind NOR
    // HAM: gap=25000 → signed = 22000-25000 = -3000 → ahead of HAM
    const snapshot = createSnapshot({
      drivers: {
        NOR: { gapToLeaderMs: 20_000 },
        HAM: { gapToLeaderMs: 25_000 },
      },
    })
    const result = selectPitRejoinProjection(snapshot, 'VER', createPitLoss(22_000))
    expect(result).not.toBeNull()
    expect(result!.aheadComparator!.signedGapMs).toBe(2_000)
    expect(result!.aheadComparator!.driverId).toBe('NOR')
    expect(result!.behindComparator!.signedGapMs).toBe(-3_000)
    expect(result!.behindComparator!.driverId).toBe('HAM')
    expect(result!.projectedPosition).toBe(2)
  })
})
