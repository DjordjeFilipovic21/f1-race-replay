import { describe, expect, test } from 'vitest'
import { sha256Hex } from '../../../src/data/replay/digest'
import { parseManifest, parsePointer } from '../../../src/data/replay/guards'
import { loadReplayData } from '../../../src/data/replay/loader'
import type { ReplaySource } from '../../../src/data/replay/types'

const ids = {
  manifest: 'urn:f1-cache-replay:schema:replay-data:v2:manifest',
  chunk: 'urn:f1-cache-replay:schema:replay-data:v2:chunk',
  track: 'urn:f1-cache-replay:schema:replay-data:v2:track-assets',
  qualifying: 'urn:f1-cache-replay:schema:replay-data:v2:qualifying-summary',
}

function track(fixtureId: string): Record<string, unknown> {
  const line = [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 2, y: 1 }, { x: 0, y: 1 }]
  return {
    contractVersion: 'v2', fixtureId, trackId: 'test-track', trackName: 'Test Track',
    coordinateSpace: { units: 'meters', origin: 'start-finish' }, circuitLengthMeters: 1000,
    rotationDegrees: 0, startFinish: { center: { x: 0, y: 0 }, inner: { x: 0, y: -1 }, outer: { x: 0, y: 1 } },
    centerLine: line, innerBoundary: line, outerBoundary: line,
  }
}

function chunk(fixtureId: string): Record<string, unknown> {
  const columns = {
    x: [0, 1], y: [0, 1], trackDistanceMeters: [0, 500], speed: [100, 110],
    throttle: [50, 60], brake: [0, 0], gapToLeaderMs: [0, 0], lap: [1, 1],
    position: [1, 1], gear: [3, 4], drs: [0, 0], tyreCompound: ['SOFT', 'SOFT'],
    status: ['running', 'running'], isInPitLane: [false, false],
  }
  return {
    contractVersion: 'v2', fixtureId, chunkId: 'chunk-001', sequence: 1, startMs: 0, endMs: 2000,
    overlap: { kind: 'none', previousChunkPath: null, range: null, authoritativeFromMs: null },
    timeMs: [0, 1000], authoritativeStartIndex: 0, drivers: { HAM: columns },
    leaderboardOrder: [['HAM'], ['HAM']], trackStatusCode: [1, 1], weatherState: ['dry', 'dry'], events: [],
  }
}

function manifest(sessionMode: string, fixtureId = 'test-session'): Record<string, unknown> {
  return {
    contractVersion: 'v2', formatVersion: 'browser-delivery-v2', sessionMode, fixtureId,
    fixtureName: 'Test Session', schemas: { manifest: ids.manifest, chunk: ids.chunk, trackAssets: ids.track },
    trackAssets: { path: 'track-assets.json', schemaId: ids.track },
    chunks: [{ sequence: 1, path: 'chunks/chunk-001.json', schemaId: ids.chunk, startMs: 0, endMs: 2000, overlapWithPreviousMs: 0 }],
    drivers: [{ id: 'HAM', displayName: 'Lewis Hamilton', teamName: 'Ferrari', colorHex: '#E8002D', carNumber: '44' }],
  }
}

function qualifyingManifest(): Record<string, unknown> {
  const value = manifest('qualifying', 'qualifying-session')
  value.schemas = { ...(value.schemas as Record<string, string>), qualifyingSummary: ids.qualifying }
  value.qualifyingSummary = { path: 'qualifying-summary.json', schemaId: ids.qualifying, sha256: 'a'.repeat(64) }
  return value
}

describe('browser delivery v2 guards', () => {
  test.each(['race', 'practice', 'qualifying'])('accepts a valid %s manifest', (sessionMode) => {
    const value = sessionMode === 'qualifying' ? qualifyingManifest() : manifest(sessionMode)
    expect(parseManifest(value).sessionMode).toBe(sessionMode)
  })

  test('rejects v1 pointers and mixed-version manifest identities', () => {
    expect(() => parsePointer({ formatVersion: 'browser-delivery-v1', deliveryVersion: 'demo', manifestPath: 'generations/demo/manifest.json', manifestSha256: 'a'.repeat(64) })).toThrow()
    expect(() => parseManifest({ ...manifest('race'), contractVersion: 'v1' })).toThrow()
    expect(() => parseManifest({ ...manifest('race'), schemas: { manifest: ids.manifest, chunk: 'urn:f1-cache-replay:schema:replay-data:v1:chunk', trackAssets: ids.track } })).toThrow()
  })

  test('rejects unsafe references and invalid mode sidecars', () => {
    const unsafe = manifest('race')
    unsafe.trackAssets = { path: '../track-assets.json', schemaId: ids.track }
    expect(() => parseManifest(unsafe)).toThrow('unsafe')
    const practice = manifest('practice')
    practice.schemas = { ...(practice.schemas as Record<string, string>), pitLossModel: 'urn:f1-cache-replay:schema:replay-data:v2:pit-loss-model' }
    practice.pitLossModel = { path: 'pit-loss-model.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:pit-loss-model', sha256: 'a'.repeat(64) }
    expect(() => parseManifest(practice)).toThrow('session mode')
  })
})

describe('browser delivery v2 loader', () => {
  test('loads an immutable qualifying bundle and verifies artifact identities', async () => {
    const encoder = new TextEncoder()
    const fixtureId = 'qualifying-session'
    const trackBytes = encoder.encode(JSON.stringify(track(fixtureId)))
    const chunkBytes = encoder.encode(JSON.stringify(chunk(fixtureId)))
    const qualifyingSummaryBytes = encoder.encode(JSON.stringify({
      contractVersion: 'v2', fixtureId, drivers: {
        HAM: { qualifyingPosition: [1], q1TimeMs: [1000], q2TimeMs: [900], q3TimeMs: [800], bestLapNumber: [1], bestLapTimeMs: [800] },
      },
    }))
    const value = qualifyingManifest()
    value.trackAssets = { path: 'track-assets.json', schemaId: ids.track, sha256: await sha256Hex(trackBytes) }
    ;(value.chunks as Array<Record<string, unknown>>)[0].sha256 = await sha256Hex(chunkBytes)
    ;(value.qualifyingSummary as Record<string, unknown>).sha256 = await sha256Hex(qualifyingSummaryBytes)
    const manifestBytes = encoder.encode(JSON.stringify(value))
    const files = new Map([
      ['manifest.json', manifestBytes], ['track-assets.json', trackBytes], ['chunks/chunk-001.json', chunkBytes], ['qualifying-summary.json', qualifyingSummaryBytes],
    ])
    const source: ReplaySource = { read: async (path) => files.get(path) ?? new Uint8Array() }

    const replay = await loadReplayData({ source })

    expect(replay.manifest.sessionMode).toBe('qualifying')
    expect(replay.chunks[0].contractVersion).toBe('v2')
    expect(Object.isFrozen(replay.chunks[0].drivers.HAM.x)).toBe(true)
  })
})
