import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { describe, expect, test, vi } from 'vitest'
import { createReplayController } from '../src/replay-engine/controller'
import { loadReplayIndex } from '../src/replay-data/loader'
import type { ReplayChunk, ReplayIndex, ReplaySource } from '../src/replay-data/types'
import type { PlaybackScheduler } from '../src/replay-engine/clock'

const fixtureRoot = resolve(import.meta.dirname, '../../contracts/replay-data/v1/fixtures/deterministic-race')
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

  test('pauses one delayed unavailable transition and retains its crossed event', async () => {
    const base = await eventIndex(1_500)
    const deferred = createDeferred<ReplayChunk>()
    const index = withThirdChunk(base, (sequence) => sequence === 3 ? deferred.promise : base.loadChunk(sequence))
    const loadChunk = vi.fn(index.loadChunk)
    const scheduler = createScheduler()
    const controller = createReplayController({ index: Object.freeze({ ...index, loadChunk }), scheduler })
    await waitForReady(controller)
    controller.start()
    scheduler.fire(1_000)
    scheduler.fire(2_000)
    scheduler.fire(2_500)

    expect([controller.getSnapshot().isPlaying, loadChunk.mock.calls.filter(([sequence]) => sequence === 2).length]).toEqual([true, 1])
    deferred.resolve(Object.freeze({ ...(await base.loadChunk(2)), sequence: 3, startMs: 4_000, endMs: 6_000 }))
    await waitForReady(controller)

    expect(controller.getSnapshot().crossedEvents.map(({ eventType }) => eventType)).toEqual(['pass'])
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
    controller.seek(2_000)
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
    controller.seek(2_000)
    controller.seek(500)
    deferred.resolve(Object.freeze({ ...(await base.loadChunk(2)), sequence: 3, startMs: 4_000, endMs: 6_000 }))
    await Promise.resolve()

    expect([controller.getSnapshot().status, controller.getSnapshot().replay?.sessionTimeMs]).toEqual(['ready', 500])
  })
})

function createDeferred<T>(): { readonly promise: Promise<T>; readonly resolve: (value: T) => void } {
  let resolvePromise!: (value: T) => void
  return { promise: new Promise<T>((resolve) => { resolvePromise = resolve }), resolve: (value) => resolvePromise(value) }
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
