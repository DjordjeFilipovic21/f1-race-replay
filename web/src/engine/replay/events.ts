import type { ReplayEvent } from '../../data/replay/types'

export function exactTimeEvents(events: readonly ReplayEvent[], timeMs: number): readonly ReplayEvent[] {
  validateTime(timeMs)
  return Object.freeze(events.filter((event) => event.sessionTimeMs === timeMs))
}

export function forwardEventCrossings(events: readonly ReplayEvent[], previousTimeMs: number, currentTimeMs: number): readonly ReplayEvent[] {
  validateTime(previousTimeMs)
  validateTime(currentTimeMs)
  if (currentTimeMs <= previousTimeMs) return Object.freeze([])
  return Object.freeze(events.filter((event) => event.sessionTimeMs > previousTimeMs && event.sessionTimeMs <= currentTimeMs))
}

export interface EventCrossingCursor {
  readonly advance: (timeMs: number) => readonly ReplayEvent[]
  readonly seek: (timeMs: number) => void
  readonly getTimeMs: () => number
}

export function createEventCrossingCursor(events: readonly ReplayEvent[], initialTimeMs: number): EventCrossingCursor {
  validateTime(initialTimeMs)
  let currentTimeMs = initialTimeMs
  return Object.freeze({
    advance: (nextTimeMs: number) => {
      validateTime(nextTimeMs)
      const crossings = forwardEventCrossings(events, currentTimeMs, nextTimeMs)
      currentTimeMs = nextTimeMs
      return crossings
    },
    seek: (nextTimeMs: number) => {
      validateTime(nextTimeMs)
      currentTimeMs = nextTimeMs
    },
    getTimeMs: () => currentTimeMs,
  })
}

function validateTime(timeMs: number): void {
  if (!Number.isSafeInteger(timeMs)) throw new RangeError('Replay time must be an integer millisecond')
}
