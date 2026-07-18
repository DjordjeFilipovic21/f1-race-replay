import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'
import { loadReplayData } from '../src/replay-data/loader'
import type { DriverColumns, ReplayChunk, ReplayData, ReplaySource } from '../src/replay-data/types'
import { prepareReplaySampler, samplePreparedReplayAt, sampleReplayAt } from '../src/replay-engine/sampler'

const fixtureRoot = resolve(import.meta.dirname, '../../contracts/replay-data/v1/fixtures/deterministic-race')
const fixtureSource: ReplaySource = { read: (path) => readFile(resolve(fixtureRoot, path)) }

describe('replay-engine sampler', () => {
  test.each([
    1500, 1750, 2000, 2600,
  ])('matches fixture golden behavior at %ims', async (timeMs) => {
    const replay = await loadReplayData({ source: fixtureSource })
    const golden = await readGolden(timeMs)

    const snapshot = sampleReplayAt(replay, timeMs)

    expect({ drivers: snapshot.drivers, leaderboardOrder: snapshot.leaderboardOrder, trackStatusCode: snapshot.trackStatusCode, weatherState: snapshot.weatherState, events: snapshot.events }).toMatchObject({
      drivers: golden.drivers,
      leaderboardOrder: golden.leaderboardOrder,
      trackStatusCode: golden.trackStatusCode,
      weatherState: golden.weatherState,
      events: golden.events,
    })
  })

  test('uses the owning chunk at the handoff boundary and exposes only exact-time events', async () => {
    const replay = await loadReplayData({ source: fixtureSource })

    const boundary = sampleReplayAt(replay, 2000)
    const event = sampleReplayAt(replay, 2600)

    expect([boundary.drivers.HAM.speed, boundary.trackStatusCode, event.events.length]).toEqual([210, 4, 1])
  })

  test('skips null shared-timeline entries to use valid same-driver field bounds', () => {
    const replay = syntheticReplay([0, null, null, 30])

    const snapshot = sampleReplayAt(replay, 150)

    expect(snapshot.drivers.HAM.x).toBe(15)
  })

  test('bridges a bounded global position-telemetry gap', () => {
    const replay = syntheticReplay([0, null, null, 30], [0, 500, 1000, 1300])

    expect(sampleReplayAt(replay, 650).drivers.HAM.x).toBe(15)
  })

  test('returns null rather than interpolate coordinates across a gap longer than 1.5 seconds', () => {
    const replay = syntheticReplay([0, null, null, 30], [0, 500, 1000, 1600])

    const snapshot = sampleReplayAt(replay, 750)

    expect(snapshot.drivers.HAM.x).toBeNull()
  })

  test('returns deeply immutable public snapshots', async () => {
    const replay = await loadReplayData({ source: fixtureSource })
    const snapshot = sampleReplayAt(replay, 2600)

    expect([Object.isFrozen(snapshot), Object.isFrozen(snapshot.drivers), Object.isFrozen(snapshot.drivers.HAM), Object.isFrozen(snapshot.leaderboardOrder), Object.isFrozen(snapshot.events[0].payload)]).toEqual([true, true, true, true, true])
  })

  test('prepares valid-value indexes once for repeated window samples', () => {
    const prepared = prepareReplaySampler(syntheticReplay([0, null, null, 30]))

    const snapshots = [100, 150, 200].map((timeMs) => samplePreparedReplayAt(prepared, timeMs).drivers.HAM.x)

    expect(snapshots).toEqual([10, 15, 20])
  })

  test('offers a bounded smooth coordinate filter without changing the linear default', () => {
    const replay = syntheticReplay([0, 10, 0, 10, 0], [0, 250, 500, 750, 1_000])
    const linear = samplePreparedReplayAt(prepareReplaySampler(replay), 500)
    const filtered = samplePreparedReplayAt(prepareReplaySampler(replay, undefined, 'smooth'), 500)

    expect(linear.drivers.HAM.x).toBe(0)
    expect(filtered.drivers.HAM.x).not.toBe(0)
    expect(filtered.drivers.HAM.x).toBeGreaterThanOrEqual(-10)
    expect(filtered.drivers.HAM.x).toBeLessThanOrEqual(10)
  })

  test('does not let the smooth filter borrow evidence across a long telemetry gap', () => {
    const replay = syntheticReplay([0, 10, null, null, 100, 110], [0, 250, 1_000, 1_500, 2_000, 2_250])
    const filtered = prepareReplaySampler(replay, undefined, 'smooth')

    expect(samplePreparedReplayAt(filtered, 1_125).drivers.HAM.x).toBeNull()
  })

  test.each([
    ['pit lane', { isInPitLane: [false, false, true, false, false] }],
    ['off track', { status: ['OnTrack', 'OnTrack', 'OffTrack', 'OnTrack', 'OnTrack'] }],
  ])('does not move an exact source coordinate while the driver is %s', (_label, overrides) => {
    const replay = syntheticReplay([0, 10, 0, 10, 0], [0, 250, 500, 750, 1_000], overrides as Partial<DriverColumns>)
    const filtered = prepareReplaySampler(replay, undefined, 'smooth')

    expect(samplePreparedReplayAt(filtered, 500).drivers.HAM.x).toBe(0)
  })

  test('samples populated derived distance and gap fields at exact and interpolated times', () => {
    const replay = derivedReplay({ trackDistanceMeters: [100, 200], gapToLeaderMs: [0, 20] })

    const exact = sampleReplayAt(replay, 0)
    const interpolated = sampleReplayAt(replay, 500)

    expect([exact.drivers.HAM.trackDistanceMeters, exact.drivers.HAM.gapToLeaderMs]).toEqual([100, 0])
    expect([interpolated.drivers.HAM.trackDistanceMeters, interpolated.drivers.HAM.gapToLeaderMs]).toEqual([150, 0])
  })

  test('interpolates circuit distance forward through the centerline origin and preserves exact origin samples', () => {
    const replay = derivedReplay({ trackDistanceMeters: [950, 50] }, 1_000)

    expect(sampleReplayAt(replay, 500).drivers.HAM.trackDistanceMeters).toBe(0)
    expect(sampleReplayAt(replay, 1_000).drivers.HAM.trackDistanceMeters).toBe(50)
  })

  test('returns null for a non-approved large backward circuit-distance bound', () => {
    const replay = derivedReplay({ trackDistanceMeters: [800, 0] }, 1_000)

    expect(sampleReplayAt(replay, 500).drivers.HAM.trackDistanceMeters).toBeNull()
  })

  test('keeps previous position and order aligned while forcing the sampled leader gap to zero', async () => {
    const replay = await loadReplayData({ source: fixtureSource })

    const beforeOrderChange = sampleReplayAt(replay, 2_600)
    const afterOrderChange = sampleReplayAt(replay, 3_000)

    expect([beforeOrderChange.leaderboardOrder, beforeOrderChange.drivers.HAM.position, beforeOrderChange.drivers.RUS.position, beforeOrderChange.drivers.HAM.gapToLeaderMs]).toEqual([['HAM', 'RUS'], 1, 2, 0])
    expect([afterOrderChange.leaderboardOrder, afterOrderChange.drivers.HAM.position, afterOrderChange.drivers.RUS.position, afterOrderChange.drivers.RUS.gapToLeaderMs]).toEqual([['RUS', 'HAM'], 2, 1, 0])
  })

  test('preserves null-only derived fields without deriving a leader gap from legacy order data', () => {
    const replay = syntheticReplay([0, 10])

    const snapshot = samplePreparedReplayAt(prepareReplaySampler(replay), 50)

    expect([snapshot.drivers.HAM.trackDistanceMeters, snapshot.drivers.HAM.gapToLeaderMs, snapshot.drivers.HAM.position, snapshot.leaderboardOrder]).toEqual([null, null, null, null])
  })
})

function syntheticReplay(x: readonly (number | null)[], timeMs = [0, 100, 200, 300], overrides: Partial<DriverColumns> = {}): ReplayData {
  const nulls = timeMs.map(() => null)
  const driver: DriverColumns = { x, y: nulls, trackDistanceMeters: nulls, speed: nulls, throttle: nulls, brake: nulls, gapToLeaderMs: nulls, lap: nulls, position: nulls, gear: nulls, drs: nulls, tyreCompound: nulls, status: nulls, isInPitLane: nulls, ...overrides }
  const chunk: ReplayChunk = {
    contractVersion: 'v1', fixtureId: 'synthetic', chunkId: 'chunk-001', sequence: 1, startMs: 0, endMs: 2_000,
    overlap: { kind: 'none', previousChunkPath: null, range: null, authoritativeFromMs: null }, timeMs, authoritativeStartIndex: 0,
    drivers: { HAM: driver },
    leaderboardOrder: nulls, trackStatusCode: nulls, weatherState: nulls, events: [],
  }
  return { manifest: { contractVersion: 'v1', fixtureId: 'synthetic', fixtureName: 'Synthetic', schemas: { manifest: '', chunk: '', trackAssets: '' }, trackAssets: { path: '', schemaId: '' }, chunks: [], drivers: [{ id: 'HAM', displayName: 'Hamilton', teamName: 'Mercedes', colorHex: '#000000', carNumber: '44' }] }, trackAssets: {} as ReplayData['trackAssets'], chunks: [chunk] }
}

function derivedReplay(overrides: Partial<DriverColumns>, circuitLengthMeters = 1_000): ReplayData {
  const timeMs = [0, 1_000]
  const nulls = [null, null]
  const driver: DriverColumns = {
    x: [0, 10], y: nulls, trackDistanceMeters: nulls, speed: nulls, throttle: nulls, brake: nulls, gapToLeaderMs: nulls,
    lap: [1, 1], position: [1, 1], gear: nulls, drs: nulls, tyreCompound: nulls, status: nulls, isInPitLane: nulls,
    ...overrides,
  }
  const chunk: ReplayChunk = {
    contractVersion: 'v1', fixtureId: 'derived', chunkId: 'chunk-001', sequence: 1, startMs: 0, endMs: 2_000,
    overlap: { kind: 'none', previousChunkPath: null, range: null, authoritativeFromMs: null }, timeMs, authoritativeStartIndex: 0,
    drivers: { HAM: driver }, leaderboardOrder: [['HAM'], ['HAM']], trackStatusCode: nulls, weatherState: nulls, events: [],
  }
  return {
    manifest: { contractVersion: 'v1', fixtureId: 'derived', fixtureName: 'Derived', schemas: { manifest: '', chunk: '', trackAssets: '' }, trackAssets: { path: '', schemaId: '' }, chunks: [], drivers: [{ id: 'HAM', displayName: 'Hamilton', teamName: 'Mercedes', colorHex: '#000000', carNumber: '44' }] },
    trackAssets: { circuitLengthMeters } as ReplayData['trackAssets'], chunks: [chunk],
  }
}

async function readGolden(sessionTimeMs: number): Promise<GoldenSnapshot> {
  const path = resolve(fixtureRoot, 'golden-snapshots.json')
  const golden = JSON.parse(await readFile(path, 'utf8')) as { readonly snapshots: readonly GoldenSnapshot[] }
  const snapshot = golden.snapshots.find((candidate) => candidate.sessionTimeMs === sessionTimeMs)
  if (!snapshot) throw new Error(`Missing golden snapshot for ${sessionTimeMs}ms`)
  return snapshot
}

interface GoldenSnapshot {
  readonly sessionTimeMs: number
  readonly drivers: Readonly<Record<string, Partial<Record<string, string | number | boolean | null>>>>
  readonly leaderboardOrder: readonly string[] | null
  readonly trackStatusCode: number | null
  readonly weatherState: string | null
  readonly events: readonly unknown[]
}
