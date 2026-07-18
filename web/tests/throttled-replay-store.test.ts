import { afterEach, expect, test, vi } from 'vitest'
import { createThrottledReplayStore } from '../src/replay-ui/throttled-replay-store'
import type { ReplayController, ReplayControllerSnapshot } from '../src/replay-engine'

const ready = (timeMs: number, isPlaying = true): ReplayControllerSnapshot => ({
  status: 'ready', timeMs, speed: 1, isPlaying, replay: null, crossedEvents: [], error: null,
})

function createController(initial: ReplayControllerSnapshot) {
  let snapshot = initial
  const listeners = new Set<() => void>()
  let subscribeCalls = 0
  let unsubscribeCalls = 0
  const controller: ReplayController = {
    getSnapshot: () => snapshot,
    subscribe: (listener) => {
      subscribeCalls += 1
      listeners.add(listener)
      return () => { unsubscribeCalls += 1; listeners.delete(listener) }
    },
    start: vi.fn(), pause: vi.fn(), seek: vi.fn(), setSpeed: vi.fn(), retry: vi.fn(async () => undefined), dispose: vi.fn(),
  }
  return {
    controller,
    publish: (next: ReplayControllerSnapshot) => { snapshot = next; listeners.forEach((listener) => listener()) },
    getSubscribeCalls: () => subscribeCalls,
    getUnsubscribeCalls: () => unsubscribeCalls,
  }
}

afterEach(() => vi.useRealTimers())

test('bounds playing updates while immediately publishing paused and status snapshots', () => {
  vi.useFakeTimers()
  const source = createController(ready(0))
  const store = createThrottledReplayStore(source.controller, 125)
  expect(source.getSubscribeCalls()).toBe(0)
  const listener = vi.fn()
  store.subscribe(listener)
  expect(source.getSubscribeCalls()).toBe(1)

  for (let timeMs = 1; timeMs <= 20; timeMs += 1) source.publish(ready(timeMs))
  expect(listener).not.toHaveBeenCalled()
  vi.advanceTimersByTime(125)
  expect(listener).toHaveBeenCalledOnce()
  expect(store.getSnapshot().timeMs).toBe(20)

  source.publish(ready(21, false))
  expect(listener).toHaveBeenCalledTimes(2)
  source.publish({ ...ready(22), status: 'loading', replay: null })
  expect(listener).toHaveBeenCalledTimes(3)
  store.dispose()
  expect(source.getUnsubscribeCalls()).toBe(1)
})

test('flush publishes an explicit seek immediately and cancels pending work', () => {
  vi.useFakeTimers()
  const source = createController(ready(0))
  const store = createThrottledReplayStore(source.controller, 125)
  const listener = vi.fn()
  store.subscribe(listener)

  source.publish(ready(10))
  source.publish(ready(500))
  store.flush()

  expect(store.getSnapshot().timeMs).toBe(500)
  expect(listener).toHaveBeenCalledOnce()
  vi.advanceTimersByTime(125)
  expect(listener).toHaveBeenCalledOnce()
  store.dispose()
})

test('retains the last replay while an immediate loading snapshot has no sample', () => {
  const replay = Object.freeze({}) as ReplayControllerSnapshot['replay']
  const source = createController({ ...ready(0, false), replay })
  const store = createThrottledReplayStore(source.controller)
  const listener = vi.fn()
  store.subscribe(listener)

  source.publish({ ...ready(1, false), status: 'loading', replay: null })

  expect(store.getSnapshot().replay).toBe(replay)
  expect(listener).toHaveBeenCalledOnce()
  store.dispose()
})
