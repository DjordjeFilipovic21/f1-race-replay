import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'
import { loadReplayIndex } from '../src/replay-data/loader'
import type { ReplayChunk, ReplayIndex, ReplaySource } from '../src/replay-data/types'
import { createChunkCache } from '../src/replay-engine/chunk-cache'

const fixtureRoot = resolve(import.meta.dirname, '../../contracts/replay-data/v1/fixtures/deterministic-race')
const fixtureSource: ReplaySource = { read: (path) => readFile(resolve(fixtureRoot, path)) }

describe('replay-engine chunk cache', () => {
  test('deduplicates concurrent loads, prefetches adjacent chunks, and retains immutable entries', async () => {
    const index = await loadReplayIndex({ source: fixtureSource })
    let loads = 0
    const cache = createChunkCache(withLoadChunk(index, async (sequence) => { loads += 1; return index.loadChunk(sequence) }))

    const [first, second] = await Promise.all([cache.seek(1), cache.seek(1)])

    expect([loads, first.next?.sequence, second.next?.sequence, Object.isFrozen(first.current)]).toEqual([2, 2, 2, true])
  })

  test('keeps previous/current/next available across a handoff and evicts an obsolete seek window', async () => {
    const index = await threeChunkIndex()
    const cache = createChunkCache(index)

    const handoff = await cache.seek(2)
    await cache.seek(3)

    expect([handoff.previous?.sequence, handoff.current.sequence, handoff.next?.sequence, cache.getSnapshot().window?.previous?.sequence]).toEqual([1, 2, 3, 2])
  })

  test('publishes a failed request and retries it without retaining a poisoned rejection', async () => {
    const index = await loadReplayIndex({ source: fixtureSource })
    let attempts = 0
    const cache = createChunkCache(withLoadChunk(index, async (sequence) => {
      attempts += 1
      if (attempts === 1) throw new Error('temporary failure')
      return index.loadChunk(sequence)
    }))

    await expect(cache.seek(1)).rejects.toThrow('temporary failure')
    const window = await cache.retry()

    expect([cache.getSnapshot().status, window.current.sequence, attempts]).toEqual(['ready', 1, 3])
  })

  test('does not publish a stale seek completion over the newer window', async () => {
    const index = await threeChunkIndex()
    const pending = new Map<number, Deferred<ReplayChunk>>()
    const cache = createChunkCache(withLoadChunk(index, (sequence) => {
      const deferred = createDeferred<ReplayChunk>()
      pending.set(sequence, deferred)
      return deferred.promise
    }))

    const first = cache.seek(1)
    const second = cache.seek(3)
    pending.get(3)?.resolve(await index.loadChunk(3))
    pending.get(2)?.resolve(await index.loadChunk(2))
    await second
    pending.get(1)?.resolve(await index.loadChunk(1))
    await first

    expect([cache.getSnapshot().sequence, cache.getSnapshot().status, cache.getSnapshot().window?.current.sequence]).toEqual([3, 'ready', 3])
  })
})

function withLoadChunk(index: ReplayIndex, loadChunk: ReplayIndex['loadChunk']): ReplayIndex {
  return Object.freeze({ ...index, loadChunk })
}

async function threeChunkIndex(): Promise<ReplayIndex> {
  const index = await loadReplayIndex({ source: fixtureSource })
  const chunk = await index.loadChunk(2)
  const chunks = new Map([[1, await index.loadChunk(1)], [2, chunk], [3, Object.freeze({ ...chunk, chunkId: 'chunk-003', sequence: 3, startMs: 4_000, endMs: 6_000 })]])
  const manifest = Object.freeze({ ...index.manifest, chunks: Object.freeze([...index.manifest.chunks, { ...index.manifest.chunks[1], sequence: 3, path: 'chunks/chunk-003.json', startMs: 4_000, endMs: 6_000 }]) })
  return Object.freeze({ ...index, manifest, loadChunk: async (sequence: number) => {
    const value = chunks.get(sequence)
    if (!value) throw new Error(`Unknown chunk sequence: ${sequence}`)
    return value
  } })
}

interface Deferred<T> { readonly promise: Promise<T>; readonly resolve: (value: T) => void }

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((complete) => { resolve = complete })
  return { promise, resolve }
}
