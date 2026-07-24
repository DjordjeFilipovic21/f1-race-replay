import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { describe, expect, test, vi } from 'vitest'
import { createReplayController } from '../../../src/engine/replay/controller'
import { loadReplayIndex } from '../../../src/data/replay/loader'
import type { ReplayChunk, ReplayIndex, ReplaySource } from '../../../src/data/replay/types'
import type { PlaybackScheduler } from '../../../src/engine/replay/clock'

const fixtureRoot = resolve(import.meta.dirname, '../../../../contracts/replay-data/v1/fixtures/deterministic-race')
const fixtureSource: ReplaySource = { read: (path) => readFile(resolve(fixtureRoot, path)) }

describe('replay controller', () => {
  test('composes loader-compatible indexed chunks without loading all chunks', async () => {
    const index = await loadReplayIndex({ source: fixtureSource })
    let allChunkLoads = 0
    const controller = createReplayController({ index: Object.freeze({ ...index, loadAllChunks: async () => { allChunkLoads += 1; return [] } }), scheduler: createScheduler() })

    await waitForReady(controller)

    expect([controller.getSnapshot().replay?.sessionTimeMs, allChunkLoads]).toEqual([0, 0])
  })

  test('keeps getSnapshot identity until state changes and removes the exact listener', async () => {
    const controller = createReplayController({ index: await loadReplayIndex({ source: fixtureSource }), scheduler: createScheduler() })
    await waitForReady(controller)
    const initial = controller.getSnapshot()
    const repeated = controller.getSnapshot()
    let calls = 0
    const unsubscribe = controller.subscribe(() => { calls += 1 })
    unsubscribe()
    controller.setSpeed(2)

    expect([Object.is(initial, repeated), calls]).toEqual([true, 0])
  })

  test('publishes deterministic playback time and forward crossing events', async () => {
    const scheduler = createScheduler()
    const index = await eventIndex()
    const controller = createReplayController({ index, scheduler })
    await waitForReady(controller)
    controller.start()
    scheduler.fire(1_000)

    expect([controller.getSnapshot().timeMs, controller.getSnapshot().crossedEvents.map(({ eventType }) => eventType)]).toEqual([1_000, ['pass']])
  })

  test('seeks to the manifest-owned handoff chunk without emitting historical events', async () => {
    const controller = createReplayController({ index: await loadReplayIndex({ source: fixtureSource }), scheduler: createScheduler() })
    await waitForReady(controller)
    controller.seek(2_000)
    await waitForReady(controller)

    expect([controller.getSnapshot().replay?.sessionTimeMs, controller.getSnapshot().crossedEvents]).toEqual([2_000, []])
  })

  test('keeps telemetry ready while handing off to a prefetched chunk', async () => {
    const scheduler = createScheduler()
    const controller = createReplayController({ index: await loadReplayIndex({ source: fixtureSource }), scheduler })
    const snapshots: ReturnType<typeof controller.getSnapshot>[] = []
    controller.subscribe(() => { snapshots.push(controller.getSnapshot()) })
    await waitForReady(controller)
    controller.start()
    scheduler.fire(1_000)
    scheduler.fire(2_000)

    expect(controller.getSnapshot()).toMatchObject({ status: 'ready', isPlaying: true, replay: { sessionTimeMs: 2_000 } })
    expect(snapshots.some(({ timeMs, replay }) => timeMs === 2_000 && replay === null)).toBe(false)
  })

  test('seeks into a prefetched chunk without clearing telemetry and promotes the following window', async () => {
    const base = await loadReplayIndex({ source: fixtureSource })
    const third = Object.freeze({ ...(await base.loadChunk(2)), chunkId: 'chunk-003', sequence: 3, startMs: 4_000, endMs: 6_000 })
    const loadChunk = vi.fn((sequence: number) => sequence === 3 ? Promise.resolve(third) : base.loadChunk(sequence))
    const controller = createReplayController({ index: withThirdChunk(base, loadChunk), scheduler: createScheduler() })
    const snapshots: ReturnType<typeof controller.getSnapshot>[] = []
    controller.subscribe(() => { snapshots.push(controller.getSnapshot()) })
    await waitForReady(controller)
    controller.seek(2_000)
    await flushAsyncWork()
    controller.seek(4_000)

    expect(controller.getSnapshot()).toMatchObject({ status: 'ready', replay: { sessionTimeMs: 4_000 } })
    expect(snapshots.some(({ timeMs, replay }) => timeMs === 2_000 && replay === null)).toBe(false)
    expect(loadChunk.mock.calls.filter(([sequence]) => sequence === 3)).toHaveLength(1)
  })

  test('propagates a cache error and retries without publishing a stale completion', async () => {
    const base = await loadReplayIndex({ source: fixtureSource })
    let attempts = 0
    const index = Object.freeze({ ...base, loadChunk: async (sequence: number): Promise<ReplayChunk> => {
      attempts += 1
      if (attempts === 1) throw new Error('temporary failure')
      return base.loadChunk(sequence)
    } })
    const controller = createReplayController({ index, scheduler: createScheduler() })
    await waitForStatus(controller, 'error')
    await controller.retry()

    expect([controller.getSnapshot().status, controller.getSnapshot().replay?.sessionTimeMs]).toEqual(['ready', 0])
  })

  test('disposes idempotently and cancels the pending scheduler frame', async () => {
    const scheduler = createScheduler()
    const controller = createReplayController({ index: await loadReplayIndex({ source: fixtureSource }), scheduler })
    await waitForReady(controller)
    controller.start()
    controller.dispose()
    controller.dispose()

    expect(scheduler.cancelled).toEqual([1])
  })

  test('retains events from a delayed promoted chunk at the following handoff', async () => {
    const base = await loadReplayIndex({ source: fixtureSource })
    const deferred = createDeferred<ReplayChunk>()
    const second = Object.freeze({
      ...(await base.loadChunk(2)),
      events: Object.freeze([{ sessionTimeMs: 3_500, eventType: 'sector', description: 'Sector' }]),
    })
    const index = withThirdChunk(base, (sequence) => sequence === 2 ? Promise.resolve(second) : sequence === 3 ? deferred.promise : base.loadChunk(sequence))
    const loadChunk = vi.fn(index.loadChunk)
    const scheduler = createScheduler()
    const controller = createReplayController({ index: Object.freeze({ ...index, loadChunk }), scheduler })
    await waitForReady(controller)
    controller.start()
    scheduler.fire(1_000)
    scheduler.fire(2_000)
    scheduler.fire(3_000)
    scheduler.fire(4_000)

    expect([controller.getSnapshot().isPlaying, loadChunk.mock.calls.filter(([sequence]) => sequence === 2).length]).toEqual([true, 1])
    deferred.resolve(Object.freeze({
      ...second,
      sequence: 3,
      startMs: 4_000,
      endMs: 6_000,
      events: Object.freeze([{ sessionTimeMs: 4_000, eventType: 'pass', description: 'Pass' }]),
    }))
    await waitForReady(controller)

    expect(controller.getSnapshot().crossedEvents.map(({ sessionTimeMs }) => sessionTimeMs)).toEqual([3_500, 4_000])
  })

  test('keeps a transition error paused until explicit retry', async () => {
    const base = await loadReplayIndex({ source: fixtureSource })
    let fail = true
    const controller = createReplayController({ index: withThirdChunk(base, async (sequence) => {
      if (sequence === 3 && fail) throw new Error('unavailable')
      if (sequence === 3) return Object.freeze({ ...(await base.loadChunk(2)), chunkId: 'chunk-003', sequence: 3, startMs: 4_000, endMs: 6_000 })
      return base.loadChunk(sequence)
    }), scheduler: createScheduler() })
    await waitForReady(controller)
    controller.start()
    controller.seek(4_000)
    await waitForStatus(controller, 'error')
    await Promise.resolve()

    expect(controller.getSnapshot().isPlaying).toBe(true)
    fail = false
    await controller.retry()
    expect(controller.getSnapshot().status).toBe('ready')
  })

  test('ignores an abandoned far completion after a ready-window seek', async () => {
    const base = await loadReplayIndex({ source: fixtureSource })
    const deferred = createDeferred<ReplayChunk>()
    const controller = createReplayController({ index: withThirdChunk(base, (sequence) => sequence === 3 ? deferred.promise : base.loadChunk(sequence)), scheduler: createScheduler() })
    await waitForReady(controller)
    controller.seek(4_000)
    controller.seek(500)
    deferred.resolve(Object.freeze({ ...(await base.loadChunk(2)), sequence: 3, startMs: 4_000, endMs: 6_000 }))
    await flushAsyncWork()

    expect([controller.getSnapshot().status, controller.getSnapshot().replay?.sessionTimeMs]).toEqual(['ready', 500])
  })
})

function createDeferred<T>(): { readonly promise: Promise<T>; readonly resolve: (value: T) => void } {
  let resolvePromise!: (value: T) => void
  return { promise: new Promise<T>((resolve) => { resolvePromise = resolve }), resolve: (value) => resolvePromise(value) }
}

async function flushAsyncWork(): Promise<void> {
  await new Promise<void>((resolve) => { setTimeout(resolve, 0) })
}

function withThirdChunk(base: ReplayIndex, loadChunk: ReplayIndex['loadChunk']): ReplayIndex {
  const second = base.manifest.chunks[1]
  const third = Object.freeze({ ...second, sequence: 3, startMs: 4_000, endMs: 6_000 })
  return Object.freeze({ ...base, manifest: Object.freeze({ ...base.manifest, chunks: Object.freeze([...base.manifest.chunks, third]) }), loadChunk })
}

async function eventIndex(eventTimeMs = 1_000): Promise<ReplayIndex> {
  const index = await loadReplayIndex({ source: fixtureSource })
  return Object.freeze({ ...index, loadChunk: async (sequence: number) => {
    const chunk = await index.loadChunk(sequence)
    if (sequence !== 1) return chunk
    return Object.freeze({ ...chunk, events: Object.freeze([{ sessionTimeMs: eventTimeMs, eventType: 'pass', description: 'Pass' }]) })
  } })
}

async function waitForReady(controller: ReturnType<typeof createReplayController>): Promise<void> {
  await waitForStatus(controller, 'ready')
}

async function waitForStatus(controller: ReturnType<typeof createReplayController>, status: 'ready' | 'error'): Promise<void> {
  if (controller.getSnapshot().status === status) return
  await new Promise<void>((resolveReady) => {
    const unsubscribe = controller.subscribe(() => {
      if (controller.getSnapshot().status !== status) return
      unsubscribe()
      resolveReady()
    })
  })
}

function createScheduler(): PlaybackScheduler & { readonly fire: (at: number) => void; readonly cancelled: readonly number[] } {
  let nextHandle = 1
  let callback: FrameRequestCallback | null = null
  const cancelled: number[] = []
  return {
    now: () => 0,
    requestFrame: (nextCallback) => {
      callback = nextCallback
      return nextHandle++
    },
    cancelFrame: (handle) => { cancelled.push(handle) },
    fire: (at) => {
      const scheduled = callback
      callback = null
      scheduled?.(at)
    },
    cancelled,
  }
}
