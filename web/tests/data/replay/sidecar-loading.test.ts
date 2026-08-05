import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'
import { sha256Hex } from '../../../src/data/replay/digest'
import {
  parseLapSectorSidecar,
  parseManifest,
  parsePitLossEstimateSidecar,
  parsePitLossModel,
  parseStintSummary,
} from '../../../src/data/replay/guards'
import { loadReplayIndex } from '../../../src/data/replay/loader'
import type { PitLossEstimateSidecar, ReplaySource } from '../../../src/data/replay/types'

const fixtureRoot = resolve(import.meta.dirname, '../../../../contracts/replay-data/v1/fixtures/deterministic-race')
const fixtureSource: ReplaySource = { read: (path) => readFile(resolve(fixtureRoot, path)) }
const decoder = new TextDecoder()
const encoder = new TextEncoder()

// ---------------------------------------------------------------------------
// Payload builders — construct valid sidecar shapes that guards accept
// ---------------------------------------------------------------------------

function lapSectorSidecarPayload(overrides?: Record<string, unknown>): Record<string, unknown> {
  return {
    contractVersion: 'v1',
    fixtureId: 'deterministic-race',
    drivers: {
      HAM: {
        lapNumber: [1, 2, 3],
        lapStartMs: [0, 1_000, 2_000],
        lapEndMs: [800, 1_900, 2_900],
        lapDurationMs: [800, 900, 900],
        sector1DurationMs: [200, null, 250],
        sector2DurationMs: [300, 350, 300],
        sector3DurationMs: [300, 300, 350],
        sector1SessionTimeMs: [0, 1_000, 2_000],
        sector2SessionTimeMs: [200, 1_250, 2_250],
        sector3SessionTimeMs: [500, 1_600, 2_550],
      },
      RUS: {
        lapNumber: [],
        lapStartMs: [],
        lapEndMs: [],
        lapDurationMs: [],
        sector1DurationMs: [],
        sector2DurationMs: [],
        sector3DurationMs: [],
        sector1SessionTimeMs: [],
        sector2SessionTimeMs: [],
        sector3SessionTimeMs: [],
      },
    },
    ...overrides,
  }
}

function stintSummaryPayload(overrides?: Record<string, unknown>): Record<string, unknown> {
  return {
    contractVersion: 'v1',
    fixtureId: 'deterministic-race',
    drivers: {
      HAM: {
        stintNumber: [1, 2],
        compound: ['MEDIUM', 'HARD'],
        startLap: [1, 15],
        endLap: [14, null],
        startTimeMs: [0, 10_500],
        endTimeMs: [10_000, null],
        tyreLifeAtStart: [0, 5],
        isFreshTyre: [true, false],
        pitInTimeMs: [null, 10_000],
        pitOutTimeMs: [null, 10_500],
      },
      RUS: {
        stintNumber: [],
        compound: [],
        startLap: [],
        endLap: [],
        startTimeMs: [],
        endTimeMs: [],
        tyreLifeAtStart: [],
        isFreshTyre: [],
        pitInTimeMs: [],
        pitOutTimeMs: [],
      },
    },
    ...overrides,
  }
}

function pitLossModelPayload(overrides?: Record<string, unknown>): Record<string, unknown> {
  return {
    contractVersion: 'v1',
    fixtureId: 'deterministic-race',
    method: 'global-prior-weighted-mean-v1',
    baselineMs: 20_000,
    priorWeight: 5,
    timeMs: [0, 1_000, 2_000, 3_000],
    estimatedLossMs: [20_000, 19_500, 18_000, 17_000],
    observedSampleCount: [0, 3, 8, 15],
    ...overrides,
  }
}

function pitLossModelMinimalPayload(): Record<string, unknown> {
  return {
    contractVersion: 'v1',
    fixtureId: 'deterministic-race',
    method: 'global-prior-weighted-mean-v1',
    baselineMs: 20_000,
    priorWeight: 5,
    timeMs: [0],
    estimatedLossMs: [20_000],
    observedSampleCount: [0],
  }
}

function pitLossEstimateSidecarPayload(overrides?: Record<string, unknown>): Record<string, unknown> {
  return {
    contractVersion: 'v1',
    fixtureId: 'deterministic-race',
    trackId: 'deterministic-short-loop',
    method: 'track-status-median-v1',
    race: {
      timeMs: [0, 1_000, 2_000],
      estimatedLossMs: [20_000, 19_000, 18_000],
      observedSampleCount: [0, 1, 2],
    },
    ...overrides,
  }
}

/**
 * Australia curated baseline entry: Green 19300 ms, VSC 12300 ms (7-second
 * discount), SC 9300 ms (10-second discount). Each status timeline is a single
 * replay-start point without current-race observedSampleCount. The helper keeps
 * legacy audit fields so the loader's backwards-compatible discard path is
 * covered.
 */
function curatedPitLossEstimateSidecarPayload(overrides?: Record<string, unknown>): Record<string, unknown> {
  const provenance = {
    sourceUrl: 'https://www.formula1.com/en/latest/article/need-to-know-the-most-important-facts-stats-and-trivia-ahead-of-the-2026.7gyyqNLcwuCPZdXvgGwhCM',
    capturedDate: '2026-03-10',
    evidence: 'Official Formula1.com per-circuit pit-loss baseline for the 2026 Australian Grand Prix.',
    method: 'official-circuit-baseline',
  }
  return {
    contractVersion: 'v1',
    fixtureId: 'deterministic-race',
    trackId: 'deterministic-short-loop',
    method: 'curated-track-baseline-v1',
    catalogVersion: 'v1',
    provenance,
    evidenceCount: 1,
    confidence: 'high',
    statusMetadata: {
      green: {
        provenance: { ...provenance, evidence: 'Official Formula1.com Australia Green baseline.' },
        evidenceCount: 1,
        confidence: 'high',
      },
      sc: {
        provenance: { ...provenance, evidence: 'Derived Safety Car baseline for Australia.' },
        evidenceCount: 1,
        confidence: 'medium',
      },
      vsc: {
        provenance: { ...provenance, evidence: 'Derived Virtual Safety Car baseline for Australia.' },
        evidenceCount: 1,
        confidence: 'medium',
      },
    },
    race: { timeMs: [0], estimatedLossMs: [19_300] },
    safetyCar: { timeMs: [0], estimatedLossMs: [9_300] },
    virtualSafetyCar: { timeMs: [0], estimatedLossMs: [12_300] },
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Source builder — extends publishedFixtureSource with sidecar files
// ---------------------------------------------------------------------------

async function fixtureSourceWithSidecars(options: {
  lapSectorSidecar?: Record<string, unknown>
  stintSummary?: Record<string, unknown>
  pitLossModel?: Record<string, unknown>
  corruptLapSectorDigest?: boolean
  corruptStintDigest?: boolean
  corruptPitLossDigest?: boolean
  includePitLossEstimateSidecar?: boolean
  pitLossEstimateSidecar?: Record<string, unknown>
  corruptPitLossEstimateDigest?: boolean
  malformedSidecarPath?: 'lap-sector-sidecar.json' | 'stint-summary.json' | 'pit-loss-model.json' | 'pit-loss-estimate-sidecar.json'
  omitLapSector?: boolean
  omitStintSummary?: boolean
  omitPitLossModel?: boolean
  omitPitLossEstimateSidecar?: boolean
  wrongFixtureLapSector?: boolean
  wrongFixtureStintSummary?: boolean
  wrongFixturePitLossModel?: boolean
  wrongFixturePitLossEstimate?: boolean
  wrongTrackPitLossEstimate?: boolean
  unsafePitLossEstimatePath?: boolean
  seasonMetadata?: Record<string, unknown>
  telemetryCapabilities?: Record<string, unknown>
  omitSeasonMetadata?: boolean
  omitTelemetryCapabilities?: boolean
} = {}): Promise<{ source: ReplaySource; reads: string[] }> {
  const manifest = JSON.parse(decoder.decode(await fixtureSource.read('manifest.json'))) as Record<string, unknown>
  const track = await fixtureSource.read('track-assets.json')
  const chunkOne = await fixtureSource.read('chunks/chunk-001.json')
  const chunkTwo = await fixtureSource.read('chunks/chunk-002.json')

  const trackReference = manifest.trackAssets as Record<string, unknown>
  trackReference.sha256 = await sha256Hex(track)

  const chunkReferences = manifest.chunks as Array<Record<string, unknown>>
  chunkReferences[0].sha256 = await sha256Hex(chunkOne)
  chunkReferences[1].sha256 = await sha256Hex(chunkTwo)

  manifest.formatVersion = 'browser-delivery-v1'
  manifest.deliveryVersion = 'demo-v1'
  if (options.seasonMetadata !== undefined) manifest.seasonMetadata = options.seasonMetadata
  if (options.telemetryCapabilities !== undefined) manifest.telemetryCapabilities = options.telemetryCapabilities
  if (options.omitSeasonMetadata) delete manifest.seasonMetadata
  if (options.omitTelemetryCapabilities) delete manifest.telemetryCapabilities

  const files = new Map<string, Uint8Array>([
    ['generations/demo/track-assets.json', track],
    ['generations/demo/chunks/chunk-001.json', chunkOne],
    ['generations/demo/chunks/chunk-002.json', chunkTwo],
  ])

  // ---- Phase 1: write all sidecar file content to the file map ----

  // Lap sector sidecar — write payload to files first
  if (options.wrongFixtureLapSector) {
    const payloadBytes = encoder.encode(JSON.stringify(lapSectorSidecarPayload({ fixtureId: 'wrong-fixture' })))
    files.set('generations/demo/lap-sector-sidecar.json', payloadBytes)
  } else if (options.lapSectorSidecar) {
    files.set('generations/demo/lap-sector-sidecar.json', encoder.encode(JSON.stringify(options.lapSectorSidecar)))
  } else if (!options.omitLapSector) {
    files.set('generations/demo/lap-sector-sidecar.json', encoder.encode(JSON.stringify(lapSectorSidecarPayload())))
  }

  // Stint summary
  if (options.wrongFixtureStintSummary) {
    const payloadBytes = encoder.encode(JSON.stringify(stintSummaryPayload({ fixtureId: 'wrong-fixture' })))
    files.set('generations/demo/stint-summary.json', payloadBytes)
  } else if (options.stintSummary) {
    files.set('generations/demo/stint-summary.json', encoder.encode(JSON.stringify(options.stintSummary)))
  } else if (!options.omitStintSummary) {
    files.set('generations/demo/stint-summary.json', encoder.encode(JSON.stringify(stintSummaryPayload())))
  }

  // Pit loss model
  if (options.wrongFixturePitLossModel) {
    const payloadBytes = encoder.encode(JSON.stringify(pitLossModelPayload({ fixtureId: 'wrong-fixture' })))
    files.set('generations/demo/pit-loss-model.json', payloadBytes)
  } else if (options.pitLossModel) {
    files.set('generations/demo/pit-loss-model.json', encoder.encode(JSON.stringify(options.pitLossModel)))
  } else if (!options.omitPitLossModel) {
    files.set('generations/demo/pit-loss-model.json', encoder.encode(JSON.stringify(pitLossModelPayload())))
  }

  // Status-aware pit-loss estimate sidecar
  if (options.wrongFixturePitLossEstimate) {
    const payloadBytes = encoder.encode(JSON.stringify(pitLossEstimateSidecarPayload({ fixtureId: 'wrong-fixture' })))
    files.set('generations/demo/pit-loss-estimate-sidecar.json', payloadBytes)
  } else if (options.wrongTrackPitLossEstimate) {
    const payloadBytes = encoder.encode(JSON.stringify(pitLossEstimateSidecarPayload({ trackId: 'wrong-track' })))
    files.set('generations/demo/pit-loss-estimate-sidecar.json', payloadBytes)
  } else if (options.pitLossEstimateSidecar) {
    files.set('generations/demo/pit-loss-estimate-sidecar.json', encoder.encode(JSON.stringify(options.pitLossEstimateSidecar)))
  } else if (options.includePitLossEstimateSidecar && !options.omitPitLossEstimateSidecar) {
    files.set('generations/demo/pit-loss-estimate-sidecar.json', encoder.encode(JSON.stringify(pitLossEstimateSidecarPayload())))
  }

  // ---- Phase 2: apply malformed JSON overrides (after files are written, before digest computation) ----
  if (options.malformedSidecarPath) {
    files.set(`generations/demo/${options.malformedSidecarPath}`, encoder.encode('not json'))
  }

  // ---- Phase 3: compute digests and set manifest references from ACTUAL file content ----
  if (files.has('generations/demo/lap-sector-sidecar.json')) {
    const bytes = files.get('generations/demo/lap-sector-sidecar.json')!
    const digest = options.corruptLapSectorDigest ? '0'.repeat(64) : await sha256Hex(bytes)
    manifest.lapSectorSidecar = { path: 'lap-sector-sidecar.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v1:browser-lap-sector-sidecar', sha256: digest }
  }

  if (files.has('generations/demo/stint-summary.json')) {
    const bytes = files.get('generations/demo/stint-summary.json')!
    const digest = options.corruptStintDigest ? '0'.repeat(64) : await sha256Hex(bytes)
    manifest.stintSummary = { path: 'stint-summary.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v1:stint-summary', sha256: digest }
  }

  if (files.has('generations/demo/pit-loss-model.json')) {
    const bytes = files.get('generations/demo/pit-loss-model.json')!
    const digest = options.corruptPitLossDigest ? '0'.repeat(64) : await sha256Hex(bytes)
    manifest.pitLossModel = { path: 'pit-loss-model.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v1:pit-loss-model', sha256: digest }
  }

  if (files.has('generations/demo/pit-loss-estimate-sidecar.json')) {
    const bytes = files.get('generations/demo/pit-loss-estimate-sidecar.json')!
    const digest = options.corruptPitLossEstimateDigest ? '0'.repeat(64) : await sha256Hex(bytes)
    manifest.pitLossEstimateSidecar = {
      path: options.unsafePitLossEstimatePath ? '../pit-loss-estimate-sidecar.json' : 'pit-loss-estimate-sidecar.json',
      schemaId: 'urn:f1-cache-replay:schema:replay-data:v1:pit-loss-estimate-sidecar',
      sha256: digest,
    }
  }

  // ---- Phase 4: build final manifest and pointer ----
  const manifestBytes = encoder.encode(JSON.stringify(manifest))
  files.set('generations/demo/manifest.json', manifestBytes)

  const pointer = {
    formatVersion: 'browser-delivery-v1',
    deliveryVersion: 'demo-v1',
    manifestPath: 'generations/demo/manifest.json',
    manifestSha256: await sha256Hex(manifestBytes),
  }
  files.set('browser-current.json', encoder.encode(JSON.stringify(pointer)))

  const reads: string[] = []
  const source: ReplaySource = {
    async read(path) {
      reads.push(path)
      const value = files.get(path)
      if (!value) throw new Error(`Missing fixture path: ${path}`)
      return value
    },
  }

  return { source, reads }
}

// ---------------------------------------------------------------------------
// parseLapSectorSidecar
// ---------------------------------------------------------------------------

describe('parseLapSectorSidecar', () => {
  test('parses a valid minimal payload with one driver and one lap', () => {
    // Arrange
    const payload = {
      contractVersion: 'v1',
      fixtureId: 'deterministic-race',
      drivers: {
        HAM: {
          lapNumber: [1],
          lapStartMs: [0],
          lapEndMs: [800],
          lapDurationMs: [800],
          sector1DurationMs: [200],
          sector2DurationMs: [300],
          sector3DurationMs: [300],
          sector1SessionTimeMs: [0],
          sector2SessionTimeMs: [200],
          sector3SessionTimeMs: [500],
        },
      },
    }

    // Act
    const result = parseLapSectorSidecar(payload)

    // Assert
    expect(result.fixtureId).toBe('deterministic-race')
    expect(result.contractVersion).toBe('v1')
    expect(Object.keys(result.drivers)).toEqual(['HAM'])
    expect(result.drivers.HAM.lapNumber).toEqual([1])
    expect(result.drivers.HAM.lapStartMs).toEqual([0])
    expect(result.drivers.HAM.sector1DurationMs).toEqual([200])
  })

  test('parses a valid full payload with multiple drivers and laps', () => {
    // Arrange
    const payload = lapSectorSidecarPayload({
      drivers: {
        HAM: {
          lapNumber: [1, 2, 3],
          lapStartMs: [0, 1_000, 2_000],
          lapEndMs: [800, 1_900, 2_900],
          lapDurationMs: [800, 900, 900],
          sector1DurationMs: [200, null, 250],
          sector2DurationMs: [300, 350, 300],
          sector3DurationMs: [300, 300, 350],
          sector1SessionTimeMs: [0, 1_000, 2_000],
          sector2SessionTimeMs: [200, 1_250, 2_250],
          sector3SessionTimeMs: [500, 1_600, 2_550],
        },
        RUS: {
          lapNumber: [1, 2, 3],
          lapStartMs: [10, 1_010, 2_010],
          lapEndMs: [810, 1_910, 2_910],
          lapDurationMs: [800, 900, 900],
          sector1DurationMs: [201, null, 251],
          sector2DurationMs: [301, 351, 301],
          sector3DurationMs: [298, 298, 348],
          sector1SessionTimeMs: [10, 1_010, 2_010],
          sector2SessionTimeMs: [211, 1_261, 2_261],
          sector3SessionTimeMs: [512, 1_612, 2_562],
        },
      },
    })

    // Act
    const result = parseLapSectorSidecar(payload)

    // Assert
    expect(result.contractVersion).toBe('v1')
    expect(result.fixtureId).toBe('deterministic-race')
    // Drivers sorted alphabetically
    expect(Object.keys(result.drivers)).toEqual(['HAM', 'RUS'])
    expect(result.drivers.HAM.lapNumber.length).toBe(3)
    expect(result.drivers.RUS.lapNumber.length).toBe(3)
    expect(result.drivers.HAM.sector1DurationMs).toEqual([200, null, 250])
  })

  test('rejects a payload missing a required field', () => {
    // Arrange — omit drivers
    const payload = { contractVersion: 'v1', fixtureId: 'deterministic-race' }

    // Act & Assert
    expect(() => parseLapSectorSidecar(payload)).toThrow('required')
  })

  test('rejects a payload with an extra field', () => {
    // Arrange
    const payload = lapSectorSidecarPayload({ extraField: 1 })

    // Act & Assert
    expect(() => parseLapSectorSidecar(payload)).toThrow('not allowed')
  })

  test('rejects a payload with wrong contractVersion', () => {
    // Arrange
    const payload = lapSectorSidecarPayload({ contractVersion: 'v2' })

    // Act & Assert
    expect(() => parseLapSectorSidecar(payload)).toThrow('contract version v1')
  })

  test('rejects a payload with invalid fixtureId', () => {
    // Arrange
    const payload = lapSectorSidecarPayload({ fixtureId: 'Invalid!' })

    // Act & Assert
    expect(() => parseLapSectorSidecar(payload)).toThrow('fixture id is invalid')
  })

  test('rejects a payload with unequal array lengths across columns', () => {
    // Arrange — lapStartMs has 2 entries while lapNumber has 3
    const payload = lapSectorSidecarPayload()
    ;(payload.drivers as Record<string, Record<string, unknown>>).HAM.lapStartMs = [0, 1_000]

    // Act & Assert
    expect(() => parseLapSectorSidecar(payload)).toThrow('not aligned')
  })

  test('rejects a payload with an invalid driver ID', () => {
    // Arrange
    const payload = lapSectorSidecarPayload({
      drivers: {
        '!!!': {
          lapNumber: [1],
          lapStartMs: [0],
          lapEndMs: [800],
          lapDurationMs: [800],
          sector1DurationMs: [200],
          sector2DurationMs: [300],
          sector3DurationMs: [300],
          sector1SessionTimeMs: [0],
          sector2SessionTimeMs: [200],
          sector3SessionTimeMs: [500],
        },
      },
    })

    // Act & Assert
    expect(() => parseLapSectorSidecar(payload)).toThrow('driver ID is invalid')
  })

  test('rejects an empty drivers object', () => {
    // Arrange
    const payload = lapSectorSidecarPayload({ drivers: {} })

    // Act & Assert
    expect(() => parseLapSectorSidecar(payload)).toThrow('non-empty')
  })

  test('rejects when lapStartMs is not non-decreasing', () => {
    // Arrange — lapStartMs decreases
    const payload = lapSectorSidecarPayload()
    ;(payload.drivers as Record<string, Record<string, unknown>>).HAM.lapStartMs = [1_000, 0, 2_000]

    // Act & Assert
    expect(() => parseLapSectorSidecar(payload)).toThrow('ordered')
  })

  test('rejects when lapEndMs precedes lapStartMs', () => {
    // Arrange — make lapEndMs at index 0 strictly less than lapStartMs while preserving non-decreasing order
    const payload = lapSectorSidecarPayload()
    ;(payload.drivers as Record<string, Record<string, unknown>>).HAM.lapStartMs = [1, 1_000, 2_000]
    ;(payload.drivers as Record<string, Record<string, unknown>>).HAM.lapEndMs = [0, 1_900, 2_900]

    // Act & Assert
    expect(() => parseLapSectorSidecar(payload)).toThrow('lap end must not precede lap start')
  })

  test('rejects when lapNumber is not strictly increasing', () => {
    // Arrange — lapNumber has duplicate
    const payload = lapSectorSidecarPayload()
    ;(payload.drivers as Record<string, Record<string, unknown>>).HAM.lapNumber = [1, 1, 3]

    // Act & Assert
    expect(() => parseLapSectorSidecar(payload)).toThrow('strictly increasing')
  })

  test('returns a deeply frozen result', () => {
    // Arrange
    const payload = lapSectorSidecarPayload()

    // Act
    const result = parseLapSectorSidecar(payload)

    // Assert
    expect(Object.isFrozen(result)).toBe(true)
    expect(Object.isFrozen(result.drivers)).toBe(true)
    expect(Object.isFrozen(result.drivers.HAM)).toBe(true)
    expect(Object.isFrozen(result.drivers.HAM.lapNumber)).toBe(true)
    expect(Object.isFrozen(result.drivers.HAM.lapStartMs)).toBe(true)
    expect(Object.isFrozen(result.drivers.HAM.sector1DurationMs)).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// parseStintSummary
// ---------------------------------------------------------------------------

describe('parseStintSummary', () => {
  test('parses a valid minimal payload with one driver and one stint', () => {
    // Arrange
    const payload = {
      contractVersion: 'v1',
      fixtureId: 'deterministic-race',
      drivers: {
        HAM: {
          stintNumber: [1],
          compound: ['MEDIUM'],
          startLap: [1],
          endLap: [null],
          startTimeMs: [0],
          endTimeMs: [null],
          tyreLifeAtStart: [null],
          isFreshTyre: [true],
          pitInTimeMs: [null],
          pitOutTimeMs: [null],
        },
      },
    }

    // Act
    const result = parseStintSummary(payload)

    // Assert
    expect(result.contractVersion).toBe('v1')
    expect(result.fixtureId).toBe('deterministic-race')
    expect(Object.keys(result.drivers)).toEqual(['HAM'])
    expect(result.drivers.HAM.stintNumber).toEqual([1])
    expect(result.drivers.HAM.compound).toEqual(['MEDIUM'])
    expect(result.drivers.HAM.isFreshTyre).toEqual([true])
  })

  test('parses a valid full payload with multiple drivers and stints', () => {
    // Arrange
    const payload = stintSummaryPayload({
      drivers: {
        HAM: {
          stintNumber: [1, 2],
          compound: ['MEDIUM', 'HARD'],
          startLap: [1, 15],
          endLap: [14, null],
          startTimeMs: [0, 10_500],
          endTimeMs: [10_000, null],
          tyreLifeAtStart: [0, 5],
          isFreshTyre: [true, false],
          pitInTimeMs: [null, 10_000],
          pitOutTimeMs: [null, 10_500],
        },
        RUS: {
          stintNumber: [1],
          compound: ['SOFT'],
          startLap: [1],
          endLap: [null],
          startTimeMs: [0],
          endTimeMs: [null],
          tyreLifeAtStart: [0],
          isFreshTyre: [true],
          pitInTimeMs: [null],
          pitOutTimeMs: [null],
        },
      },
    })

    // Act
    const result = parseStintSummary(payload)

    // Assert
    expect(Object.keys(result.drivers)).toEqual(['HAM', 'RUS'])
    expect(result.drivers.HAM.compound).toEqual(['MEDIUM', 'HARD'])
    expect(result.drivers.RUS.compound).toEqual(['SOFT'])
  })

  test('rejects a payload missing a required field', () => {
    // Arrange — omit fixtureId
    const payload = {
      contractVersion: 'v1',
      drivers: {
        HAM: {
          stintNumber: [1],
          compound: ['MEDIUM'],
          startLap: [1],
          endLap: [null],
          startTimeMs: [0],
          endTimeMs: [null],
          tyreLifeAtStart: [null],
          isFreshTyre: [true],
          pitInTimeMs: [null],
          pitOutTimeMs: [null],
        },
      },
    }

    // Act & Assert
    expect(() => parseStintSummary(payload)).toThrow('required')
  })

  test('rejects a payload with an extra field at the top level', () => {
    // Arrange
    const payload = stintSummaryPayload({ extra: true })

    // Act & Assert
    expect(() => parseStintSummary(payload)).toThrow('not allowed')
  })

  test('rejects a payload with wrong contractVersion', () => {
    // Arrange
    const payload = stintSummaryPayload({ contractVersion: 'v99' })

    // Act & Assert
    expect(() => parseStintSummary(payload)).toThrow('contract version v1')
  })

  test('rejects a payload with invalid fixtureId', () => {
    // Arrange
    const payload = stintSummaryPayload({ fixtureId: 'INVALID-FORMAT' })

    // Act & Assert
    expect(() => parseStintSummary(payload)).toThrow('invalid')
  })

  test('rejects a payload with unequal array lengths across columns', () => {
    // Arrange — compound has 1 entry while stintNumber has 2
    const payload = stintSummaryPayload()
    ;(payload.drivers as Record<string, Record<string, unknown>>).HAM.compound = ['MEDIUM']

    // Act & Assert
    expect(() => parseStintSummary(payload)).toThrow('not aligned')
  })

  test('rejects a payload with an invalid driver ID', () => {
    // Arrange
    const payload = stintSummaryPayload({
      drivers: {
        'LOWER': {
          stintNumber: [1],
          compound: ['MEDIUM'],
          startLap: [1],
          endLap: [null],
          startTimeMs: [0],
          endTimeMs: [null],
          tyreLifeAtStart: [null],
          isFreshTyre: [true],
          pitInTimeMs: [null],
          pitOutTimeMs: [null],
        },
      },
    })

    // Act & Assert
    expect(() => parseStintSummary(payload)).toThrow('driver ID is invalid')
  })

  test('rejects an empty drivers object', () => {
    // Arrange
    const payload = stintSummaryPayload({ drivers: {} })

    // Act & Assert
    expect(() => parseStintSummary(payload)).toThrow('non-empty')
  })

  test('rejects when stintNumber is not strictly increasing', () => {
    // Arrange
    const payload = stintSummaryPayload()
    ;(payload.drivers as Record<string, Record<string, unknown>>).HAM.stintNumber = [2, 1]

    // Act & Assert
    expect(() => parseStintSummary(payload)).toThrow('strictly increasing')
  })

  test('rejects when endLap precedes startLap', () => {
    // Arrange — make endLap strictly less than startLap at index 0
    const payload = stintSummaryPayload()
    ;(payload.drivers as Record<string, Record<string, unknown>>).HAM.startLap = [5, 15]
    ;(payload.drivers as Record<string, Record<string, unknown>>).HAM.endLap = [1, null]

    // Act & Assert
    expect(() => parseStintSummary(payload)).toThrow('endLap must not precede startLap')
  })

  test('rejects when endTimeMs precedes startTimeMs', () => {
    // Arrange
    const payload = stintSummaryPayload()
    ;(payload.drivers as Record<string, Record<string, unknown>>).HAM.startTimeMs = [15_000, 10_500]

    // Act & Assert
    expect(() => parseStintSummary(payload)).toThrow('endTimeMs must not precede startTimeMs')
  })

  test('returns a deeply frozen result', () => {
    // Arrange
    const payload = stintSummaryPayload()

    // Act
    const result = parseStintSummary(payload)

    // Assert
    expect(Object.isFrozen(result)).toBe(true)
    expect(Object.isFrozen(result.drivers)).toBe(true)
    expect(Object.isFrozen(result.drivers.HAM)).toBe(true)
    expect(Object.isFrozen(result.drivers.HAM.stintNumber)).toBe(true)
    expect(Object.isFrozen(result.drivers.HAM.compound)).toBe(true)
    expect(Object.isFrozen(result.drivers.HAM.isFreshTyre)).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// parsePitLossModel
// ---------------------------------------------------------------------------

describe('parsePitLossModel', () => {
  test('parses a valid minimal payload with single-element arrays', () => {
    // Arrange
    const payload = pitLossModelMinimalPayload()

    // Act
    const result = parsePitLossModel(payload)

    // Assert
    expect(result.contractVersion).toBe('v1')
    expect(result.fixtureId).toBe('deterministic-race')
    expect(result.method).toBe('global-prior-weighted-mean-v1')
    expect(result.baselineMs).toBe(20_000)
    expect(result.priorWeight).toBe(5)
    expect(result.timeMs).toEqual([0])
    expect(result.estimatedLossMs).toEqual([20_000])
    expect(result.observedSampleCount).toEqual([0])
  })

  test('parses a valid full payload with multiple entries', () => {
    // Arrange
    const payload = pitLossModelPayload()

    // Act
    const result = parsePitLossModel(payload)

    // Assert
    expect(result.baselineMs).toBe(20_000)
    expect(result.timeMs).toEqual([0, 1_000, 2_000, 3_000])
    expect(result.estimatedLossMs).toEqual([20_000, 19_500, 18_000, 17_000])
    expect(result.observedSampleCount).toEqual([0, 3, 8, 15])
  })

  test('rejects a payload missing a required field', () => {
    // Arrange — omit baselineMs
    const payload = {
      contractVersion: 'v1',
      fixtureId: 'deterministic-race',
      method: 'global-prior-weighted-mean-v1',
      priorWeight: 5,
      timeMs: [0],
      estimatedLossMs: [20_000],
      observedSampleCount: [0],
    }

    // Act & Assert
    expect(() => parsePitLossModel(payload)).toThrow('required')
  })

  test('rejects a payload with an extra field', () => {
    // Arrange
    const payload = pitLossModelPayload({ extra: 'field' })

    // Act & Assert
    expect(() => parsePitLossModel(payload)).toThrow('not allowed')
  })

  test('rejects a payload with wrong contractVersion', () => {
    // Arrange
    const payload = pitLossModelPayload({ contractVersion: 'v0' })

    // Act & Assert
    expect(() => parsePitLossModel(payload)).toThrow('contract version v1')
  })

  test('rejects a payload with invalid fixtureId', () => {
    // Arrange
    const payload = pitLossModelPayload({ fixtureId: 'WRONG_FORMAT' })

    // Act & Assert
    expect(() => parsePitLossModel(payload)).toThrow('invalid')
  })

  test('rejects a payload with unequal array lengths', () => {
    // Arrange — estimatedLossMs is shorter than timeMs
    const payload = pitLossModelPayload()
    ;(payload as Record<string, unknown>).estimatedLossMs = [20_000, 19_500]

    // Act & Assert
    expect(() => parsePitLossModel(payload)).toThrow('must be aligned')
  })

  test('rejects a payload with non-increasing timeMs', () => {
    // Arrange — timeMs decreases
    const payload = pitLossModelPayload()
    ;(payload as Record<string, unknown>).timeMs = [0, 2_000, 1_000, 3_000]

    // Act & Assert
    expect(() => parsePitLossModel(payload)).toThrow('strictly increasing')
  })

  test('rejects a payload where timeMs is not strictly increasing (duplicate)', () => {
    // Arrange — duplicate timeMs value
    const payload = pitLossModelPayload()
    ;(payload as Record<string, unknown>).timeMs = [0, 1_000, 1_000, 3_000]

    // Act & Assert
    expect(() => parsePitLossModel(payload)).toThrow('strictly increasing')
  })

  test('rejects a payload where observedSampleCount is not strictly increasing', () => {
    // Arrange — observedSampleCount has duplicate
    const payload = pitLossModelPayload()
    ;(payload as Record<string, unknown>).observedSampleCount = [0, 8, 8, 15]

    // Act & Assert
    expect(() => parsePitLossModel(payload)).toThrow('strictly increase')
  })

  test('rejects a payload where observedSampleCount decreases', () => {
    // Arrange — observedSampleCount decreases
    const payload = pitLossModelPayload()
    ;(payload as Record<string, unknown>).observedSampleCount = [0, 15, 8, 20]

    // Act & Assert
    expect(() => parsePitLossModel(payload)).toThrow('strictly increase')
  })

  test('rejects a payload where first estimatedLossMs does not equal baselineMs', () => {
    // Arrange
    const payload = pitLossModelPayload()
    ;(payload as Record<string, unknown>).estimatedLossMs = [10_000, 19_500, 18_000, 17_000]

    // Act & Assert
    expect(() => parsePitLossModel(payload)).toThrow('first estimatedLossMs must equal baselineMs')
  })

  test('rejects a payload where first observedSampleCount is not zero', () => {
    // Arrange
    const payload = pitLossModelPayload()
    ;(payload as Record<string, unknown>).observedSampleCount = [1, 3, 8, 15]

    // Act & Assert
    expect(() => parsePitLossModel(payload)).toThrow('first observedSampleCount must be zero')
  })

  test('rejects an empty timeMs array', () => {
    // Arrange
    const payload = pitLossModelPayload()
    ;(payload as Record<string, unknown>).timeMs = []
    ;(payload as Record<string, unknown>).estimatedLossMs = []
    ;(payload as Record<string, unknown>).observedSampleCount = []

    // Act & Assert
    expect(() => parsePitLossModel(payload)).toThrow('must be non-empty')
  })

  test('rejects an invalid method', () => {
    // Arrange
    const payload = pitLossModelPayload({ method: 'unknown-method' })

    // Act & Assert
    expect(() => parsePitLossModel(payload)).toThrow('method is invalid')
  })

  test('returns a deeply frozen result', () => {
    // Arrange
    const payload = pitLossModelPayload()

    // Act
    const result = parsePitLossModel(payload)

    // Assert
    expect(Object.isFrozen(result)).toBe(true)
    expect(Object.isFrozen(result.timeMs)).toBe(true)
    expect(Object.isFrozen(result.estimatedLossMs)).toBe(true)
    expect(Object.isFrozen(result.observedSampleCount)).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// parsePitLossEstimateSidecar
// ---------------------------------------------------------------------------

describe('parsePitLossEstimateSidecar', () => {
  test('parses a valid race-only sidecar', () => {
    // Arrange
    const payload = pitLossEstimateSidecarPayload()

    // Act
    const result = parsePitLossEstimateSidecar(payload)

    // Assert
    expect(result.trackId).toBe('deterministic-short-loop')
    expect(result.race.estimatedLossMs).toEqual([20_000, 19_000, 18_000])
    expect(result.safetyCar).toBeUndefined()
    expect(result.virtualSafetyCar).toBeUndefined()
  })

  test('parses a valid Safety Car sidecar', () => {
    // Arrange
    const payload = pitLossEstimateSidecarPayload({
      safetyCar: { timeMs: [0, 1_000], estimatedLossMs: [20_000, 18_000], observedSampleCount: [0, 1] },
    })

    // Act
    const result = parsePitLossEstimateSidecar(payload)

    // Assert
    expect(result.safetyCar).toEqual({ timeMs: [0, 1_000], estimatedLossMs: [20_000, 18_000], observedSampleCount: [0, 1] })
  })

  test('parses a valid Virtual Safety Car sidecar', () => {
    // Arrange
    const payload = pitLossEstimateSidecarPayload({
      virtualSafetyCar: { timeMs: [0, 1_000], estimatedLossMs: [20_000, 19_000], observedSampleCount: [0, 1] },
    })

    // Act
    const result = parsePitLossEstimateSidecar(payload)

    // Assert
    expect(result.virtualSafetyCar).toEqual({ timeMs: [0, 1_000], estimatedLossMs: [20_000, 19_000], observedSampleCount: [0, 1] })
  })

  test('parses an unavailable status estimate', () => {
    // Arrange
    const payload = pitLossEstimateSidecarPayload({ safetyCar: { status: 'unavailable' } })

    // Act
    const result = parsePitLossEstimateSidecar(payload)

    // Assert
    expect(result.safetyCar).toEqual({ status: 'unavailable' })
  })

  test.each([
    ['missing race timeline', { race: undefined }],
    ['misaligned timeline arrays', { race: { timeMs: [0], estimatedLossMs: [], observedSampleCount: [0] } }],
    ['invalid unavailable status', { safetyCar: { status: 'missing' } }],
  ])('rejects malformed sidecar shape: %s', (_description, override) => {
    // Arrange
    const payload = pitLossEstimateSidecarPayload(override)

    // Act & Assert
    expect(() => parsePitLossEstimateSidecar(payload)).toThrow()
  })
})

// ---------------------------------------------------------------------------
// parsePitLossEstimateSidecar — curated baseline entries
// ---------------------------------------------------------------------------

describe('parsePitLossEstimateSidecar curated baseline', () => {
  test('parses a valid curated sidecar with single replay-start values', () => {
    // Arrange
    const payload = curatedPitLossEstimateSidecarPayload()

    // Act
    const result = parsePitLossEstimateSidecar(payload) as Extract<PitLossEstimateSidecar, { readonly method: 'curated-track-baseline-v1' }>

    // Assert
    expect(result.method).toBe('curated-track-baseline-v1')
    expect(result.race).toEqual({ timeMs: [0], estimatedLossMs: [19_300] })
    expect(result.safetyCar).toEqual({ timeMs: [0], estimatedLossMs: [9_300] })
    expect(result.virtualSafetyCar).toEqual({ timeMs: [0], estimatedLossMs: [12_300] })
    // Curated replay-start values carry no current-race observedSampleCount.
    expect(result.race).not.toHaveProperty('observedSampleCount')
    for (const key of ['catalogVersion', 'provenance', 'evidenceCount', 'confidence', 'statusMetadata']) {
      expect(result).not.toHaveProperty(key)
    }
  })

  test('rejects a curated sidecar with an unavailable status timeline', () => {
    // Arrange — unavailable is a legacy-only shape; curated sidecars require
    // available replay-start timelines for all three statuses
    const payload = curatedPitLossEstimateSidecarPayload({ safetyCar: { status: 'unavailable' } })

    // Act & Assert
    expect(() => parsePitLossEstimateSidecar(payload)).toThrow('not valid for curated sidecars')
  })

  test('rejects a curated sidecar where Safety Car exceeds Virtual Safety Car', () => {
    // Arrange — SC 13000 > VSC 12300 violates SC <= VSC <= Green
    const payload = curatedPitLossEstimateSidecarPayload()
    payload.safetyCar = { timeMs: [0], estimatedLossMs: [13_000] }

    // Act & Assert
    expect(() => parsePitLossEstimateSidecar(payload)).toThrow('SC <= VSC <= Green')
  })

  test('rejects a curated sidecar where Virtual Safety Car exceeds Green', () => {
    // Arrange — VSC 20000 > Green 19300 violates SC <= VSC <= Green
    const payload = curatedPitLossEstimateSidecarPayload()
    payload.virtualSafetyCar = { timeMs: [0], estimatedLossMs: [20_000] }

    // Act & Assert
    expect(() => parsePitLossEstimateSidecar(payload)).toThrow('SC <= VSC <= Green')
  })

  test('rejects a curated sidecar whose race timeline is not a single replay-start point', () => {
    // Arrange — curated values must be static generation-time values
    const payload = curatedPitLossEstimateSidecarPayload()
    payload.race = { timeMs: [0, 100], estimatedLossMs: [19_300, 19_000] }

    // Act & Assert
    expect(() => parsePitLossEstimateSidecar(payload)).toThrow('must contain one replay-start value')
  })

})

// ---------------------------------------------------------------------------
// Sidecar loader integration
// ---------------------------------------------------------------------------

describe('sidecar loader integration', () => {
  test.each([
    ['race-only', pitLossEstimateSidecarPayload()],
    ['Safety Car', pitLossEstimateSidecarPayload({ safetyCar: { timeMs: [0, 1_000], estimatedLossMs: [20_000, 18_000], observedSampleCount: [0, 1] } })],
    ['Virtual Safety Car', pitLossEstimateSidecarPayload({ virtualSafetyCar: { timeMs: [0, 1_000], estimatedLossMs: [20_000, 19_000], observedSampleCount: [0, 1] } })],
  ])('loads a valid %s pit-loss estimate sidecar', async (_name, sidecar) => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({ pitLossEstimateSidecar: sidecar })

    // Act
    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })

    // Assert
    expect(index.pitLossEstimateSidecar).toEqual(sidecar)
  })

  test('loads an unavailable status estimate without treating it as malformed', async () => {
    // Arrange
    const sidecar = pitLossEstimateSidecarPayload({ virtualSafetyCar: { status: 'unavailable' } })
    const { source } = await fixtureSourceWithSidecars({ pitLossEstimateSidecar: sidecar })

    // Act
    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })

    // Assert
    expect(index.pitLossEstimateSidecar?.virtualSafetyCar).toEqual({ status: 'unavailable' })
  })

  test('loads an omitted estimate sidecar from a legacy delivery', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({
      omitPitLossEstimateSidecar: true,
      omitSeasonMetadata: true,
      omitTelemetryCapabilities: true,
    })

    // Act
    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })

    // Assert
    expect(index.pitLossEstimateSidecar).toBeUndefined()
    expect(index.pitLossModel).toBeDefined()
    expect(index.manifest.pitLossEstimateSidecar).toBeUndefined()
  })

  test('rejects a malformed estimate sidecar payload', async () => {
    // Arrange
    const sidecar = pitLossEstimateSidecarPayload({
      race: { timeMs: [0], estimatedLossMs: [], observedSampleCount: [0] },
    })
    const { source } = await fixtureSourceWithSidecars({ pitLossEstimateSidecar: sidecar })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('arrays must be aligned')
  })

  test('rejects an estimate sidecar SHA-256 digest mismatch', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({
      includePitLossEstimateSidecar: true,
      corruptPitLossEstimateDigest: true,
    })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('digest does not match')
  })

  test('rejects an unsafe estimate sidecar path before reading it', async () => {
    // Arrange
    const { source, reads } = await fixtureSourceWithSidecars({
      includePitLossEstimateSidecar: true,
      unsafePitLossEstimatePath: true,
    })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('path is unsupported')
    expect(reads).not.toContain('pit-loss-estimate-sidecar.json')
  })

  test('rejects an estimate sidecar with the wrong fixtureId', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({ wrongFixturePitLossEstimate: true })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('fixture identities disagree')
  })

  test('rejects an estimate sidecar with the wrong trackId', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({ wrongTrackPitLossEstimate: true })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('track identities disagree')
  })

  test('exposes frozen manifest telemetry metadata alongside sidecars', async () => {
    const { source } = await fixtureSourceWithSidecars({
      seasonMetadata: { year: 2026 },
      telemetryCapabilities: {
        drs: 'not-published',
        overtakeMode: 'not-published',
        activeAero: 'not-published',
        ersReplacement: 'not-published',
      },
    })

    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })

    expect(index.seasonMetadata).toEqual({ year: 2026 })
    expect(index.telemetryCapabilities?.drs).toBe('not-published')
    expect(Object.isFrozen(index.seasonMetadata!)).toBe(true)
    expect(Object.isFrozen(index.telemetryCapabilities!)).toBe(true)
  })

  test('loads a legacy manifest without season or telemetry metadata alongside sidecars', async () => {
    // Arrange — strip both optional metadata blocks but keep all three sidecars
    const { source } = await fixtureSourceWithSidecars({
      omitSeasonMetadata: true,
      omitTelemetryCapabilities: true,
    })

    // Act
    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })

    // Assert — capability metadata is absent while sidecars still load
    expect(index).not.toHaveProperty('seasonMetadata')
    expect(index).not.toHaveProperty('telemetryCapabilities')
    expect(index.lapSectorSidecar).toBeDefined()
    expect(index.stintSummary).toBeDefined()
    expect(index.pitLossModel).toBeDefined()
  })

  test('loads all three sidecars in parallel with valid references', async () => {
    // Arrange
    const { source, reads } = await fixtureSourceWithSidecars()

    // Act
    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })

    // Assert — mandatory files loaded first, sidecars in parallel
    expect(reads.slice(0, 3)).toEqual([
      'browser-current.json',
      'generations/demo/manifest.json',
      'generations/demo/track-assets.json',
    ])
    expect(reads).toContain('generations/demo/lap-sector-sidecar.json')
    expect(reads).toContain('generations/demo/stint-summary.json')
    expect(reads).toContain('generations/demo/pit-loss-model.json')
    expect(index.lapSectorSidecar).toBeDefined()
    expect(index.stintSummary).toBeDefined()
    expect(index.pitLossModel).toBeDefined()
    expect(index.lapSectorSidecar!.fixtureId).toBe('deterministic-race')
    expect(index.stintSummary!.fixtureId).toBe('deterministic-race')
    expect(index.pitLossModel!.fixtureId).toBe('deterministic-race')
  })

  test('loads successfully when no sidecar references exist on the manifest', async () => {
    // Arrange — omit all sidecar references
    const { source, reads } = await fixtureSourceWithSidecars({
      omitLapSector: true,
      omitStintSummary: true,
      omitPitLossModel: true,
    })

    // Act
    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })

    // Assert — only manifest and track assets are read
    expect(reads).toEqual([
      'browser-current.json',
      'generations/demo/manifest.json',
      'generations/demo/track-assets.json',
    ])
    expect(index.lapSectorSidecar).toBeUndefined()
    expect(index.stintSummary).toBeUndefined()
    expect(index.pitLossModel).toBeUndefined()
  })

  test('loads a subset when only some sidecar references are present', async () => {
    // Arrange — only lap sector and pit loss model
    const { source, reads } = await fixtureSourceWithSidecars({
      omitStintSummary: true,
    })

    // Act
    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })

    // Assert
    expect(reads.slice(0, 3)).toEqual([
      'browser-current.json',
      'generations/demo/manifest.json',
      'generations/demo/track-assets.json',
    ])
    expect(reads).toContain('generations/demo/lap-sector-sidecar.json')
    expect(reads).not.toContain('generations/demo/stint-summary.json')
    expect(reads).toContain('generations/demo/pit-loss-model.json')
    expect(index.lapSectorSidecar).toBeDefined()
    expect(index.stintSummary).toBeUndefined()
    expect(index.pitLossModel).toBeDefined()
  })

  test('rejects initialization on lap sector sidecar SHA-256 digest mismatch', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({
      omitStintSummary: true,
      omitPitLossModel: true,
      corruptLapSectorDigest: true,
    })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('digest does not match')
  })

  test('rejects initialization on stint summary SHA-256 digest mismatch', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({
      omitLapSector: true,
      omitPitLossModel: true,
      corruptStintDigest: true,
    })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('digest does not match')
  })

  test('rejects initialization on pit loss model SHA-256 digest mismatch', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({
      omitLapSector: true,
      omitStintSummary: true,
      corruptPitLossDigest: true,
    })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('digest does not match')
  })

  test('rejects initialization when lap sector sidecar has wrong fixtureId', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({
      omitStintSummary: true,
      omitPitLossModel: true,
      wrongFixtureLapSector: true,
    })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('fixture identities disagree')
  })

  test('rejects initialization when stint summary has wrong fixtureId', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({
      omitLapSector: true,
      omitPitLossModel: true,
      wrongFixtureStintSummary: true,
    })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('fixture identities disagree')
  })

  test('rejects initialization when pit loss model has wrong fixtureId', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({
      omitLapSector: true,
      omitStintSummary: true,
      wrongFixturePitLossModel: true,
    })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('fixture identities disagree')
  })

  test('rejects initialization when sidecar file contains malformed JSON', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({
      omitStintSummary: true,
      omitPitLossModel: true,
      malformedSidecarPath: 'lap-sector-sidecar.json',
    })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('JSON is invalid')
  })

  test('rejects initialization when sidecar drivers disagree with manifest drivers', async () => {
    // Arrange — lap sector sidecar has a driver not in the manifest
    const { source } = await fixtureSourceWithSidecars({
      omitStintSummary: true,
      omitPitLossModel: true,
      lapSectorSidecar: lapSectorSidecarPayload({
        drivers: {
          ALS: {
            lapNumber: [1],
            lapStartMs: [0],
            lapEndMs: [800],
            lapDurationMs: [800],
            sector1DurationMs: [200],
            sector2DurationMs: [300],
            sector3DurationMs: [300],
            sector1SessionTimeMs: [0],
            sector2SessionTimeMs: [200],
            sector3SessionTimeMs: [500],
          },
        },
      }),
    })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('drivers disagree')
  })

  test('all parsed sidecar data on the index is deeply frozen', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars()

    // Act
    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })

    // Assert
    expect(Object.isFrozen(index)).toBe(true)
    expect(Object.isFrozen(index.lapSectorSidecar)).toBe(true)
    expect(Object.isFrozen(index.stintSummary)).toBe(true)
    expect(Object.isFrozen(index.pitLossModel)).toBe(true)
    // Deep freeze for lap sector sidecar
    expect(Object.isFrozen(index.lapSectorSidecar!.drivers)).toBe(true)
    expect(Object.isFrozen(index.lapSectorSidecar!.drivers.HAM)).toBe(true)
    expect(Object.isFrozen(index.lapSectorSidecar!.drivers.HAM.lapNumber)).toBe(true)
    expect(Object.isFrozen(index.lapSectorSidecar!.drivers.HAM.sector1DurationMs)).toBe(true)
    // Deep freeze for stint summary
    expect(Object.isFrozen(index.stintSummary!.drivers)).toBe(true)
    expect(Object.isFrozen(index.stintSummary!.drivers.HAM)).toBe(true)
    expect(Object.isFrozen(index.stintSummary!.drivers.HAM.stintNumber)).toBe(true)
    expect(Object.isFrozen(index.stintSummary!.drivers.HAM.compound)).toBe(true)
    // Deep freeze for pit loss model
    expect(Object.isFrozen(index.pitLossModel!.timeMs)).toBe(true)
    expect(Object.isFrozen(index.pitLossModel!.estimatedLossMs)).toBe(true)
    expect(Object.isFrozen(index.pitLossModel!.observedSampleCount)).toBe(true)
  })

  test('loads the exact production pointer layout with all sidecars present', async () => {
    // Arrange
    const { source, reads } = await fixtureSourceWithSidecars()

    // Act
    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })

    // Assert — reads happen in parallel alongside the pointer
    expect(reads.slice(0, 3)).toEqual([
      'browser-current.json',
      'generations/demo/manifest.json',
      'generations/demo/track-assets.json',
    ])
    expect(reads).toContain('generations/demo/lap-sector-sidecar.json')
    expect(reads).toContain('generations/demo/stint-summary.json')
    expect(reads).toContain('generations/demo/pit-loss-model.json')
    expect(index.manifest.lapSectorSidecar).toBeDefined()
    expect(index.manifest.stintSummary).toBeDefined()
    expect(index.manifest.pitLossModel).toBeDefined()
  })

  test('parses a manifest that includes all sidecar references', async () => {
    // Arrange
    const manifest = JSON.parse(decoder.decode(await fixtureSource.read('manifest.json'))) as Record<string, unknown>
    manifest.lapSectorSidecar = {
      path: 'lap-sector-sidecar.json',
      schemaId: 'urn:f1-cache-replay:schema:replay-data:v1:browser-lap-sector-sidecar',
      sha256: 'a'.repeat(64),
    }
    manifest.stintSummary = {
      path: 'stint-summary.json',
      schemaId: 'urn:f1-cache-replay:schema:replay-data:v1:stint-summary',
      sha256: 'a'.repeat(64),
    }
    manifest.pitLossModel = {
      path: 'pit-loss-model.json',
      schemaId: 'urn:f1-cache-replay:schema:replay-data:v1:pit-loss-model',
      sha256: 'a'.repeat(64),
    }

    // Act
    const parsed = parseManifest(manifest)

    // Assert
    expect(parsed.lapSectorSidecar).toBeDefined()
    expect(parsed.stintSummary).toBeDefined()
    expect(parsed.pitLossModel).toBeDefined()
    expect(parsed.lapSectorSidecar!.path).toBe('lap-sector-sidecar.json')
    expect(parsed.stintSummary!.path).toBe('stint-summary.json')
    expect(parsed.pitLossModel!.path).toBe('pit-loss-model.json')
  })

  test('loads a digest-checked curated pit-loss estimate sidecar', async () => {
    // Arrange
    const sidecar = curatedPitLossEstimateSidecarPayload()
    const { source } = await fixtureSourceWithSidecars({ pitLossEstimateSidecar: sidecar })

    // Act
    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })

    // Assert — legacy audit fields are discarded and the loaded object is frozen
    expect(index.pitLossEstimateSidecar).toMatchObject({
      contractVersion: 'v1',
      fixtureId: 'deterministic-race',
      trackId: 'deterministic-short-loop',
      method: 'curated-track-baseline-v1',
      race: sidecar.race,
      safetyCar: sidecar.safetyCar,
      virtualSafetyCar: sidecar.virtualSafetyCar,
    })
    for (const key of ['catalogVersion', 'provenance', 'evidenceCount', 'confidence', 'statusMetadata']) {
      expect(index.pitLossEstimateSidecar).not.toHaveProperty(key)
    }
    expect(index.pitLossEstimateSidecar?.method).toBe('curated-track-baseline-v1')
    expect(Object.isFrozen(index.pitLossEstimateSidecar)).toBe(true)
  })

  test('rejects a curated pit-loss estimate sidecar SHA-256 digest mismatch', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({
      pitLossEstimateSidecar: curatedPitLossEstimateSidecarPayload(),
      corruptPitLossEstimateDigest: true,
    })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('digest does not match')
  })

  test('rejects a curated pit-loss estimate sidecar with the wrong trackId', async () => {
    // Arrange
    const { source } = await fixtureSourceWithSidecars({
      pitLossEstimateSidecar: curatedPitLossEstimateSidecarPayload({ trackId: 'wrong-track' }),
    })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('track identities disagree')
  })

  test('rejects a curated pit-loss estimate sidecar that violates monotonic baseline ordering', async () => {
    // Arrange — SC 13000 > VSC 12300 fails the SC <= VSC <= Green invariant
    const { source } = await fixtureSourceWithSidecars({
      pitLossEstimateSidecar: curatedPitLossEstimateSidecarPayload({
        safetyCar: { timeMs: [0], estimatedLossMs: [13_000] },
      }),
    })

    // Act & Assert
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' }))
      .rejects.toThrow('SC <= VSC <= Green')
  })

  test('legacy loading does not attempt to read the curated sidecar when absent', async () => {
    // Arrange
    const { source, reads } = await fixtureSourceWithSidecars({
      omitPitLossEstimateSidecar: true,
    })

    // Act
    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })

    // Assert — legacy delivery keeps the pit-loss model and never reads the sidecar
    expect(index.pitLossEstimateSidecar).toBeUndefined()
    expect(index.manifest.pitLossEstimateSidecar).toBeUndefined()
    expect(index.pitLossModel).toBeDefined()
    expect(reads).not.toContain('generations/demo/pit-loss-estimate-sidecar.json')
  })
})
