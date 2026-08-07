import { describe, expect, test } from 'vitest'
import { sha256Hex } from '../../../src/data/replay/digest'
import { parseManifest, parseQualifyingLapStatus } from '../../../src/data/replay/guards'
import { loadReplayIndex } from '../../../src/data/replay/loader'
import type { ReplaySource } from '../../../src/data/replay/types'

const schema = 'urn:f1-cache-replay:schema:replay-data:v2:browser-qualifying-lap-status'
const fixtureId = 'qualifying-status-fixture'

function sidecarPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    contractVersion: 'v2', fixtureId,
    drivers: {
      HAM: {
        lapNumber: [1, 2, 3], lapStartMs: [0, 100, 200], lapEndMs: [90, 190, 290],
        status: ['valid', 'valid', 'deleted'], deletedReason: [null, null, null],
      },
    },
    events: [
      { driverId: 'HAM', lapNumber: 2, eventTimeMs: 1000, status: 'deleted', reason: 'TRACK LIMITS', rawMessage: 'TIME 0:10.000 DELETED - TRACK LIMITS' },
      { driverId: 'HAM', lapNumber: 2, eventTimeMs: 1100, status: 'reinstated', reason: null, rawMessage: 'TIME 0:10.000 REINSTATED' },
      { driverId: 'HAM', lapNumber: 3, eventTimeMs: 1200, status: 'deleted', reason: null, rawMessage: 'TIME 0:10.000 DELETED' },
    ],
    ...overrides,
  }
}

describe('qualifying lap-status V2 parser', () => {
  test('parses, sorts, and deeply freezes causal records', () => {
    const parsed = parseQualifyingLapStatus(sidecarPayload())

    expect(parsed.events.map(({ eventTimeMs }) => eventTimeMs)).toEqual([1000, 1100, 1200])
    expect(parsed.drivers.HAM.status).toEqual(['valid', 'valid', 'deleted'])
    expect(Object.isFrozen(parsed)).toBe(true)
    expect(Object.isFrozen(parsed.drivers.HAM.lapNumber)).toBe(true)
  })

  test.each([
    ['wrong contract', { contractVersion: 'v1' }, 'contract version v2'],
    ['missing event fields', { events: [{ driverId: 'HAM' }] }, 'required'],
    ['misaligned columns', { drivers: { HAM: { lapNumber: [1, 2], lapStartMs: [0, 100], lapEndMs: [90, 190], status: ['valid'], deletedReason: [null, null] } } }, 'not aligned'],
    ['nullable reason with whitespace', { events: [{ driverId: 'HAM', lapNumber: 1, eventTimeMs: 100, status: 'deleted', reason: '   ', rawMessage: 'deleted' }] }, 'non-blank'],
  ] as const)('rejects %s', (_name, overrides, message) => {
    expect(() => parseQualifyingLapStatus(sidecarPayload(overrides))).toThrow(message)
  })

  test('rejects unknown driver or lap references and contradictory same-time statuses', () => {
    expect(() => parseQualifyingLapStatus(sidecarPayload({ events: [{ driverId: 'VER', lapNumber: 1, eventTimeMs: 100, status: 'deleted', reason: null, rawMessage: 'deleted' }] }))).toThrow('unknown lap')
    expect(() => parseQualifyingLapStatus(sidecarPayload({ events: [{ driverId: 'HAM', lapNumber: 9, eventTimeMs: 100, status: 'deleted', reason: null, rawMessage: 'deleted' }] }))).toThrow('unknown lap')
    expect(() => parseQualifyingLapStatus(sidecarPayload({ events: [
      { driverId: 'HAM', lapNumber: 1, eventTimeMs: 100, status: 'deleted', reason: null, rawMessage: 'deleted' },
      { driverId: 'HAM', lapNumber: 1, eventTimeMs: 100, status: 'reinstated', reason: null, rawMessage: 'reinstated' },
    ], drivers: { HAM: { lapNumber: [1], lapStartMs: [0], lapEndMs: [90], status: ['valid'], deletedReason: [null] } } }))).toThrow('contradictory')
  })

  test('rejects non-canonical and mixed-version manifest references', () => {
    const wrongPath = minimalManifest('a'.repeat(64), 'a'.repeat(64), 'a'.repeat(64))
    wrongPath.qualifyingLapStatus = { path: 'wrong.json', schemaId: schema, sha256: 'a'.repeat(64) }
    expect(() => parseManifest(wrongPath)).toThrow('path is unsupported')
    const wrongSchema = minimalManifest('a'.repeat(64), 'a'.repeat(64), 'a'.repeat(64))
    wrongSchema.qualifyingLapStatus = { path: 'qualifying-lap-status.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v1:browser-qualifying-lap-status', sha256: 'a'.repeat(64) }
    expect(() => parseManifest(wrongSchema)).toThrow('schema identity is unsupported')
  })
})

describe('qualifying lap-status loader integration', () => {
  test('verifies digest, fixture identity, and published driver identity before exposure', async () => {
    const encoder = new TextEncoder()
    const sidecarBytes = encoder.encode(JSON.stringify(sidecarPayload()))
    const trackBytes = encoder.encode(JSON.stringify(minimalTrack()))
    const chunkBytes = encoder.encode(JSON.stringify(minimalChunk()))
    const manifest = minimalManifest(await sha256Hex(sidecarBytes), await sha256Hex(trackBytes), await sha256Hex(chunkBytes))
    const files = new Map<string, Uint8Array>([
      ['manifest.json', encoder.encode(JSON.stringify(manifest))],
      ['track-assets.json', trackBytes], ['chunks/chunk-001.json', chunkBytes],
      ['qualifying-lap-status.json', sidecarBytes],
    ])
    const source: ReplaySource = { read: async (path) => files.get(path) ?? new Uint8Array() }

    const index = await loadReplayIndex({ source })

    expect(index.qualifyingLapStatus?.fixtureId).toBe(fixtureId)
    expect(Object.isFrozen(index.qualifyingLapStatus)).toBe(true)
    const corrupt = new Map(files)
    const wrongIdentityBytes = encoder.encode(JSON.stringify(sidecarPayload({ fixtureId: 'other-fixture' })))
    corrupt.set('qualifying-lap-status.json', wrongIdentityBytes)
    await expect(loadReplayIndex({ source: { read: async (path) => corrupt.get(path) ?? new Uint8Array() } })).rejects.toThrow('digest does not match')
    const identityManifest = { ...manifest, qualifyingLapStatus: { ...(manifest.qualifyingLapStatus as Record<string, unknown>), sha256: await sha256Hex(wrongIdentityBytes) } }
    corrupt.set('manifest.json', encoder.encode(JSON.stringify(identityManifest)))
    await expect(loadReplayIndex({ source: { read: async (path) => corrupt.get(path) ?? new Uint8Array() } })).rejects.toThrow('fixture identities disagree')
  })
})

function minimalManifest(sidecarSha256: string, trackSha256: string, chunkSha256: string): Record<string, unknown> {
  return {
    contractVersion: 'v2', formatVersion: 'browser-delivery-v2', sessionMode: 'qualifying', fixtureId,
    fixtureName: 'Qualifying Status Fixture',
    schemas: { manifest: 'urn:f1-cache-replay:schema:replay-data:v2:manifest', chunk: 'urn:f1-cache-replay:schema:replay-data:v2:chunk', trackAssets: 'urn:f1-cache-replay:schema:replay-data:v2:track-assets', qualifyingLapStatus: schema },
    trackAssets: { path: 'track-assets.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:track-assets', sha256: trackSha256 },
    chunks: [{ sequence: 1, path: 'chunks/chunk-001.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:chunk', startMs: 0, endMs: 2000, overlapWithPreviousMs: 0, sha256: chunkSha256 }],
    drivers: [{ id: 'HAM', displayName: 'Lewis Hamilton', teamName: 'Ferrari', colorHex: '#E8002D', carNumber: '44' }],
    qualifyingLapStatus: { path: 'qualifying-lap-status.json', schemaId: schema, sha256: sidecarSha256 },
  }
}

function minimalTrack(): Record<string, unknown> {
  const line = [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }, { x: 0, y: 1 }]
  return { contractVersion: 'v2', fixtureId, trackId: 'status-track', trackName: 'Status Track', coordinateSpace: { units: 'meters', origin: 'start' }, circuitLengthMeters: 1000, rotationDegrees: 0, startFinish: { center: { x: 0, y: 0 }, inner: { x: 0, y: -1 }, outer: { x: 0, y: 1 } }, centerLine: line, innerBoundary: line, outerBoundary: line }
}

function minimalChunk(): Record<string, unknown> {
  const columns = { x: [0], y: [0], trackDistanceMeters: [0], speed: [1], throttle: [1], brake: [0], gapToLeaderMs: [0], lap: [1], position: [1], gear: [1], drs: [0], tyreCompound: ['SOFT'], status: ['running'], isInPitLane: [false] }
  return { contractVersion: 'v2', fixtureId, chunkId: 'chunk-001', sequence: 1, startMs: 0, endMs: 2000, overlap: { kind: 'none', previousChunkPath: null, range: null, authoritativeFromMs: null }, timeMs: [0], authoritativeStartIndex: 0, drivers: { HAM: columns }, leaderboardOrder: [['HAM']], trackStatusCode: [1], weatherState: ['dry'], events: [] }
}
