import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { describe, expect, test, vi } from 'vitest'
import { loadReplayData, loadReplayIndex } from '../../src/data/replay/loader'
import {
  BROWSER_LAP_SECTOR_SIDECAR_SCHEMA, QUALIFYING_LAP_STATUS_SCHEMA, QUALIFYING_SUMMARY_SCHEMA,
} from '../../src/data/replay/guards'
import type {
  LapSectorSidecar, QualifyingLapStatusSidecar, QualifyingPhaseBoundary, QualifyingSummary,
  ReplayChunk, ReplayIndex, ReplayManifest, ReplaySource,
} from '../../src/data/replay/types'
import { createReplayController } from '../../src/engine/replay/controller'
import type { PlaybackScheduler } from '../../src/engine/replay/clock'
import { sampleReplayAt } from '../../src/engine/replay/sampler'
import { selectQualifyingLiveState } from '../../src/features/replay/selectors/qualifying-live-state-selectors'
import { selectCurrentQualifyingPhase, selectQualifyingPhaseBoundary } from '../../src/features/replay/playback/PlaybackControls'

const fixtureRoot = resolve(import.meta.dirname, '../../../contracts/replay-data/v2/fixtures/deterministic-race')

describe('replay engine integration', () => {
  test('lazily loads, samples, advances, hands off, and keeps sparse event views distinct', async () => {
    // Arrange - use the unchanged delivery fixture through the real lazy index loader.
    const reads: string[] = []
    const source: ReplaySource = { read: async (path) => { reads.push(path); return readFile(resolve(fixtureRoot, path)) } }
    const loaded = await loadReplayIndex({ source })
    const loadChunk = vi.fn((sequence: number) => loaded.loadChunk(sequence))
    const loadAllChunks = vi.fn(loaded.loadAllChunks)
    const index = Object.freeze({ ...loaded, loadChunk, loadAllChunks }) satisfies ReplayIndex
    const scheduler = createScheduler()
    const controller = createReplayController({ index, scheduler })

    // Act - wait for the initial window, then cross into and play within the handoff chunk.
    await waitForReady(controller)
    const initial = controller.getSnapshot()
    controller.start()
    scheduler.fire(1_000)
    scheduler.fire(2_000)
    await waitForReady(controller)
    scheduler.fire(2_700)
    const crossed = controller.getSnapshot()
    controller.pause()
    controller.seek(2_600)
    const exact = controller.getSnapshot()
    controller.seek(2_000)
    const handoff = controller.getSnapshot()

    // Assert - only the bounded working set was requested; ownership and event semantics remain public and stable.
    expect(reads.slice(0, 2)).toEqual(['manifest.json', 'track-assets.json'])
    expect(loadAllChunks).not.toHaveBeenCalled()
    expect(loadChunk.mock.calls.map(([sequence]) => sequence)).toEqual([1, 2])
    expect([initial.status, initial.replay?.sessionTimeMs, initial.replay?.drivers.HAM.speed]).toEqual(['ready', 0, 210])
    expect(Object.is(initial, controller.getSnapshot())).toBe(false)
    expect([Object.isFrozen(initial), Object.isFrozen(initial.replay), Object.isFrozen(initial.replay?.drivers)]).toEqual([true, true, true])
    expect([crossed.timeMs, crossed.replay?.events, crossed.crossedEvents.map(({ sessionTimeMs }) => sessionTimeMs)]).toEqual([2_700, [], [2_600]])
    expect([exact.replay?.events.map(({ sessionTimeMs }) => sessionTimeMs), exact.crossedEvents]).toEqual([[2_600], []])
    expect([handoff.replay?.sessionTimeMs, handoff.replay?.drivers.HAM.speed, handoff.replay?.trackStatusCode, handoff.crossedEvents]).toEqual([2_000, 210, 4, []])
  })

  test('does not publish or schedule work after disposal', async () => {
    // Arrange - create a ready controller with a deterministic scheduler and observer.
    const source: ReplaySource = { read: (path) => readFile(resolve(fixtureRoot, path)) }
    const scheduler = createScheduler()
    const controller = createReplayController({ index: await loadReplayIndex({ source }), scheduler })
    await waitForReady(controller)
    let publications = 0
    const unsubscribe = controller.subscribe(() => { publications += 1 })
    controller.start()
    publications = 0
    const beforeDispose = controller.getSnapshot()

    // Act - dispose, then attempt every externally available state-changing operation.
    controller.dispose()
    controller.dispose()
    controller.start()
    controller.pause()
    controller.seek(2_000)
    controller.setSpeed(2)
    scheduler.fire(1_000)
    unsubscribe()

    // Assert - disposal cancels the frame once and makes later operations observationally inert.
    expect(scheduler.cancelled).toEqual([1])
    expect(publications).toBe(0)
    expect(controller.getSnapshot()).toBe(beforeDispose)
  })

  test('direct sampling, seek, and playback publish equal derived snapshots at one integer timestamp', async () => {
    const source: ReplaySource = { read: (path) => readFile(resolve(fixtureRoot, path)) }
    const replay = await loadReplayData({ source })
    const scheduler = createScheduler()
    const controller = createReplayController({ index: await loadReplayIndex({ source }), scheduler })
    await waitForReady(controller)
    const direct = sampleReplayAt(replay, 2_600)

    controller.seek(2_600)
    await waitForReady(controller)
    const sought = controller.getSnapshot().replay
    controller.seek(0)
    await waitForReady(controller)
    controller.setSpeed(4)
    controller.start()
    scheduler.fire(650)
    await waitForReady(controller)
    const played = controller.getSnapshot().replay

    expect([sought, played]).toEqual([direct, direct])
  })

  test('publishes the exact replay cursor selected by a qualifying phase boundary', async () => {
    const source: ReplaySource = { read: (path) => readFile(resolve(fixtureRoot, path)) }
    const controller = createReplayController({ index: await loadReplayIndex({ source }), scheduler: createScheduler() })
    await waitForReady(controller)

    controller.seek(2_000)
    await waitForReady(controller)

    expect(controller.getSnapshot().timeMs).toBe(2_000)
    expect(controller.getSnapshot().replay?.sessionTimeMs).toBe(2_000)
  })

  test('loads, seeks, and plays a legacy null-only derived artifact without throwing or deriving values', async () => {
    const scheduler = createScheduler()
    const controller = createReplayController({ index: legacyIndex(), scheduler })
    await waitForReady(controller)

    controller.seek(500)
    controller.start()
    scheduler.fire(0)
    scheduler.fire(125)
    const replay = controller.getSnapshot().replay

    expect([replay?.drivers.HAM.trackDistanceMeters, replay?.drivers.HAM.gapToLeaderMs, replay?.drivers.HAM.position]).toEqual([null, null, null])
  })

  test('qualifying phase navigation only returns delivered boundaries and cannot fabricate timing', () => {
    // Arrange - delivered Q boundaries at fixed session times.
    const boundaries: readonly QualifyingPhaseBoundary[] = [
      { phase: 'Q1', startMs: 0 },
      { phase: 'Q2', startMs: 2_000 },
      { phase: 'Q3', startMs: 4_000 },
    ]

    // Act / Assert - the current phase is causal to the boundary starts.
    expect(selectCurrentQualifyingPhase(boundaries, 0)).toBe('Q1')
    expect(selectCurrentQualifyingPhase(boundaries, 1_999)).toBe('Q1')
    expect(selectCurrentQualifyingPhase(boundaries, 2_000)).toBe('Q2')
    expect(selectCurrentQualifyingPhase(boundaries, 4_000)).toBe('Q3')

    // Assert - navigation cannot fabricate a boundary outside the delivered set.
    expect(selectQualifyingPhaseBoundary(boundaries, 'Q1', -1)).toBeUndefined()
    expect(selectQualifyingPhaseBoundary(boundaries, 'Q3', 1)).toBeUndefined()
    expect(selectQualifyingPhaseBoundary(boundaries, null, 1)).toEqual({ phase: 'Q1', startMs: 0 })
    expect(selectQualifyingPhaseBoundary(boundaries, 'Q2', -1)).toEqual({ phase: 'Q1', startMs: 0 })
    expect(selectQualifyingPhaseBoundary(boundaries, 'Q2', 1)).toEqual({ phase: 'Q3', startMs: 4_000 })
    expect(selectQualifyingPhaseBoundary(undefined, 'Q1', 1)).toBeUndefined()
    expect(selectQualifyingPhaseBoundary([], 'Q1', 1)).toBeUndefined()
    expect(selectCurrentQualifyingPhase([], 3_000)).toBeNull()
  })

  test('engine sampling and selectors exclude a deleted lap from the phase-scoped fastest time', async () => {
    // Arrange - a qualifying index with a deleted Q2 lap, a valid Q2 lap, and a
    // Q1 lap so phase-scoped fastest time must ignore the deleted lap.
    const index = qualifyingIndex()
    const scheduler = createScheduler()
    const controller = createReplayController({ index, scheduler })
    await waitForReady(controller)

    // Act - sample the replay at a cursor inside Q2 after both Q2 laps complete.
    controller.seek(5_000)
    await waitForReady(controller)
    const sampled = controller.getSnapshot().replay
    const state = selectQualifyingLiveState(
      sampled, 'HAM', index.qualifyingSummary, index.lapSectorSidecar, index.qualifyingLapStatus,
    )

    // Assert - the deleted Q2 lap cannot become the fastest time; the valid
    // 1_600 ms Q2 lap wins and the Q1 lap is not counted inside Q2.
    expect(state.activeQualifyingPhase).toBe('Q2')
    expect(state.fastestCausalLapDurationMs).toBe(1_600)
    expect(state.currentLapEvidence?.status).toBe('valid')
    expect(state.causalLapEvidence.find((lap) => lap.lapNumber === 2)?.status).toBe('deleted')
  })
})

function legacyIndex(): ReplayIndex {
  const chunk: ReplayChunk = {
    contractVersion: 'v2', fixtureId: 'legacy', chunkId: 'chunk-001', sequence: 1, startMs: 0, endMs: 1_000,
    overlap: { kind: 'none', previousChunkPath: null, range: null, authoritativeFromMs: null }, timeMs: [0, 1_000], authoritativeStartIndex: 0,
    drivers: { HAM: { x: [0, 1], y: [0, 1], trackDistanceMeters: [null, null], speed: [null, null], throttle: [null, null], brake: [null, null], gapToLeaderMs: [null, null], lap: [1, 1], position: [null, null], gear: [null, null], drs: [null, null], tyreCompound: [null, null], status: [null, null], isInPitLane: [null, null] } },
    leaderboardOrder: [['HAM'], ['HAM']], trackStatusCode: [null, null], weatherState: [null, null], events: [],
  }
  const manifest = { contractVersion: 'v2' as const, formatVersion: 'browser-delivery-v2' as const, sessionMode: 'race' as const, fixtureId: 'legacy', fixtureName: 'Legacy', schemas: { manifest: 'urn:f1-cache-replay:schema:replay-data:v2:manifest', chunk: 'urn:f1-cache-replay:schema:replay-data:v2:chunk', trackAssets: 'urn:f1-cache-replay:schema:replay-data:v2:track-assets' }, trackAssets: { path: 'track-assets.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:track-assets' }, chunks: [{ sequence: 1, path: 'chunks/chunk-001.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:chunk', startMs: 0, endMs: 1_000, overlapWithPreviousMs: 0 }], drivers: [{ id: 'HAM', displayName: 'Hamilton', teamName: 'Mercedes', colorHex: '#000000', carNumber: '44' }] }
  return Object.freeze({ manifest, trackAssets: { circuitLengthMeters: 1_000 } as ReplayIndex['trackAssets'], loadChunk: async () => chunk, loadAllChunks: async () => [chunk] })
}

function qualifyingIndex(): ReplayIndex {
  // One-driver qualifying fixture: Q1 valid lap, Q2 deleted lap, Q2 valid lap.
  const chunk: ReplayChunk = {
    contractVersion: 'v2', fixtureId: 'qualifying-fixture', chunkId: 'chunk-001', sequence: 1, startMs: 0, endMs: 6_000,
    overlap: { kind: 'none', previousChunkPath: null, range: null, authoritativeFromMs: null },
    timeMs: [0, 1_000, 2_000, 2_500, 3_000, 4_000, 5_000], authoritativeStartIndex: 0,
    drivers: { HAM: { x: [null, null, null, null, null, null, null], y: [null, null, null, null, null, null, null], trackDistanceMeters: [null, null, null, null, null, null, null], speed: [null, null, null, null, null, null, null], throttle: [null, null, null, null, null, null, null], brake: [null, null, null, null, null, null, null], gapToLeaderMs: [null, null, null, null, null, null, null], lap: [1, 1, 2, 2, 3, 3, 3], position: [1, 1, 1, 1, 1, 1, 1], gear: [null, null, null, null, null, null, null], drs: [null, null, null, null, null, null, null], tyreCompound: ['SOFT', 'SOFT', 'SOFT', 'SOFT', 'SOFT', 'SOFT', 'SOFT'], status: ['OnTrack', 'OnTrack', 'OnTrack', 'OnTrack', 'OnTrack', 'OnTrack', 'OnTrack'], isInPitLane: [false, false, false, false, false, false, false] } },
    leaderboardOrder: [['HAM'], ['HAM'], ['HAM'], ['HAM'], ['HAM'], ['HAM'], ['HAM']], trackStatusCode: [null, null, null, null, null, null, null], weatherState: [null, null, null, null, null, null, null], events: [],
  }
  const manifest: ReplayManifest = {
    contractVersion: 'v2', formatVersion: 'browser-delivery-v2', sessionMode: 'qualifying', fixtureId: 'qualifying-fixture', fixtureName: 'Qualifying Fixture',
    schemas: { manifest: 'urn:f1-cache-replay:schema:replay-data:v2:manifest', chunk: 'urn:f1-cache-replay:schema:replay-data:v2:chunk', trackAssets: 'urn:f1-cache-replay:schema:replay-data:v2:track-assets' },
    trackAssets: { path: 'track-assets.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:track-assets' },
    chunks: [{ sequence: 1, path: 'chunks/chunk-001.json', schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:chunk', startMs: 0, endMs: 6_000, overlapWithPreviousMs: 0 }],
    drivers: [{ id: 'HAM', displayName: 'Hamilton', teamName: 'Mercedes', colorHex: '#000000', carNumber: '44' }],
    lapSectorSidecar: { path: 'lap-sector-sidecar.json', schemaId: BROWSER_LAP_SECTOR_SIDECAR_SCHEMA, sha256: 'a'.repeat(64) },
    qualifyingSummary: { path: 'qualifying-summary.json', schemaId: QUALIFYING_SUMMARY_SCHEMA, sha256: 'a'.repeat(64) },
    qualifyingLapStatus: { path: 'qualifying-lap-status.json', schemaId: QUALIFYING_LAP_STATUS_SCHEMA, sha256: 'a'.repeat(64) },
  }
  const lapSectorSidecar: LapSectorSidecar = {
    contractVersion: 'v2', fixtureId: 'qualifying-fixture', phaseBoundaries: [
      { phase: 'Q1', startMs: 0 },
      { phase: 'Q2', startMs: 2_000 },
    ],
    drivers: {
      HAM: {
        lapNumber: [1, 2, 3], lapStartMs: [0, 2_000, 3_000], lapEndMs: [1_000, 3_000, 4_600],
        lapDurationMs: [1_500, 1_500, 1_600],
        sector1DurationMs: [500, 500, 533], sector2DurationMs: [500, 500, 533], sector3DurationMs: [500, 500, 534],
        sector1SessionTimeMs: [500, 2_500, 3_500], sector2SessionTimeMs: [1_000, 3_000, 4_000], sector3SessionTimeMs: [1_000, 3_000, 4_600],
        qualifyingPhase: ['Q1', 'Q2', 'Q2'], lapKind: ['flying', 'flying', 'flying'],
      },
    },
  }
  const qualifyingLapStatus: QualifyingLapStatusSidecar = {
    contractVersion: 'v2', fixtureId: 'qualifying-fixture',
    drivers: { HAM: { lapNumber: [1, 2, 3], lapStartMs: [0, 2_000, 3_000], lapEndMs: [1_000, 3_000, 4_600], status: ['valid', 'deleted', 'valid'], deletedReason: [null, 'TRACK LIMITS', null] } },
    events: [{ driverId: 'HAM', lapNumber: 2, eventTimeMs: 2_400, status: 'deleted', reason: 'TRACK LIMITS', rawMessage: 'CAR 44 TIME 1:30.000 DELETED - TRACK LIMITS' }],
  }
  const qualifyingSummary: QualifyingSummary = {
    contractVersion: 'v2', fixtureId: 'qualifying-fixture',
    drivers: { HAM: { qualifyingPosition: [1], q1TimeMs: [1_500], q2TimeMs: [1_600], q3TimeMs: [null], bestLapNumber: [3], bestLapTimeMs: [1_600] } },
  }
  return Object.freeze({
    manifest, trackAssets: { circuitLengthMeters: 1_000 } as ReplayIndex['trackAssets'],
    lapSectorSidecar, qualifyingSummary, qualifyingLapStatus,
    loadChunk: async () => chunk, loadAllChunks: async () => [chunk],
  })
}

async function waitForReady(controller: ReturnType<typeof createReplayController>): Promise<void> {
  if (controller.getSnapshot().status === 'ready') return
  await new Promise<void>((resolveReady) => {
    const unsubscribe = controller.subscribe(() => {
      if (controller.getSnapshot().status !== 'ready') return
      unsubscribe()
      resolveReady()
    })
  })
}

function createScheduler(): PlaybackScheduler & { readonly fire: (at: number) => void; readonly cancelled: readonly number[] } {
  let nextHandle = 1
  let now = 0
  let callback: FrameRequestCallback | null = null
  const cancelled: number[] = []
  return {
    now: () => now,
    requestFrame: (nextCallback) => {
      callback = nextCallback
      return nextHandle++
    },
    cancelFrame: (handle) => {
      cancelled.push(handle)
      callback = null
    },
    fire: (at) => {
      now = at
      const scheduled = callback
      callback = null
      scheduled?.(at)
    },
    cancelled,
  }
}
