import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'
import { loadReplayData } from '../src/replay-data/loader'
import type { ReplayChunk, ReplayData, ReplaySource } from '../src/replay-data/types'
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

  test('returns null rather than interpolate across a gap longer than one second', () => {
    const replay = syntheticReplay([0, null, null, 30], [0, 500, 1000, 1500])

    const snapshot = sampleReplayAt(replay, 750)

    expect(snapshot.drivers.HAM.x).toBeNull()
  })

  test('returns deeply immutable public snapshots', async () => {
    const replay = await loadReplayData({ source: fixtureSource })
    const snapshot = sampleReplayAt(replay, 2600)

    expect([Object.isFrozen(snapshot), Object.isFrozen(snapshot.drivers), Object.isFrozen(snapshot.events[0].payload)]).toEqual([true, true, true])
  })

  test('prepares valid-value indexes once for repeated window samples', () => {
    const prepared = prepareReplaySampler(syntheticReplay([0, null, null, 30]))

    const snapshots = [100, 150, 200].map((timeMs) => samplePreparedReplayAt(prepared, timeMs).drivers.HAM.x)

    expect(snapshots).toEqual([10, 15, 20])
  })
})

function syntheticReplay(x: readonly (number | null)[], timeMs = [0, 100, 200, 300]): ReplayData {
  const nulls = timeMs.map(() => null)
  const chunk: ReplayChunk = {
    contractVersion: 'v1', fixtureId: 'synthetic', chunkId: 'chunk-001', sequence: 1, startMs: 0, endMs: 2_000,
    overlap: { kind: 'none', previousChunkPath: null, range: null, authoritativeFromMs: null }, timeMs, authoritativeStartIndex: 0,
    drivers: { HAM: { x, y: nulls, trackDistanceMeters: nulls, speed: nulls, throttle: nulls, brake: nulls, gapToLeaderMs: nulls, lap: nulls, position: nulls, gear: nulls, drs: nulls, tyreCompound: nulls, status: nulls, isInPitLane: nulls } },
    leaderboardOrder: nulls, trackStatusCode: nulls, weatherState: nulls, events: [],
  }
  return { manifest: { contractVersion: 'v1', fixtureId: 'synthetic', fixtureName: 'Synthetic', schemas: { manifest: '', chunk: '', trackAssets: '' }, trackAssets: { path: '', schemaId: '' }, chunks: [], drivers: [{ id: 'HAM', displayName: 'Hamilton', teamName: 'Mercedes', colorHex: '#000000', carNumber: '44' }] }, trackAssets: {} as ReplayData['trackAssets'], chunks: [chunk] }
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
