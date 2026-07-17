import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { describe, expect, test, vi } from 'vitest'
import { loadReplayData, loadReplayIndex } from '../src/replay-data/loader'
import type { ReplayChunk, ReplayIndex, ReplaySource } from '../src/replay-data/types'
import { createReplayController } from '../src/replay-engine/controller'
import type { PlaybackScheduler } from '../src/replay-engine/clock'
import { sampleReplayAt } from '../src/replay-engine/sampler'

const fixtureRoot = resolve(import.meta.dirname, '../../contracts/replay-data/v1/fixtures/deterministic-race')

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
})

function legacyIndex(): ReplayIndex {
  const chunk: ReplayChunk = {
    contractVersion: 'v1', fixtureId: 'legacy', chunkId: 'chunk-001', sequence: 1, startMs: 0, endMs: 1_000,
    overlap: { kind: 'none', previousChunkPath: null, range: null, authoritativeFromMs: null }, timeMs: [0, 1_000], authoritativeStartIndex: 0,
    drivers: { HAM: { x: [0, 1], y: [0, 1], trackDistanceMeters: [null, null], speed: [null, null], throttle: [null, null], brake: [null, null], gapToLeaderMs: [null, null], lap: [1, 1], position: [null, null], gear: [null, null], drs: [null, null], tyreCompound: [null, null], status: [null, null], isInPitLane: [null, null] } },
    leaderboardOrder: [['HAM'], ['HAM']], trackStatusCode: [null, null], weatherState: [null, null], events: [],
  }
  const manifest = { contractVersion: 'v1' as const, fixtureId: 'legacy', fixtureName: 'Legacy', schemas: { manifest: '', chunk: '', trackAssets: '' }, trackAssets: { path: '', schemaId: '' }, chunks: [{ sequence: 1, path: 'chunks/chunk-001.json', schemaId: '', startMs: 0, endMs: 1_000, overlapWithPreviousMs: 0 }], drivers: [{ id: 'HAM', displayName: 'Hamilton', teamName: 'Mercedes', colorHex: '#000000', carNumber: '44' }] }
  return Object.freeze({ manifest, trackAssets: { circuitLengthMeters: 1_000 } as ReplayIndex['trackAssets'], loadChunk: async () => chunk, loadAllChunks: async () => [chunk] })
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
