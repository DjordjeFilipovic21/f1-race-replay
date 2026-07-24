import type { ReplayChunk, ReplayIndex } from '../../data/replay/types'

export type ChunkCacheStatus = 'idle' | 'loading' | 'ready' | 'error'

export interface ChunkWindow {
  readonly previous: ReplayChunk | null
  readonly current: ReplayChunk
  readonly next: ReplayChunk | null
}

export interface ChunkCacheSnapshot {
  readonly status: ChunkCacheStatus
  readonly sequence: number | null
  readonly window: ChunkWindow | null
  readonly error: unknown | null
}

export interface ChunkCache {
  readonly seek: (sequence: number) => Promise<ChunkWindow>
  readonly retry: () => Promise<ChunkWindow>
  readonly getSnapshot: () => ChunkCacheSnapshot
  readonly subscribe: (listener: () => void) => () => void
}

/**
 * Keeps only the chunks needed to sample across the active handoff.  A seek
 * revision makes completions from abandoned seeks observationally inert.
 */
export function createChunkCache(index: ReplayIndex): ChunkCache {
  const chunks = new Map<number, ReplayChunk>()
  const inFlight = new Map<number, Promise<ReplayChunk>>()
  const listeners = new Set<() => void>()
  let requestedSequence: number | null = null
  let revision = 0
  let snapshot = freezeSnapshot('idle', null, null, null)

  const publish = (next: ChunkCacheSnapshot): void => {
    snapshot = next
    listeners.forEach((listener) => listener())
  }

  const load = (sequence: number): Promise<ReplayChunk> => {
    const cached = chunks.get(sequence)
    if (cached) return Promise.resolve(cached)
    const pending = inFlight.get(sequence)
    if (pending) return pending
    const request = index.loadChunk(sequence).then((chunk) => {
      const immutable = Object.freeze(chunk)
      if (requestedSequence === null || isInWindow(sequence, requestedSequence)) chunks.set(sequence, immutable)
      return immutable
    })
    inFlight.set(sequence, request)
    void request.then(
      () => { inFlight.delete(sequence) },
      () => { inFlight.delete(sequence) },
    )
    return request
  }

  const seek = async (sequence: number): Promise<ChunkWindow> => {
    assertKnownSequence(index, sequence)
    const seekRevision = ++revision
    requestedSequence = sequence
    publish(freezeSnapshot('loading', sequence, null, null))
    try {
      const window = await loadWindow(index, sequence, load)
      if (seekRevision === revision) {
        evictOutsideWindow(chunks, sequence)
        publish(freezeSnapshot('ready', sequence, window, null))
      }
      return window
    } catch (error) {
      if (seekRevision === revision) publish(freezeSnapshot('error', sequence, null, error))
      throw error
    }
  }

  return Object.freeze({
    seek,
    retry: () => {
      if (requestedSequence === null) return Promise.reject(new Error('No chunk seek is available to retry'))
      return seek(requestedSequence)
    },
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener)
      return () => { listeners.delete(listener) }
    },
  })
}

async function loadWindow(index: ReplayIndex, sequence: number, load: (sequence: number) => Promise<ReplayChunk>): Promise<ChunkWindow> {
  const [previous, current, next] = await Promise.all([
    sequence === 1 ? Promise.resolve(null) : load(sequence - 1),
    load(sequence),
    sequence === index.manifest.chunks.length ? Promise.resolve(null) : load(sequence + 1),
  ])
  return Object.freeze({ previous, current, next })
}

function assertKnownSequence(index: ReplayIndex, sequence: number): void {
  if (!Number.isSafeInteger(sequence) || sequence < 1 || sequence > index.manifest.chunks.length) {
    throw new RangeError(`Unknown chunk sequence: ${sequence}`)
  }
}

function evictOutsideWindow(chunks: Map<number, ReplayChunk>, sequence: number): void {
  for (const cachedSequence of chunks.keys()) {
    if (!isInWindow(cachedSequence, sequence)) chunks.delete(cachedSequence)
  }
}

function isInWindow(sequence: number, currentSequence: number): boolean {
  return sequence >= currentSequence - 1 && sequence <= currentSequence + 1
}

function freezeSnapshot(status: ChunkCacheStatus, sequence: number | null, window: ChunkWindow | null, error: unknown | null): ChunkCacheSnapshot {
  return Object.freeze({ status, sequence, window, error })
}
