import { describe, expect, test } from 'vitest'
import type { PenaltyIssuance, PenaltySidecar } from '../../../../src/data/replay/types'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'
import { selectDriverPenaltyStatus } from '../../../../src/features/replay/selectors/penalty-selectors'

function createSnapshot(sessionTimeMs: number): ReplaySnapshot {
  return {
    sessionTimeMs,
    drivers: {},
    leaderboardOrder: null,
    trackStatusCode: null,
    weatherState: null,
    events: [],
  }
}

function createPenalty(driverId: string, sessionTimeMs: number): PenaltyIssuance {
  return {
    driverId,
    sessionTimeMs,
    penaltyType: 'time',
    reason: 'Test reason',
    rawMessage: 'Test penalty',
  }
}

function createSidecar(...penaltyIssuances: PenaltyIssuance[]): PenaltySidecar {
  return {
    contractVersion: 'v2',
    fixtureId: 'test-fixture',
    penaltyIssuances,
  }
}

describe('selectDriverPenaltyStatus', () => {
  test('returns false when no penalty sidecar exists', () => {
    expect(selectDriverPenaltyStatus(createSnapshot(10_000), undefined, 'VER')).toBe(false)
  })

  test('returns false when the sidecar has no matching driver', () => {
    const sidecar = createSidecar(createPenalty('NOR', 10_000))

    expect(selectDriverPenaltyStatus(createSnapshot(20_000), sidecar, 'VER')).toBe(false)
  })

  test('returns false when seeking before issuance', () => {
    const sidecar = createSidecar(createPenalty('VER', 10_000))

    expect(selectDriverPenaltyStatus(createSnapshot(9_999), sidecar, 'VER')).toBe(false)
  })

  test('returns true at and after issuance', () => {
    const sidecar = createSidecar(createPenalty('VER', 10_000))

    expect(selectDriverPenaltyStatus(createSnapshot(10_000), sidecar, 'VER')).toBe(true)
    expect(selectDriverPenaltyStatus(createSnapshot(20_000), sidecar, 'VER')).toBe(true)
  })

  test('supports direct large seeks without incremental state', () => {
    const sidecar = createSidecar(createPenalty('VER', 10_000))

    expect(selectDriverPenaltyStatus(createSnapshot(5_000), sidecar, 'VER')).toBe(false)
    expect(selectDriverPenaltyStatus(createSnapshot(1_000_000), sidecar, 'VER')).toBe(true)
    expect(selectDriverPenaltyStatus(createSnapshot(5_000), sidecar, 'VER')).toBe(false)
  })

  test('supports multiple penalties for the same driver', () => {
    const sidecar = createSidecar(
      createPenalty('VER', 10_000),
      createPenalty('VER', 30_000),
    )

    expect(selectDriverPenaltyStatus(createSnapshot(20_000), sidecar, 'VER')).toBe(true)
    expect(selectDriverPenaltyStatus(createSnapshot(40_000), sidecar, 'VER')).toBe(true)
  })
})
