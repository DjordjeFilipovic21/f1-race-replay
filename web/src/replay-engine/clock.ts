export const PLAYBACK_SPEEDS = [0.25, 0.5, 1, 2, 4] as const
export const MAX_FRAME_ELAPSED_MS = 1_000

export type PlaybackSpeed = (typeof PLAYBACK_SPEEDS)[number]

export interface PlaybackScheduler {
  readonly now: () => number
  readonly requestFrame: (callback: FrameRequestCallback) => number
  readonly cancelFrame: (handle: number) => void
}

export interface PlaybackClockOptions {
  readonly startMs: number
  readonly endMs: number
  readonly scheduler: PlaybackScheduler
  readonly initialTimeMs?: number
  readonly initialSpeed?: PlaybackSpeed
}

export interface PlaybackClockSnapshot {
  readonly timeMs: number
  readonly speed: PlaybackSpeed
  readonly isPlaying: boolean
}

export interface PlaybackClock {
  readonly getSnapshot: () => PlaybackClockSnapshot
  readonly subscribe: (listener: () => void) => () => void
  readonly start: () => void
  readonly pause: () => void
  readonly seek: (timeMs: number) => void
  readonly setSpeed: (speed: PlaybackSpeed) => void
  readonly dispose: () => void
}

export function createPlaybackClock(options: PlaybackClockOptions): PlaybackClock {
  validateBounds(options.startMs, options.endMs)
  const { scheduler } = options
  let timeMs = clampTime(options.initialTimeMs ?? options.startMs, options.startMs, options.endMs)
  let replayTimeMs = timeMs
  let speed = options.initialSpeed ?? 1
  validateSpeed(speed)
  let isPlaying = false
  let isDisposed = false
  let frameHandle: number | null = null
  let previousFrameAt = scheduler.now()
  let snapshot = createSnapshot(timeMs, speed, isPlaying)
  const listeners = new Set<() => void>()

  const publish = () => {
    const nextSnapshot = createSnapshot(timeMs, speed, isPlaying)
    if (nextSnapshot.timeMs === snapshot.timeMs && nextSnapshot.speed === snapshot.speed && nextSnapshot.isPlaying === snapshot.isPlaying) return
    snapshot = nextSnapshot
    listeners.forEach((listener) => listener())
  }

  const scheduleFrame = () => {
    frameHandle = scheduler.requestFrame(onFrame)
  }

  const onFrame: FrameRequestCallback = (frameAt) => {
    frameHandle = null
    if (!isPlaying || isDisposed) return
    const elapsedMs = clampElapsed(frameAt - previousFrameAt)
    previousFrameAt = frameAt
    replayTimeMs = clampReplayTime(replayTimeMs + elapsedMs * speed, options.startMs, options.endMs)
    timeMs = Math.floor(replayTimeMs)
    if (replayTimeMs === options.endMs) isPlaying = false
    publish()
    if (isPlaying) scheduleFrame()
  }

  const cancelFrame = () => {
    if (frameHandle === null) return
    scheduler.cancelFrame(frameHandle)
    frameHandle = null
  }

  return Object.freeze({
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      if (isDisposed) return () => undefined
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    start: () => {
      if (isDisposed || isPlaying || timeMs === options.endMs) return
      isPlaying = true
      previousFrameAt = scheduler.now()
      publish()
      scheduleFrame()
    },
    pause: () => {
      if (isDisposed || !isPlaying) return
      isPlaying = false
      cancelFrame()
      publish()
    },
    seek: (requestedTimeMs: number) => {
      if (isDisposed) return
      const nextTimeMs = clampTime(requestedTimeMs, options.startMs, options.endMs)
      if (timeMs === nextTimeMs && replayTimeMs === nextTimeMs) return
      timeMs = nextTimeMs
      replayTimeMs = nextTimeMs
      previousFrameAt = scheduler.now()
      if (timeMs === options.endMs) {
        isPlaying = false
        cancelFrame()
      }
      publish()
    },
    setSpeed: (nextSpeed: PlaybackSpeed) => {
      if (isDisposed) return
      validateSpeed(nextSpeed)
      if (speed === nextSpeed) return
      speed = nextSpeed
      previousFrameAt = scheduler.now()
      publish()
    },
    dispose: () => {
      if (isDisposed) return
      isDisposed = true
      isPlaying = false
      cancelFrame()
      listeners.clear()
      publish()
    },
  })
}

function createSnapshot(timeMs: number, speed: PlaybackSpeed, isPlaying: boolean): PlaybackClockSnapshot {
  return Object.freeze({ timeMs, speed, isPlaying })
}

function validateBounds(startMs: number, endMs: number): void {
  if (!Number.isSafeInteger(startMs) || !Number.isSafeInteger(endMs) || startMs > endMs) {
    throw new RangeError('Replay bounds must be ordered integer milliseconds')
  }
}

function validateSpeed(speed: number): asserts speed is PlaybackSpeed {
  if (!PLAYBACK_SPEEDS.includes(speed as PlaybackSpeed)) throw new RangeError('Unsupported playback speed')
}

function clampTime(timeMs: number, startMs: number, endMs: number): number {
  if (!Number.isSafeInteger(timeMs)) throw new RangeError('Replay time must be an integer millisecond')
  return clampReplayTime(timeMs, startMs, endMs)
}

function clampReplayTime(timeMs: number, startMs: number, endMs: number): number {
  return Math.min(endMs, Math.max(startMs, timeMs))
}

function clampElapsed(elapsedMs: number): number {
  // Background rAF suspension is capped rather than caught up to avoid a surprise jump.
  return Math.min(MAX_FRAME_ELAPSED_MS, Math.max(0, elapsedMs))
}
