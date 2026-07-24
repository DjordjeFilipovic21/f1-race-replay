import type { ReplayChunk, ReplayData, ReplayEvent, ReplayIndex } from '../../data/replay/types'
import { createChunkCache, type ChunkCacheStatus, type ChunkWindow } from './chunk-cache'
import { createPlaybackClock, type PlaybackScheduler, type PlaybackSpeed } from './clock'
import { forwardEventCrossings } from './events'
import { prepareReplaySampler, samplePreparedReplayAt, type CoordinateInterpolationStrategy, type PreparedReplaySampler } from './sampler'
import { createReplayStore, type StoreListener } from './store'
import type { ReplaySnapshot } from './types'

export interface ReplayControllerOptions { readonly index: ReplayIndex; readonly scheduler?: PlaybackScheduler; readonly initialTimeMs?: number; readonly initialSpeed?: PlaybackSpeed; readonly coordinateInterpolation?: CoordinateInterpolationStrategy }
export interface ReplayControllerSnapshot {
  readonly status: ChunkCacheStatus; readonly timeMs: number; readonly speed: PlaybackSpeed; readonly isPlaying: boolean
  readonly replay: ReplaySnapshot | null; readonly crossedEvents: readonly ReplayEvent[]; readonly error: unknown | null
}
export interface ReplayController {
  readonly getSnapshot: () => ReplayControllerSnapshot; readonly subscribe: (listener: StoreListener) => () => void
  readonly start: () => void; readonly pause: () => void; readonly seek: (timeMs: number) => void; readonly setSpeed: (speed: PlaybackSpeed) => void
  readonly retry: () => Promise<void>; readonly dispose: () => void
}

export function createReplayController(options: ReplayControllerOptions): ReplayController {
  const bounds = getReplayBounds(options.index)
  const clock = createPlaybackClock({ startMs: bounds.startMs, endMs: bounds.endMs, scheduler: options.scheduler ?? createBrowserPlaybackScheduler(), initialTimeMs: options.initialTimeMs, initialSpeed: options.initialSpeed })
  const cache = createChunkCache(options.index)
  const store = createReplayStore(createSnapshot('loading', clock.getSnapshot(), null, Object.freeze([]), null))
  let disposed = false
  let generation = 0
  let readyWindow: ChunkWindow | null = null
  let readySampler: PreparedReplaySampler | null = null
  let lastTimeMs = clock.getSnapshot().timeMs
  let requestedPlaying = false
  let suppressNextCrossing = false
  let transition: Promise<void> | null = null
  let promotingSequence: number | null = null
  let beginningTransition = false
  let pendingCrossings: readonly ReplayEvent[] = Object.freeze([])
  let ignoreClockChange = false

  const publish = (status: ChunkCacheStatus, replay: ReplaySnapshot | null, crossedEvents: readonly ReplayEvent[], error: unknown | null): void => {
    store.publish(createSnapshot(status, clock.getSnapshot(), replay, crossedEvents, error, requestedPlaying))
  }

  const beginTransition = (timeMs: number, crossings: readonly ReplayEvent[], retry = false, crossingStartMs: number | null = null): Promise<void> => {
    if (!retry && transition !== null) return transition
    const loadGeneration = ++generation
    const sequence = resolveSequence(options.index, timeMs)
    pendingCrossings = Object.freeze([...pendingCrossings, ...crossings])
    beginningTransition = true
    if (clock.getSnapshot().isPlaying) clock.pause()
    publish('loading', null, Object.freeze([]), null)
    const request = retry ? cache.retry() : cache.seek(sequence)
    transition = request.then(
      (window) => {
        if (disposed || loadGeneration !== generation || clock.getSnapshot().timeMs !== timeMs) return
        readyWindow = window
        readySampler = prepareReplaySampler(composeWindowReplayData(options.index, window), undefined, options.coordinateInterpolation)
        const retainedCrossings = crossingStartMs === null
          ? pendingCrossings
          : mergeCrossings(pendingCrossings, forwardEventCrossings(eventsInWindow(window), crossingStartMs, timeMs))
        pendingCrossings = Object.freeze([])
        publish('ready', samplePreparedReplayAt(readySampler, timeMs), retainedCrossings, null)
        transition = null
        if (requestedPlaying && timeMs < bounds.endMs) {
          ignoreClockChange = true
          try { clock.start() }
          finally { ignoreClockChange = false }
        }
      },
      (error: unknown) => {
        if (disposed || loadGeneration !== generation || clock.getSnapshot().timeMs !== timeMs) return
        transition = null
        publish('error', null, Object.freeze([]), error)
      },
    )
    beginningTransition = false
    return transition
  }

  const promoteWindow = (sequence: number): void => {
    if (readyWindow?.current.sequence === sequence || promotingSequence === sequence) return
    const promotionGeneration = generation
    promotingSequence = sequence
    void cache.seek(sequence).then(
      (window) => {
        if (disposed || promotionGeneration !== generation) return
        readyWindow = window
        readySampler = prepareReplaySampler(composeWindowReplayData(options.index, window), undefined, options.coordinateInterpolation)
      },
      () => undefined,
    ).finally(() => {
      if (promotionGeneration === generation && promotingSequence === sequence) promotingSequence = null
    })
  }

  const handleTime = (timeMs: number, isSeek: boolean): void => {
    const sequence = resolveSequence(options.index, timeMs)
    const previousTimeMs = lastTimeMs
    const crossings = suppressNextCrossing || isSeek || timeMs <= previousTimeMs || readyWindow === null
      ? Object.freeze([])
      : forwardEventCrossings(eventsInWindow(readyWindow), previousTimeMs, timeMs)
    suppressNextCrossing = false
    lastTimeMs = timeMs
    if (timeMs === bounds.endMs) requestedPlaying = false
    if (readyWindow !== null && readySampler !== null && (sequence === readyWindow.current.sequence || sequence === readyWindow.next?.sequence)) {
      publish('ready', samplePreparedReplayAt(readySampler, timeMs), crossings, null)
      if (sequence !== readyWindow.current.sequence) promoteWindow(sequence)
      return
    }
    void beginTransition(timeMs, crossings, false, isSeek ? null : previousTimeMs)
  }

  const onClockChange = (): void => { if (!disposed && !ignoreClockChange && !beginningTransition && transition === null) handleTime(clock.getSnapshot().timeMs, false) }
  const unsubscribeClock = clock.subscribe(onClockChange)
  void beginTransition(lastTimeMs, Object.freeze([]))

  return Object.freeze({
    getSnapshot: store.getSnapshot,
    subscribe: store.subscribe,
    start: () => {
      if (disposed || requestedPlaying || clock.getSnapshot().timeMs === bounds.endMs || store.getSnapshot().status !== 'ready') return
      requestedPlaying = true
      clock.start()
    },
    pause: () => {
      if (disposed || !requestedPlaying) return
      requestedPlaying = false
      if (clock.getSnapshot().isPlaying) clock.pause()
      else publish(store.getSnapshot().status, store.getSnapshot().replay, Object.freeze([]), store.getSnapshot().error)
    },
    seek: (timeMs: number) => {
      if (disposed) return
      const nextTimeMs = clampTime(timeMs, bounds)
      generation += 1 // A public seek invalidates every outstanding controller completion, including a ready-window seek.
      promotingSequence = null
      transition = null
      pendingCrossings = Object.freeze([])
      suppressNextCrossing = true
      ignoreClockChange = true
      clock.seek(nextTimeMs)
      ignoreClockChange = false
      handleTime(nextTimeMs, true)
    },
    setSpeed: (speed: PlaybackSpeed) => { if (!disposed) clock.setSpeed(speed) },
    retry: async () => {
      if (disposed || store.getSnapshot().status !== 'error') return
      await beginTransition(clock.getSnapshot().timeMs, Object.freeze([]), true)
    },
    dispose: () => {
      if (disposed) return
      disposed = true
      generation += 1
      unsubscribeClock()
      clock.dispose()
      store.dispose()
    },
  })
}

/** Lazily resolves browser APIs so importing this module remains SSR-safe. */
export function createBrowserPlaybackScheduler(): PlaybackScheduler {
  const requestFrame = globalThis.requestAnimationFrame
  const cancelFrame = globalThis.cancelAnimationFrame
  if (!requestFrame || !cancelFrame || !globalThis.performance) throw new Error('Browser playback APIs are unavailable; inject a scheduler')
  return Object.freeze({ now: () => globalThis.performance.now(), requestFrame: (callback: FrameRequestCallback) => requestFrame.call(globalThis, callback), cancelFrame: (handle: number) => cancelFrame.call(globalThis, handle) })
}

function getReplayBounds(index: ReplayIndex): { readonly startMs: number; readonly endMs: number } {
  const chunks = index.manifest.chunks
  if (chunks.length === 0) throw new Error('Replay manifest has no chunks')
  return { startMs: chunks[0].startMs, endMs: chunks[chunks.length - 1].endMs }
}

function resolveSequence(index: ReplayIndex, timeMs: number): number {
  const chunks = index.manifest.chunks
  const clamped = clampTime(timeMs, getReplayBounds(index))
  let low = 0
  let high = chunks.length
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    if (chunks[middle].endMs <= clamped) low = middle + 1
    else high = middle
  }
  return chunks[low]?.sequence ?? chunks[chunks.length - 1].sequence
}

function composeWindowReplayData(index: ReplayIndex, window: ChunkWindow): ReplayData {
  const chunks = [window.previous, window.current, window.next].filter((chunk): chunk is ReplayChunk => chunk !== null)
  return Object.freeze({ ...(index.pointer ? { pointer: index.pointer } : {}), manifest: index.manifest, trackAssets: index.trackAssets, chunks: Object.freeze(chunks) })
}
function eventsInWindow(window: ChunkWindow): readonly ReplayEvent[] { return Object.freeze([window.previous, window.current, window.next].filter((chunk): chunk is ReplayChunk => chunk !== null).flatMap((chunk) => chunk.events)) }
function mergeCrossings(existing: readonly ReplayEvent[], additional: readonly ReplayEvent[]): readonly ReplayEvent[] {
  const seen = new Set(existing)
  return Object.freeze([...existing, ...additional.filter((event) => !seen.has(event))])
}
function createSnapshot(status: ChunkCacheStatus, clock: { readonly timeMs: number; readonly speed: PlaybackSpeed; readonly isPlaying: boolean }, replay: ReplaySnapshot | null, crossedEvents: readonly ReplayEvent[], error: unknown | null, isPlaying = clock.isPlaying): ReplayControllerSnapshot { return Object.freeze({ status, timeMs: clock.timeMs, speed: clock.speed, isPlaying, replay, crossedEvents, error }) }
function clampTime(timeMs: number, bounds: { readonly startMs: number; readonly endMs: number }): number { if (!Number.isSafeInteger(timeMs)) throw new RangeError('Replay time must be an integer millisecond'); return Math.min(bounds.endMs, Math.max(bounds.startMs, timeMs)) }
