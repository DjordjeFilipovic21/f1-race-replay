import { describe, expect, test } from 'vitest'
import { parsePitLossEstimateSidecar } from '../../../../src/data/replay/guards'
import type {
  PitLossEstimateSidecar,
  PitLossEstimateTimeline,
  PitLossModel,
} from '../../../../src/data/replay/types'
import { selectPitLossEstimate } from '../../../../src/features/replay/selectors/pit-loss-selectors'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'

// ---------------------------------------------------------------------------
// Compile-time contract: the catalog-only internal sourceStatus must never be
// exposed by browser-facing types or selection results. Each conditional type
// resolves to `never` (failing `npm run typecheck`) if the checked type ever
// gains a sourceStatus field, and browserSafe() enforces the same boundary on
// selector inputs at the type level.
// ---------------------------------------------------------------------------

type WithoutSourceStatus<T> = T extends { readonly sourceStatus: unknown } ? never : T

function browserSafe<T>(value: WithoutSourceStatus<T>): T {
  return value
}

function createSnapshot(sessionTimeMs: number, trackStatusCode: number | null): ReplaySnapshot {
  return {
    sessionTimeMs,
    leaderboardOrder: ['VER'],
    trackStatusCode,
    weatherState: null,
    events: [],
    drivers: {
      VER: {
        x: null,
        y: null,
        trackDistanceMeters: null,
        speed: null,
        throttle: null,
        brake: null,
        gapToLeaderMs: 0,
        lap: 1,
        position: 1,
        gear: null,
        drs: null,
        tyreCompound: null,
        status: 'Running',
        isInPitLane: false,
      },
    },
  }
}

function createModel(): PitLossModel {
  return {
    contractVersion: 'v1',
    fixtureId: 'fixture-1',
    method: 'global-prior-weighted-mean-v1',
    baselineMs: 22_000,
    priorWeight: 2,
    timeMs: [0, 100, 200],
    estimatedLossMs: [22_000, 21_000, 20_000],
    observedSampleCount: [0, 1, 2],
  }
}

function createSidecar(overrides: {
  race?: PitLossEstimateSidecar['race']
  safetyCar?: PitLossEstimateSidecar['safetyCar']
  virtualSafetyCar?: PitLossEstimateSidecar['virtualSafetyCar']
} = {}): PitLossEstimateSidecar {
  return {
    contractVersion: 'v1',
    fixtureId: 'fixture-1',
    trackId: 'track-1',
    method: 'track-status-median-v1',
    race: overrides.race ?? { timeMs: [0], estimatedLossMs: [21_000], observedSampleCount: [4] },
    ...(overrides.safetyCar === undefined ? {} : { safetyCar: overrides.safetyCar }),
    ...(overrides.virtualSafetyCar === undefined ? {} : { virtualSafetyCar: overrides.virtualSafetyCar }),
  }
}

/**
 * Australia curated baseline entry: Green 19300 ms, VSC 12300 ms, SC 9300 ms.
 * Curated status timelines are single replay-start points without
 * current-race observedSampleCount. Audit metadata remains in the internal
 * catalog and is not part of the browser sidecar.
 */
function createCuratedSidecar(overrides: {
  race?: PitLossEstimateTimeline
  safetyCar?: PitLossEstimateTimeline
  virtualSafetyCar?: PitLossEstimateTimeline
} = {}): PitLossEstimateSidecar {
  return {
    contractVersion: 'v1',
    fixtureId: 'fixture-1',
    trackId: 'track-1',
    method: 'curated-track-baseline-v1',
    race: overrides.race ?? { timeMs: [0], estimatedLossMs: [19_300] },
    safetyCar: overrides.safetyCar ?? { timeMs: [0], estimatedLossMs: [9_300] },
    virtualSafetyCar: overrides.virtualSafetyCar ?? { timeMs: [0], estimatedLossMs: [12_300] },
  }
}

describe('selectPitLossEstimate', () => {
  test('selects the race timeline during normal running', () => {
    // Arrange
    const sidecar = createSidecar({ race: { timeMs: [0, 50], estimatedLossMs: [22_000, 21_500], observedSampleCount: [0, 1] } })

    // Act
    const result = selectPitLossEstimate(createModel(), createSnapshot(50, 1), sidecar)

    // Assert
    expect(result).toMatchObject({ estimatedLossMs: 21_500, source: 'race', sourceLabel: 'Race estimate' })
  })

  test('selects the Safety Car timeline when Safety Car is active', () => {
    // Arrange
    const sidecar = createSidecar({ safetyCar: { timeMs: [0, 50], estimatedLossMs: [22_000, 18_000], observedSampleCount: [0, 2] } })

    // Act
    const result = selectPitLossEstimate(createModel(), createSnapshot(50, 4), sidecar)

    // Assert
    expect(result).toMatchObject({ estimatedLossMs: 18_000, observedSampleCount: 2, source: 'safety-car', sourceLabel: 'Safety Car estimate' })
  })

  test.each([6, 7])('selects the Virtual Safety Car timeline for status code %s', (statusCode) => {
    // Arrange
    const sidecar = createSidecar({ virtualSafetyCar: { timeMs: [0, 50], estimatedLossMs: [22_000, 19_000], observedSampleCount: [0, 3] } })

    // Act
    const result = selectPitLossEstimate(createModel(), createSnapshot(50, statusCode), sidecar)

    // Assert
    expect(result).toMatchObject({ estimatedLossMs: 19_000, observedSampleCount: 3, source: 'virtual-safety-car', sourceLabel: 'Virtual Safety Car estimate' })
  })

  test.each([
    ['absent', undefined],
    ['unavailable', { status: 'unavailable' as const }],
    ['no causal sample', { timeMs: [100], estimatedLossMs: [18_000], observedSampleCount: [0] }],
  ])('falls back to the race estimate when the Safety Car estimate is %s', (_reason, safetyCar) => {
    // Arrange
    const sidecar = createSidecar({ safetyCar })

    // Act
    const result = selectPitLossEstimate(createModel(), createSnapshot(50, 4), sidecar)

    // Assert
    expect(result).toMatchObject({ estimatedLossMs: 21_000, source: 'race-fallback', sourceLabel: 'Race estimate (fallback)' })
  })

  test('does not select a zero-sample race timeline', () => {
    // Arrange
    const sidecar = createSidecar({
      race: { timeMs: [0], estimatedLossMs: [22_000], observedSampleCount: [0] },
    })

    // Act
    const result = selectPitLossEstimate(null, createSnapshot(0, 1), sidecar)

    // Assert
    expect(result).toBeNull()
  })

  test.each([null, 2, 5, 99])('uses the race fallback for absent, yellow, red, or unknown status %s', (statusCode) => {
    // Arrange
    const sidecar = createSidecar()

    // Act
    const result = selectPitLossEstimate(createModel(), createSnapshot(50, statusCode), sidecar)

    // Assert
    expect(result).toMatchObject({ estimatedLossMs: 21_000, source: 'race-fallback', sourceLabel: 'Race estimate (fallback)' })
  })

  test('selects the latest sample at or before the causal boundary', () => {
    // Arrange
    const sidecar = createSidecar({
      race: { timeMs: [0, 100, 200], estimatedLossMs: [22_000, 21_000, 20_000], observedSampleCount: [0, 1, 2] },
    })

    // Act
    const resultAtBoundary = selectPitLossEstimate(createModel(), createSnapshot(100, 1), sidecar)
    const resultBeforeNextSample = selectPitLossEstimate(createModel(), createSnapshot(199, 1), sidecar)

    // Assert
    expect(resultAtBoundary).toMatchObject({ timeMs: 100, estimatedLossMs: 21_000, observedSampleCount: 1 })
    expect(resultBeforeNextSample).toMatchObject({ timeMs: 100, estimatedLossMs: 21_000, observedSampleCount: 1 })
  })

  test('keeps legacy model-only deliveries compatible with both argument orders', () => {
    // Arrange
    const model = createModel()
    const boundary = createSnapshot(150, null)

    // Act
    const currentOrder = selectPitLossEstimate(model, boundary)
    const legacyOrder = selectPitLossEstimate(model, undefined, boundary)

    // Assert
    expect(currentOrder).toMatchObject({ timeMs: 100, estimatedLossMs: 21_000, source: 'race' })
    expect(legacyOrder).toEqual(currentOrder)
  })
})

describe('selectPitLossEstimate curated baseline', () => {
  test('selects the curated Green value at replay start without current-race samples', () => {
    // Arrange — curated race timeline has no observedSampleCount
    const sidecar = createCuratedSidecar()

    // Act
    const result = selectPitLossEstimate(null, createSnapshot(0, 1), sidecar)

    // Assert — the selected curated value is numeric and never a Baseline.
    expect(result).toMatchObject({
      timeMs: 0,
      estimatedLossMs: 19_300,
      observedSampleCount: 0,
      isBaseline: false,
      source: 'race',
      sourceLabel: 'Race estimate',
    })
  })

  test('keeps the curated Green value available beyond replay start', () => {
    // Arrange — single replay-start point must apply for the whole replay
    const sidecar = createCuratedSidecar()

    // Act
    const result = selectPitLossEstimate(null, createSnapshot(600_000, 1), sidecar)

    // Assert
    expect(result).toMatchObject({ timeMs: 0, estimatedLossMs: 19_300, isBaseline: false })
  })

  test('selects the curated Safety Car value when Safety Car is active', () => {
    // Arrange
    const sidecar = createCuratedSidecar()

    // Act
    const result = selectPitLossEstimate(null, createSnapshot(0, 4), sidecar)

    // Assert
    expect(result).toMatchObject({
      estimatedLossMs: 9_300,
      isBaseline: false,
      source: 'safety-car',
      sourceLabel: 'Safety Car estimate',
    })
  })

  test.each([6, 7])('selects the curated Virtual Safety Car value for status code %s', (statusCode) => {
    // Arrange
    const sidecar = createCuratedSidecar()

    // Act
    const result = selectPitLossEstimate(null, createSnapshot(0, statusCode), sidecar)

    // Assert — codes 6 and 7 (VSC ending) both resolve to the VSC baseline
    expect(result).toMatchObject({
      estimatedLossMs: 12_300,
      isBaseline: false,
      source: 'virtual-safety-car',
      sourceLabel: 'Virtual Safety Car estimate',
    })
  })

  test('switches the curated value as the track status changes', () => {
    // Arrange
    const sidecar = createCuratedSidecar()

    // Act — Green → Safety Car → VSC ending → VSC → Green
    const green = selectPitLossEstimate(null, createSnapshot(0, 1), sidecar)
    const safetyCar = selectPitLossEstimate(null, createSnapshot(0, 4), sidecar)
    const vscEnding = selectPitLossEstimate(null, createSnapshot(0, 7), sidecar)
    const vsc = selectPitLossEstimate(null, createSnapshot(0, 6), sidecar)
    const greenAgain = selectPitLossEstimate(null, createSnapshot(0, 1), sidecar)

    // Assert
    expect(green).toMatchObject({ estimatedLossMs: 19_300, source: 'race' })
    expect(safetyCar).toMatchObject({ estimatedLossMs: 9_300, source: 'safety-car' })
    expect(vscEnding).toMatchObject({ estimatedLossMs: 12_300, source: 'virtual-safety-car' })
    expect(vsc).toMatchObject({ estimatedLossMs: 12_300, source: 'virtual-safety-car' })
    expect(greenAgain).toMatchObject({ estimatedLossMs: 19_300, source: 'race' })
  })

  test('never reports a Baseline for curated values', () => {
    // Arrange
    const sidecar = createCuratedSidecar()

    // Act
    const selections = [1, 4, 6, 7].map((statusCode) => selectPitLossEstimate(null, createSnapshot(0, statusCode), sidecar))

    // Assert
    for (const selection of selections) {
      expect(selection).not.toBeNull()
      expect(selection?.isBaseline).toBe(false)
    }
  })

  test.each([null, 2, 5, 99])('fails closed for unknown status %s with a curated sidecar and no legacy model', (statusCode) => {
    // Arrange
    const sidecar = createCuratedSidecar()

    // Act
    const result = selectPitLossEstimate(null, createSnapshot(50, statusCode), sidecar)

    // Assert — unknown statuses must not silently reuse the Green value
    expect(result).toBeNull()
  })

  test('does not leak the legacy 22-second baseline for unknown statuses with a curated sidecar', () => {
    // Arrange — the legacy model has a causal sample at the boundary, which the
    // old fallback path would have surfaced for a curated delivery
    const model: PitLossModel = {
      contractVersion: 'v1',
      fixtureId: 'fixture-1',
      method: 'global-prior-weighted-mean-v1',
      baselineMs: 22_000,
      priorWeight: 2,
      timeMs: [0, 50],
      estimatedLossMs: [22_000, 21_000],
      observedSampleCount: [0, 1],
    }
    const sidecar = createCuratedSidecar()

    // Act
    const result = selectPitLossEstimate(model, createSnapshot(50, 99), sidecar)

    // Assert — fail closed rather than emitting baselineMs 22000 or the sample
    expect(result).toBeNull()
  })

  test('fails closed when the requested status timeline is unavailable', () => {
    // Arrange — an unavailable curated status is a legacy-only shape; the
    // guard rejects it instead of letting the selector fall back anywhere
    const sidecar = createCuratedSidecar() as unknown as Record<string, unknown>
    sidecar.safetyCar = { status: 'unavailable' }

    // Act & Assert
    expect(() => parsePitLossEstimateSidecar(sidecar)).toThrow('not valid for curated sidecars')
  })

  test('returns null when both the curated sidecar and legacy model are absent', () => {
    // Act
    const result = selectPitLossEstimate(null, createSnapshot(50, 1), null)

    // Assert
    expect(result).toBeNull()
  })
})

describe('selectPitLossEstimate curated cursor semantics', () => {
  test('keeps curated values across a later replay cursor and switches by track status', () => {
    // Arrange — curated values are static generation-time catalog data
    const sidecar = createCuratedSidecar()

    // Act — cursor far beyond replay start
    const green = selectPitLossEstimate(null, createSnapshot(600_000, 1), sidecar)
    const safetyCar = selectPitLossEstimate(null, createSnapshot(600_000, 4), sidecar)
    const vscEnding = selectPitLossEstimate(null, createSnapshot(600_000, 7), sidecar)

    // Assert — values stay available and switch by the current track status
    expect(green).toMatchObject({ estimatedLossMs: 19_300, source: 'race', isBaseline: false })
    expect(safetyCar).toMatchObject({ estimatedLossMs: 9_300, source: 'safety-car', isBaseline: false })
    expect(vscEnding).toMatchObject({ estimatedLossMs: 12_300, source: 'virtual-safety-car', isBaseline: false })
  })

  test('rejects a curated sidecar with an unavailable sibling status at the guard', () => {
    // Arrange — curated sidecars require available replay-start timelines for
    // all three statuses; an unavailable sibling is a legacy-only shape
    const sidecar = createCuratedSidecar() as unknown as Record<string, unknown>
    sidecar.virtualSafetyCar = { status: 'unavailable' }

    // Act & Assert — the guard rejects the invalid curated payload instead of
    // silently accepting an unavailable status
    expect(() => parsePitLossEstimateSidecar(sidecar)).toThrow('not valid for curated sidecars')
  })

  test('does not expose catalog audit metadata in the curated selection', () => {
    const sidecar = createCuratedSidecar()
    const result = selectPitLossEstimate(null, createSnapshot(0, 1), sidecar)

    expect(result).toMatchObject({ estimatedLossMs: 19_300, observedSampleCount: 0 })
    expect(result).not.toHaveProperty('calibrationCount')
    expect(result).not.toHaveProperty('confidence')
    expect(JSON.stringify(result)).not.toMatch(/provenance|evidence|catalog/i)
  })

  test('fails closed for a malformed empty curated timeline', () => {
    // Arrange — empty timeline (the guard would reject it; the selector must
    // still fail closed instead of fabricating a value)
    const sidecar = createCuratedSidecar() as unknown as Record<string, unknown>
    sidecar.race = { timeMs: [], estimatedLossMs: [] }

    // Act
    const result = selectPitLossEstimate(null, createSnapshot(0, 1), sidecar as unknown as PitLossEstimateSidecar)

    // Assert
    expect(result).toBeNull()
  })
})

describe('catalog source status stays catalog-only', () => {
  test('guard rejects a curated sidecar with top-level sourceStatus', () => {
    // Arrange — internal catalog metadata must not reach the browser payload
    const sidecar = createCuratedSidecar() as unknown as Record<string, unknown>
    sidecar.sourceStatus = 'official'

    // Act & Assert
    expect(() => parsePitLossEstimateSidecar(sidecar)).toThrow('sourceStatus is not allowed')
  })

  test('guard rejects a curated sidecar with sourceStatus inside a status timeline', () => {
    // Arrange
    const sidecar = createCuratedSidecar() as unknown as Record<string, unknown>
    const race = sidecar.race as Record<string, unknown>
    race.sourceStatus = 'measured'

    // Act & Assert
    expect(() => parsePitLossEstimateSidecar(sidecar)).toThrow('race.sourceStatus is not allowed')
  })

  test('guard rejects a legacy sidecar with sourceStatus inside the race timeline', () => {
    // Arrange — legacy deliveries must not carry the catalog-only field either
    const sidecar = createSidecar() as unknown as Record<string, unknown>
    const race = sidecar.race as Record<string, unknown>
    race.sourceStatus = 'derived'

    // Act & Assert
    expect(() => parsePitLossEstimateSidecar(sidecar)).toThrow('race.sourceStatus is not allowed')
  })

  test('selector output never propagates sourceStatus from a smuggled curated payload', () => {
    // Arrange — even a payload that bypassed the guard (cast through unknown)
    // must not leak the catalog-only field into the browser selection
    const sidecar = createCuratedSidecar() as unknown as Record<string, unknown>
    sidecar.sourceStatus = 'official'
    const race = sidecar.race as Record<string, unknown>
    race.sourceStatus = 'measured'

    // Act
    const result = selectPitLossEstimate(null, createSnapshot(0, 1), sidecar as unknown as PitLossEstimateSidecar)

    // Assert — the selection is a sanitized projection of known fields only
    expect(result).not.toBeNull()
    expect(result?.estimatedLossMs).toBe(19_300)
    expect(result).not.toHaveProperty('sourceStatus')
    expect(JSON.stringify(result)).not.toContain('sourceStatus')
  })

  test('type-level contract admits a curated sidecar without sourceStatus', () => {
    // Arrange — browserSafe() compiles only while CuratedPitLossEstimateSidecar
    // has no sourceStatus field; adding one fails `npm run typecheck`
    const sidecar = createCuratedSidecar()

    // Act — the type-level boundary check accepts the catalog-shaped payload
    const sanitized = browserSafe(sidecar)

    // Assert — runtime behavior is unchanged
    expect(sanitized.method).toBe('curated-track-baseline-v1')
  })
})

describe('legacy model-only and sidecar behavior', () => {
  test('keeps legacy baseline semantics for a model-only delivery with zero samples at the boundary', () => {
    // Arrange — no sidecar; the legacy model has only a zero-sample entry at t=0
    const model = createModel()

    // Act
    const result = selectPitLossEstimate(model, createSnapshot(0, 1))

    // Assert — legacy contract preserved: the baseline is exposed as a Baseline
    // selection; curated deliveries never follow this path
    expect(result).toMatchObject({
      timeMs: 0,
      estimatedLossMs: 22_000,
      observedSampleCount: 0,
      isBaseline: true,
      source: 'race',
    })
  })

  test('legacy sidecar exposes current-race observedSampleCount without calibration metadata', () => {
    // Arrange — legacy race timeline counts are current-race observations
    const sidecar = createSidecar({ race: { timeMs: [0, 100], estimatedLossMs: [22_000, 21_500], observedSampleCount: [0, 4] } })

    // Act
    const result = selectPitLossEstimate(createModel(), createSnapshot(100, 1), sidecar)

    // Assert — no calibrationCount/confidence: these are race observations
    expect(result).toMatchObject({ observedSampleCount: 4, source: 'race', isBaseline: false })
    expect(result).not.toHaveProperty('calibrationCount')
    expect(result).not.toHaveProperty('confidence')
  })

  test('legacy sidecar selections are never reported as Baseline', () => {
    // Arrange — Safety Car active but the legacy sidecar has no SC timeline,
    // so the race fallback supplies the current-race sample
    const sidecar = createSidecar({ race: { timeMs: [0, 100], estimatedLossMs: [22_000, 21_500], observedSampleCount: [0, 4] } })

    // Act
    const result = selectPitLossEstimate(createModel(), createSnapshot(100, 4), sidecar)

    // Assert
    expect(result).toMatchObject({ observedSampleCount: 4, source: 'race-fallback', isBaseline: false })
  })
})
