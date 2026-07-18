import { describe, expect, test } from 'vitest'
import { createPlaybackClock, MAX_FRAME_ELAPSED_MS, TARGET_PLAYBACK_FPS, type PlaybackScheduler } from '../src/replay-engine/clock'
import { createEventCrossingCursor, exactTimeEvents, forwardEventCrossings } from '../src/replay-engine/events'
import type { ReplayEvent } from '../src/replay-data/types'

function createScheduler(): PlaybackScheduler & { readonly fire: (at: number) => void; readonly cancelled: readonly number[]; readonly requested: () => number } {
  let nextHandle = 1
  let callback: FrameRequestCallback | null = null
  const cancelled: number[] = []
  let requestCount = 0
  return {
    now: () => 0,
    requestFrame: (nextCallback) => {
      callback = nextCallback
      requestCount += 1
      return nextHandle++
    },
    cancelFrame: (handle) => { cancelled.push(handle) },
    fire: (at) => {
      const scheduled = callback
      callback = null
      scheduled?.(at)
    },
    cancelled,
    requested: () => requestCount,
  }
}

const EVENTS: readonly ReplayEvent[] = [
  { sessionTimeMs: 100, eventType: 'flag', description: 'Flag' },
  { sessionTimeMs: 200, eventType: 'pass', description: 'Pass' },
]

describe('playback clock', () => {
  test('schedules once and cancels idempotently when paused', () => {
    const scheduler = createScheduler()
    const clock = createPlaybackClock({ startMs: 0, endMs: 1_000, scheduler })

    clock.start()
    clock.start()
    clock.pause()
    clock.pause()

    expect(scheduler.requested()).toBe(1)
    expect(scheduler.cancelled).toEqual([1])
  })

  test('advances from frame timestamps at the configured speed', () => {
    const scheduler = createScheduler()
    const clock = createPlaybackClock({ startMs: 0, endMs: 1_000, scheduler })

    clock.start()
    clock.setSpeed(2)
    scheduler.fire(125)

    expect(clock.getSnapshot().timeMs).toBe(250)
  })

  test('caps playback publication cadence at the configured target', () => {
    const scheduler = createScheduler()
    const clock = createPlaybackClock({ startMs: 0, endMs: 1_000, scheduler })

    clock.start()
    scheduler.fire(16)
    expect(clock.getSnapshot().timeMs).toBe(0)
    const targetFrameMs = Math.ceil(1_000 / TARGET_PLAYBACK_FPS)
    scheduler.fire(targetFrameMs)

    expect(clock.getSnapshot().timeMs).toBe(targetFrameMs)
  })

  test('carries target deadlines forward instead of degrading to every third 60Hz frame', () => {
    const scheduler = createScheduler()
    const clock = createPlaybackClock({ startMs: 0, endMs: 1_000, scheduler })

    clock.start()
    scheduler.fire(50)
    scheduler.fire(67)
    scheduler.fire(84)

    expect(clock.getSnapshot().timeMs).toBe(84)
  })

  test('clamps integer seeks and pauses automatically at the end', () => {
    const scheduler = createScheduler()
    const clock = createPlaybackClock({ startMs: 100, endMs: 300, scheduler })

    clock.seek(-100)
    clock.start()
    scheduler.fire(500)

    expect(clock.getSnapshot()).toEqual({ timeMs: 300, speed: 1, isPlaying: false })
  })

  test('caps a large background-frame gap instead of catching it up', () => {
    const scheduler = createScheduler()
    const clock = createPlaybackClock({ startMs: 0, endMs: 10_000, scheduler })

    clock.start()
    scheduler.fire(MAX_FRAME_ELAPSED_MS + 9_000)

    expect(clock.getSnapshot().timeMs).toBe(MAX_FRAME_ELAPSED_MS)
  })

  test('disposal cancels the pending frame once and prevents a restart', () => {
    const scheduler = createScheduler()
    const clock = createPlaybackClock({ startMs: 0, endMs: 1_000, scheduler })

    clock.start()
    clock.dispose()
    clock.dispose()
    clock.start()

    expect(scheduler.cancelled).toEqual([1])
  })
})

describe('event semantics', () => {
  test('keeps exact-time events separate from forward crossings', () => {
    expect(exactTimeEvents(EVENTS, 100)).toEqual([EVENTS[0]])
    expect(forwardEventCrossings(EVENTS, 100, 200)).toEqual([EVENTS[1]])
  })

  test('does not emit historical crossings when seeking backward', () => {
    const cursor = createEventCrossingCursor(EVENTS, 0)
    cursor.advance(200)
    cursor.seek(50)

    expect(cursor.advance(75)).toEqual([])
  })
})
