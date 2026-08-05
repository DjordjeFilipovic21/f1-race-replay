import { describe, expect, test } from 'vitest'
import { sha256Hex } from '../../../src/data/replay/digest'
import {
  parseLapSectorSidecar,
  parseManifest,
  parseQualifyingTimeline,
} from '../../../src/data/replay/guards'
import { loadReplayIndex } from '../../../src/data/replay/loader'
import { createSessionCapabilities } from '../../../src/features/replay/session-capabilities'
import type { ReplaySource } from '../../../src/data/replay/types'

const schema = 'urn:f1-cache-replay:schema:replay-data:v2:qualifying-timeline'
const fixtureId = 'qualifying-timeline-fixture'

// ---------------------------------------------------------------------------
// Payload builders
// ---------------------------------------------------------------------------

function lapSectorSidecarPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    contractVersion: 'v2',
    fixtureId,
    phaseBoundaries: [
      { phase: 'Q1', startMs: 0 },
      { phase: 'Q2', startMs: 1_000 },
      { phase: 'Q3', startMs: 2_000 },
    ],
    drivers: {
      HAM: {
        lapNumber: [1, 2, 3],
        lapStartMs: [0, 1_000, 2_000],
        lapEndMs: [900, 1_900, 2_900],
        lapDurationMs: [900, 900, 900],
        sector1DurationMs: [300, null, 300],
        sector2DurationMs: [300, 350, 300],
        sector3DurationMs: [300, 300, 300],
        sector1SessionTimeMs: [0, 1_000, 2_000],
        sector2SessionTimeMs: [300, 1_300, 2_300],
        sector3SessionTimeMs: [600, 1_600, 2_600],
        qualifyingPhase: ['Q1', 'Q2', 'Q3'],
        lapKind: ['flying', 'outlap', 'unknown'],
      },
    },
    ...overrides,
  }
}

function qualifyingTimelinePayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    contractVersion: 'v2',
    fixtureId,
    startMs: 0,
    endMs: 2_000,
    intervals: [
      { kind: 'yellow', startMs: 500, endMs: 800 },
      { kind: 'red', startMs: 1_200, endMs: 1_500 },
    ],
    incidentMarkers: [
      { driverId: 'HAM', timeMs: 700, source: 'race-control-car-event', rawMessage: 'CAR 44 STOPS', lapNumber: 2 },
      { driverId: 'HAM', timeMs: 1_300, source: 'race-control-car-event', rawMessage: 'CAR 44 CRASH' },
    ],
    ...overrides,
  }
}

function minimalManifest(
  timelineSha256: string,
  trackSha256: string,
  chunkSha256: string,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    contractVersion: 'v2',
    formatVersion: 'browser-delivery-v2',
    sessionMode: 'qualifying',
    fixtureId,
    fixtureName: 'Qualifying Timeline Fixture',
    schemas: {
      manifest: 'urn:f1-cache-replay:schema:replay-data:v2:manifest',
      chunk: 'urn:f1-cache-replay:schema:replay-data:v2:chunk',
      trackAssets: 'urn:f1-cache-replay:schema:replay-data:v2:track-assets',
      qualifyingTimeline: schema,
    },
    trackAssets: { path: 'track-assets.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:track-assets', sha256: trackSha256 },
    chunks: [{ sequence: 1, path: 'chunks/chunk-001.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:chunk', startMs: 0, endMs: 2_000, overlapWithPreviousMs: 0, sha256: chunkSha256 }],
    drivers: [{ id: 'HAM', displayName: 'Lewis Hamilton', teamName: 'Ferrari', colorHex: '#E8002D', carNumber: '44' }],
    qualifyingTimeline: { path: 'qualifying-timeline.json', schemaId: schema, sha256: timelineSha256 },
    ...overrides,
  }
}

function minimalTrack(): Record<string, unknown> {
  const line = [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }, { x: 0, y: 1 }]
  return { contractVersion: 'v2', fixtureId, trackId: 'timeline-track', trackName: 'Timeline Track', coordinateSpace: { units: 'meters', origin: 'start' }, circuitLengthMeters: 1_000, rotationDegrees: 0, startFinish: { center: { x: 0, y: 0 }, inner: { x: 0, y: -1 }, outer: { x: 0, y: 1 } }, centerLine: line, innerBoundary: line, outerBoundary: line }
}

function minimalChunk(): Record<string, unknown> {
  const columns = { x: [0], y: [0], trackDistanceMeters: [0], speed: [1], throttle: [1], brake: [0], gapToLeaderMs: [0], lap: [1], position: [1], gear: [1], drs: [0], tyreCompound: ['SOFT'], status: ['running'], isInPitLane: [false] }
  return { contractVersion: 'v2', fixtureId, chunkId: 'chunk-001', sequence: 1, startMs: 0, endMs: 2_000, overlap: { kind: 'none', previousChunkPath: null, range: null, authoritativeFromMs: null }, timeMs: [0], authoritativeStartIndex: 0, drivers: { HAM: columns }, leaderboardOrder: [['HAM']], trackStatusCode: [1], weatherState: ['dry'], events: [] }
}

// ---------------------------------------------------------------------------
// lapKind — optional aligned lap classification in the lap-sector sidecar
// ---------------------------------------------------------------------------

describe('lapKind column', () => {
  test('parses a present aligned lapKind column and preserves every value', () => {
    const parsed = parseLapSectorSidecar(lapSectorSidecarPayload())

    expect(parsed.drivers.HAM.lapKind).toEqual(['flying', 'outlap', 'unknown'])
    expect(Object.isFrozen(parsed.drivers.HAM.lapKind)).toBe(true)
  })

  test('keeps an absent lapKind column absent instead of inventing flying laps', () => {
    const payload = lapSectorSidecarPayload()
    delete (payload.drivers as Record<string, Record<string, unknown>>).HAM.lapKind

    const parsed = parseLapSectorSidecar(payload)

    expect(parsed.drivers.HAM).not.toHaveProperty('lapKind')
  })

  test('rejects a misaligned lapKind array', () => {
    const payload = lapSectorSidecarPayload()
    ;(payload.drivers as Record<string, Record<string, unknown>>).HAM.lapKind = ['flying']

    expect(() => parseLapSectorSidecar(payload)).toThrow('not aligned')
  })

  test.each([
    ['out-of-enum value', ['flying', 'cooldown', 'unknown']],
    ['null element', ['flying', null, 'unknown']],
  ] as const)('rejects a lapKind array containing an %s', (_name, lapKind) => {
    const payload = lapSectorSidecarPayload()
    ;(payload.drivers as Record<string, Record<string, unknown>>).HAM.lapKind = lapKind

    expect(() => parseLapSectorSidecar(payload)).toThrow('must be flying, outlap, inlap, or unknown')
  })

  test('keeps the strict column schema: unknown extra columns are rejected', () => {
    const payload = lapSectorSidecarPayload()
    ;(payload.drivers as Record<string, Record<string, unknown>>).HAM.lapKindExtra = ['flying']

    expect(() => parseLapSectorSidecar(payload)).toThrow('not allowed')
  })
})

// ---------------------------------------------------------------------------
// qualifyingTimeline artifact parser
// ---------------------------------------------------------------------------

describe('qualifying timeline parser', () => {
  test('parses, orders, and deeply freezes intervals and incident markers', () => {
    const parsed = parseQualifyingTimeline(qualifyingTimelinePayload())

    expect(parsed.startMs).toBe(0)
    expect(parsed.endMs).toBe(2_000)
    expect(parsed.intervals.map(({ kind }) => kind)).toEqual(['yellow', 'red'])
    expect(parsed.incidentMarkers.map(({ timeMs }) => timeMs)).toEqual([700, 1_300])
    expect(parsed.incidentMarkers[0].lapNumber).toBe(2)
    expect(parsed.incidentMarkers[1]).not.toHaveProperty('lapNumber')
    expect(Object.isFrozen(parsed)).toBe(true)
    expect(Object.isFrozen(parsed.intervals)).toBe(true)
    expect(Object.isFrozen(parsed.incidentMarkers)).toBe(true)
  })

  test('accepts an artifact with empty interval and marker arrays', () => {
    const parsed = parseQualifyingTimeline(qualifyingTimelinePayload({ intervals: [], incidentMarkers: [] }))

    expect(parsed.intervals).toEqual([])
    expect(parsed.incidentMarkers).toEqual([])
  })

  test('does not expose race DNF or OUT semantics', () => {
    const parsed = parseQualifyingTimeline(qualifyingTimelinePayload())

    expect(parsed).not.toHaveProperty('dnfMarkers')
    expect(parsed.incidentMarkers[0]).not.toHaveProperty('isFinished')
  })

  test.each([
    ['wrong contract', { contractVersion: 'v1' }, 'contract version v2'],
    ['missing marker source', { incidentMarkers: [{ driverId: 'HAM', timeMs: 100, rawMessage: 'CAR 44 STOPS' }] }, 'required'],
    ['unsupported interval kind', { intervals: [{ kind: 'sc', startMs: 100, endMs: 200 }] }, 'kind is invalid'],
    ['interval before window start', { startMs: 100, intervals: [{ kind: 'yellow', startMs: 50, endMs: 200 }] }, 'outside timeline bounds'],
    ['interval after window end', { intervals: [{ kind: 'yellow', startMs: 100, endMs: 2_001 }] }, 'outside timeline bounds'],
    ['backwards interval', { intervals: [{ kind: 'yellow', startMs: 200, endMs: 100 }] }, 'outside timeline bounds'],
    ['marker at window end', { incidentMarkers: [{ driverId: 'HAM', timeMs: 2_000, source: 'race-control-car-event', rawMessage: 'CAR 44 STOPS' }] }, 'outside timeline bounds'],
    ['invalid marker driver', { incidentMarkers: [{ driverId: 'ham', timeMs: 100, source: 'race-control-car-event', rawMessage: 'CAR 44 STOPS' }] }, 'driverId is invalid'],
    ['unsupported marker source', { incidentMarkers: [{ driverId: 'HAM', timeMs: 100, source: 'guess', rawMessage: 'CAR 44 STOPS' }] }, 'source is unsupported'],
    ['blank raw message', { incidentMarkers: [{ driverId: 'HAM', timeMs: 100, source: 'race-control-car-event', rawMessage: '   ' }] }, 'non-blank'],
    ['zero lap number', { incidentMarkers: [{ driverId: 'HAM', timeMs: 100, source: 'race-control-car-event', rawMessage: 'CAR 44 STOPS', lapNumber: 0 }] }, 'integer from 1'],
  ] as const)('rejects %s', (_name, overrides, message) => {
    expect(() => parseQualifyingTimeline(qualifyingTimelinePayload(overrides))).toThrow(message)
  })

  test('rejects out-of-order incident markers', () => {
    expect(() => parseQualifyingTimeline(qualifyingTimelinePayload({
      incidentMarkers: [
        { driverId: 'HAM', timeMs: 1_300, source: 'race-control-car-event', rawMessage: 'CAR 44 CRASH' },
        { driverId: 'HAM', timeMs: 700, source: 'race-control-car-event', rawMessage: 'CAR 44 STOPS' },
      ],
    }))).toThrow('deterministically ordered')
  })

  test('rejects out-of-order intervals', () => {
    expect(() => parseQualifyingTimeline(qualifyingTimelinePayload({
      intervals: [
        { kind: 'red', startMs: 1_200, endMs: 1_500 },
        { kind: 'yellow', startMs: 500, endMs: 800 },
      ],
    }))).toThrow('deterministically ordered')
  })

  test('rejects an unexpected top-level field', () => {
    expect(() => parseQualifyingTimeline(qualifyingTimelinePayload({ extra: true }))).toThrow('not allowed')
  })
})

// ---------------------------------------------------------------------------
// manifest reference, mode gating, and absence semantics
// ---------------------------------------------------------------------------

describe('qualifying timeline manifest reference', () => {
  test('parses the qualifyingTimeline reference and schema registry entry', async () => {
    const digest = 'a'.repeat(64)
    const manifest = minimalManifest(digest, digest, digest)

    const parsed = parseManifest(manifest)

    expect(parsed.qualifyingTimeline).toEqual({ path: 'qualifying-timeline.json', schemaId: schema, sha256: digest })
    expect(Object.isFrozen(parsed.qualifyingTimeline)).toBe(true)
  })

  test('accepts an old v2 manifest without qualifyingTimeline and exposes no property', () => {
    const digest = 'a'.repeat(64)
    const manifest = minimalManifest(digest, digest, digest)
    delete manifest.qualifyingTimeline
    delete (manifest.schemas as Record<string, unknown>).qualifyingTimeline

    const parsed = parseManifest(manifest)

    expect(parsed).not.toHaveProperty('qualifyingTimeline')
  })

  test('rejects an unsafe or non-canonical reference', () => {
    const digest = 'a'.repeat(64)
    const wrongPath = minimalManifest(digest, digest, digest)
    wrongPath.qualifyingTimeline = { path: '../escape.json', schemaId: schema, sha256: digest }
    expect(() => parseManifest(wrongPath)).toThrow('path is unsafe')

    const wrongIdentity = minimalManifest(digest, digest, digest)
    wrongIdentity.qualifyingTimeline = { path: 'qualifying-timeline.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v1:qualifying-timeline', sha256: digest }
    expect(() => parseManifest(wrongIdentity)).toThrow('schema identity is unsupported')

    const wrongSchemaRegistry = minimalManifest(digest, digest, digest)
    ;(wrongSchemaRegistry.schemas as Record<string, unknown>).qualifyingTimeline = 'urn:unsupported'
    expect(() => parseManifest(wrongSchemaRegistry)).toThrow('identity is unsupported')
  })

  test('rejects a qualifyingTimeline reference for a non-qualifying mode', () => {
    const digest = 'a'.repeat(64)
    const raceManifest = minimalManifest(digest, digest, digest, { sessionMode: 'race' })

    expect(() => parseManifest(raceManifest)).toThrow('qualifying artifacts are valid only for qualifying-like modes')
  })
})

// ---------------------------------------------------------------------------
// loader integration
// ---------------------------------------------------------------------------

describe('qualifying timeline loader integration', () => {
  test('verifies digest, fixture identity, bounds, and published driver identity', async () => {
    const encoder = new TextEncoder()
    const timelineBytes = encoder.encode(JSON.stringify(qualifyingTimelinePayload()))
    const trackBytes = encoder.encode(JSON.stringify(minimalTrack()))
    const chunkBytes = encoder.encode(JSON.stringify(minimalChunk()))
    const manifest = minimalManifest(await sha256Hex(timelineBytes), await sha256Hex(trackBytes), await sha256Hex(chunkBytes))
    const files = new Map<string, Uint8Array>([
      ['manifest.json', encoder.encode(JSON.stringify(manifest))],
      ['track-assets.json', trackBytes],
      ['chunks/chunk-001.json', chunkBytes],
      ['qualifying-timeline.json', timelineBytes],
    ])
    const source: ReplaySource = { read: async (path) => files.get(path) ?? new Uint8Array() }

    const index = await loadReplayIndex({ source })

    expect(index.qualifyingTimeline?.fixtureId).toBe(fixtureId)
    expect(index.qualifyingTimeline?.intervals.map(({ kind }) => kind)).toEqual(['yellow', 'red'])
    expect(Object.isFrozen(index.qualifyingTimeline)).toBe(true)
  })

  test('rejects a corrupt qualifyingTimeline digest', async () => {
    const encoder = new TextEncoder()
    const timelineBytes = encoder.encode(JSON.stringify(qualifyingTimelinePayload()))
    const trackBytes = encoder.encode(JSON.stringify(minimalTrack()))
    const chunkBytes = encoder.encode(JSON.stringify(minimalChunk()))
    const manifest = minimalManifest(await sha256Hex(timelineBytes), await sha256Hex(trackBytes), await sha256Hex(chunkBytes))
    const files = new Map<string, Uint8Array>([
      ['manifest.json', encoder.encode(JSON.stringify(manifest))],
      ['track-assets.json', trackBytes],
      ['chunks/chunk-001.json', chunkBytes],
      ['qualifying-timeline.json', timelineBytes],
    ])

    const wrongBytes = encoder.encode(JSON.stringify(qualifyingTimelinePayload({ fixtureId: 'other-fixture' })))
    const corrupt = new Map(files)
    corrupt.set('qualifying-timeline.json', wrongBytes)
    await expect(loadReplayIndex({ source: { read: async (path) => corrupt.get(path) ?? new Uint8Array() } }))
      .rejects.toThrow('digest does not match')

    const identityManifest = { ...manifest, qualifyingTimeline: { ...(manifest.qualifyingTimeline as Record<string, unknown>), sha256: await sha256Hex(wrongBytes) } }
    corrupt.set('manifest.json', encoder.encode(JSON.stringify(identityManifest)))
    await expect(loadReplayIndex({ source: { read: async (path) => corrupt.get(path) ?? new Uint8Array() } }))
      .rejects.toThrow('fixture identities disagree')
  })

  test('rejects an incident marker referencing an unpublished driver', async () => {
    const encoder = new TextEncoder()
    const payload = qualifyingTimelinePayload({
      incidentMarkers: [{ driverId: 'VER', timeMs: 700, source: 'race-control-car-event', rawMessage: 'CAR 1 CRASH' }],
    })
    const timelineBytes = encoder.encode(JSON.stringify(payload))
    const trackBytes = encoder.encode(JSON.stringify(minimalTrack()))
    const chunkBytes = encoder.encode(JSON.stringify(minimalChunk()))
    const manifest = minimalManifest(await sha256Hex(timelineBytes), await sha256Hex(trackBytes), await sha256Hex(chunkBytes))
    const files = new Map<string, Uint8Array>([
      ['manifest.json', encoder.encode(JSON.stringify(manifest))],
      ['track-assets.json', trackBytes],
      ['chunks/chunk-001.json', chunkBytes],
      ['qualifying-timeline.json', timelineBytes],
    ])

    await expect(loadReplayIndex({ source: { read: async (path) => files.get(path) ?? new Uint8Array() } }))
      .rejects.toThrow('drivers disagree')
  })

  test('rejects a qualifyingTimeline whose window disagrees with the manifest bounds', async () => {
    const encoder = new TextEncoder()
    const timelineBytes = encoder.encode(JSON.stringify(qualifyingTimelinePayload({ startMs: 100, endMs: 1_900 })))
    const trackBytes = encoder.encode(JSON.stringify(minimalTrack()))
    const chunkBytes = encoder.encode(JSON.stringify(minimalChunk()))
    const manifest = minimalManifest(await sha256Hex(timelineBytes), await sha256Hex(trackBytes), await sha256Hex(chunkBytes))
    const files = new Map<string, Uint8Array>([
      ['manifest.json', encoder.encode(JSON.stringify(manifest))],
      ['track-assets.json', trackBytes],
      ['chunks/chunk-001.json', chunkBytes],
      ['qualifying-timeline.json', timelineBytes],
    ])

    await expect(loadReplayIndex({ source: { read: async (path) => files.get(path) ?? new Uint8Array() } }))
      .rejects.toThrow('bounds disagree with manifest')
  })

  test('loads without the artifact and never reads its file when the reference is absent', async () => {
    const encoder = new TextEncoder()
    const trackBytes = encoder.encode(JSON.stringify(minimalTrack()))
    const chunkBytes = encoder.encode(JSON.stringify(minimalChunk()))
    const manifest = minimalManifest('a'.repeat(64), await sha256Hex(trackBytes), await sha256Hex(chunkBytes))
    delete manifest.qualifyingTimeline
    delete (manifest.schemas as Record<string, unknown>).qualifyingTimeline
    const files = new Map<string, Uint8Array>([
      ['manifest.json', encoder.encode(JSON.stringify(manifest))],
      ['track-assets.json', trackBytes],
      ['chunks/chunk-001.json', chunkBytes],
    ])
    const reads: string[] = []
    const source: ReplaySource = { read: async (path) => { reads.push(path); return files.get(path) ?? new Uint8Array() } }

    const index = await loadReplayIndex({ source })

    expect(index).not.toHaveProperty('qualifyingTimeline')
    expect(reads).not.toContain('qualifying-timeline.json')
  })

  test('rejects a qualifyingTimeline reference for a race manifest at load time', async () => {
    const encoder = new TextEncoder()
    const timelineBytes = encoder.encode(JSON.stringify(qualifyingTimelinePayload()))
    const trackBytes = encoder.encode(JSON.stringify(minimalTrack()))
    const chunkBytes = encoder.encode(JSON.stringify(minimalChunk()))
    const manifest = minimalManifest(await sha256Hex(timelineBytes), await sha256Hex(trackBytes), await sha256Hex(chunkBytes), { sessionMode: 'race' })
    const files = new Map<string, Uint8Array>([
      ['manifest.json', encoder.encode(JSON.stringify(manifest))],
      ['track-assets.json', trackBytes],
      ['chunks/chunk-001.json', chunkBytes],
      ['qualifying-timeline.json', timelineBytes],
    ])

    await expect(loadReplayIndex({ source: { read: async (path) => files.get(path) ?? new Uint8Array() } }))
      .rejects.toThrow('qualifying artifacts are valid only for qualifying-like modes')
  })
})

// ---------------------------------------------------------------------------
// capabilities — qualifying-only exposure, race timeline unchanged
// ---------------------------------------------------------------------------

describe('qualifying timeline capabilities', () => {
  test('exposes the timeline only for qualifying-like modes with the artifact', () => {
    const timeline = { startMs: 0 } as never
    const qualifying = createSessionCapabilities('qualifying', { qualifyingTimeline: timeline })

    expect(qualifying.canShowQualifyingTimeline).toBe(true)
    expect(qualifying.canShowRaceTimeline).toBe(false)
  })

  test('keeps the capability unavailable when the artifact is absent', () => {
    const qualifying = createSessionCapabilities('qualifying')

    expect(qualifying.canShowQualifyingTimeline).toBe(false)
  })

  test('never exposes the qualifying timeline for race-like modes', () => {
    const race = createSessionCapabilities('race', { qualifyingTimeline: {} as never })

    expect(race.canShowQualifyingTimeline).toBe(false)
  })

  test('preserves race timelineSummary behavior unchanged', () => {
    const race = createSessionCapabilities('race', { timelineSummary: {} as never })

    expect(race.canShowRaceTimeline).toBe(true)
    expect(race.canShowQualifyingTimeline).toBe(false)
  })
})
