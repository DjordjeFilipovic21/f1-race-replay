import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { createLocalVideoSyncCoordinator } from '../../../../src/features/replay/local-video/local-video-sync-coordinator'
import type { LocalVideoAdapter, LocalVideoAdapterSnapshot } from '../../../../src/features/replay/local-video/local-video-adapter'
import type { ReplayController, ReplayControllerSnapshot } from '../../../../src/engine/replay/controller'
import type { PlaybackSpeed } from '../../../../src/engine/replay/clock'

describe('local video sync coordinator', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  test('propagates replay-authoritative play, pause, seek, and speed changes to video', async () => {
    const controllerFake = createController({ timeMs: 1_000 })
    const adapterFake = createAdapter({ currentTimeMs: 2_000 })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })

    coordinator.alignCurrent()
    controllerFake.controller.start()
    await Promise.resolve()
    controllerFake.controller.pause()
    controllerFake.controller.seek(2_500)
    controllerFake.controller.setSpeed(2)

    expect(adapterFake.calls.play).toBe(1)
    expect(adapterFake.calls.pause).toBe(1)
    expect(adapterFake.calls.seek).toEqual([3_500])
    expect(adapterFake.calls.setRate).toEqual([2])
    expect(coordinator.getSnapshot().video.currentTimeMs).toBe(3_500)
  })

  test('propagates user play and pause, but only a committed video seek reaches replay', () => {
    const controllerFake = createController({ timeMs: 1_000 })
    const adapterFake = createAdapter({ currentTimeMs: 2_000 })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()

    adapterFake.emit({ isPlaying: true })
    adapterFake.emit({ isPlaying: false })
    adapterFake.emit({ currentTimeMs: 2_500 })

    expect(controllerFake.calls.start).toBe(1)
    expect(controllerFake.calls.pause).toBe(1)
    expect(controllerFake.calls.seek).toEqual([])

    expect(coordinator.commitVideoSeek()).toEqual({ status: 'mapped', timeMs: 1_500 })
    expect(controllerFake.calls.seek).toEqual([1_500])
  })

  test('does not seek on normal linked 24fps-like controller ticks', () => {
    const controllerFake = createController({ timeMs: 1_000 })
    const adapterFake = createAdapter({ currentTimeMs: 2_000 })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()
    controllerFake.controller.start()
    adapterFake.emit({ currentTimeMs: 2_500 })

    for (let frame = 1; frame <= 24; frame += 1) {
      const replayTimeMs = 1_000 + frame * 42
      const videoTimeMs = 2_500 + frame * 42
      controllerFake.emit({ timeMs: replayTimeMs })
      adapterFake.emit({ currentTimeMs: videoTimeMs, isPlaying: true })
    }

    expect(adapterFake.calls.seek).toEqual([])
  })

  test('does not seek on a delayed aligned 4x playback tick', () => {
    const controllerFake = createController({ timeMs: 1_000 })
    const adapterFake = createAdapter({ currentTimeMs: 2_000 })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()
    controllerFake.controller.start()
    adapterFake.emit({ isPlaying: true, currentTimeMs: 2_000 })
    controllerFake.controller.setSpeed(4)

    vi.setSystemTime(1_000)
    adapterFake.emit({ currentTimeMs: 4_000, isPlaying: true })
    controllerFake.emit({ timeMs: 3_000, isPlaying: true })

    expect(adapterFake.calls.seek).toEqual([])
  })

  test('immediately syncs a small committed replay seek while playing', () => {
    const controllerFake = createController({ timeMs: 1_000 })
    const adapterFake = createAdapter({ currentTimeMs: 2_000 })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()
    controllerFake.controller.start()
    adapterFake.emit({ isPlaying: true, currentTimeMs: 2_000 })

    controllerFake.controller.seek(1_200)

    expect(adapterFake.calls.seek).toEqual([2_200])
  })

  test('pauses video while replay data loads and seeks once before resuming', () => {
    const controllerFake = createController({ timeMs: 1_000, isPlaying: true })
    const adapterFake = createAdapter({ currentTimeMs: 2_000, isPlaying: true })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()
    adapterFake.calls.seek.length = 0

    controllerFake.emit({ status: 'loading', isPlaying: true })
    expect([adapterFake.calls.pause, controllerFake.controller.getSnapshot().isPlaying]).toEqual([1, true])
    adapterFake.emit({ currentTimeMs: 2_050 })

    controllerFake.emit({ status: 'ready', isPlaying: true })

    expect([adapterFake.calls.seek, adapterFake.calls.play]).toEqual([[2_000], 1])
  })

  test('adjusts alignment with an immediate fine correction while playing', () => {
    const controllerFake = createController({ timeMs: 1_000, isPlaying: true })
    const adapterFake = createAdapter({ currentTimeMs: 2_000, isPlaying: true })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()
    adapterFake.calls.seek.length = 0

    coordinator.adjustAlignment(100)

    expect(adapterFake.calls.seek).toEqual([2_100])
  })

  test('recovers from replay-to-video out-of-range after a valid native seek', () => {
    const controllerFake = createController({ timeMs: 5_000, isPlaying: false })
    const adapterFake = createAdapter({ currentTimeMs: 900, durationMs: 1_000 })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()
    adapterFake.emit({ currentTimeMs: 1_000 })
    const outOfRange = coordinator.commitVideoSeek()
    expect([outOfRange.status, coordinator.getSnapshot().videoMapping.status]).toEqual(['out-of-range', 'out-of-range'])

    adapterFake.emit({ currentTimeMs: 500 })
    const mapping = coordinator.commitVideoSeek()

    expect([mapping, controllerFake.calls.seek, coordinator.getSnapshot().videoMapping]).toEqual([
      { status: 'mapped', timeMs: 4_600 },
      [4_600],
      { status: 'mapped', timeMs: 4_600 },
    ])
  })

  test('hard-seeks drift of 750ms or more only after the one-second check gate', () => {
    const controllerFake = createController({ timeMs: 1_000 })
    const adapterFake = createAdapter({ currentTimeMs: 2_000 })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()
    controllerFake.emit({ isPlaying: true })

    adapterFake.emit({ isPlaying: true, currentTimeMs: 2_800 })
    vi.setSystemTime(999)
    controllerFake.emit({ timeMs: 1_000 })
    expect(adapterFake.calls.seek).toEqual([])

    vi.setSystemTime(1_000)
    controllerFake.emit({ timeMs: 1_000 })

    expect(adapterFake.calls.seek).toEqual([2_000])
  })

  test('hard-seeks drift at 750ms but ignores drift at 749ms when the gate opens', () => {
    const controllerFake = createController({ timeMs: 1_000 })
    const adapterFake = createAdapter({ currentTimeMs: 2_000 })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()
    controllerFake.emit({ isPlaying: true })
    adapterFake.emit({ isPlaying: true, currentTimeMs: 2_749 })

    vi.setSystemTime(1_000)
    controllerFake.emit({ timeMs: 1_000 })
    expect(adapterFake.calls.seek).toEqual([])

    vi.setSystemTime(2_000)
    adapterFake.emit({ currentTimeMs: 2_750 })
    controllerFake.emit({ timeMs: 1_000 })

    expect(adapterFake.calls.seek).toEqual([2_000])
  })

  test('does not infer a seek from a raw time delta while playing', () => {
    const controllerFake = createController({ timeMs: 1_000 })
    const adapterFake = createAdapter({ currentTimeMs: 2_000 })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()

    controllerFake.controller.seek(2_000)
    expect(adapterFake.calls.seek).toEqual([3_000])

    controllerFake.controller.start()
    adapterFake.emit({ isPlaying: true, currentTimeMs: 3_000 })
    controllerFake.emit({ timeMs: 3_000 })
    expect(adapterFake.calls.seek).toEqual([3_000])
  })

  test('consumes a delayed programmatic native seek after an intervening controller tick', () => {
    const controllerFake = createController({ timeMs: 1_000, isPlaying: true })
    const adapterFake = createAdapter({ currentTimeMs: 2_000, isPlaying: true })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()

    coordinator.seek(2_000)
    controllerFake.emit({ timeMs: 2_042 })
    adapterFake.emit({ currentTimeMs: 3_000 })

    coordinator.commitVideoSeek()

    expect(controllerFake.calls.seek).toEqual([2_000])
    expect(controllerFake.controller.getSnapshot().timeMs).toBe(2_042)
  })

  test('suppresses programmatic video changes and repeated events without echo loops', () => {
    const controllerFake = createController({ timeMs: 1_000 })
    const adapterFake = createAdapter({ currentTimeMs: 2_000 })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()

    coordinator.seek(2_000)
    adapterFake.emit({ currentTimeMs: 3_000 })
    coordinator.commitVideoSeek()
    adapterFake.emit({ currentTimeMs: 3_500 })
    coordinator.commitVideoSeek()

    expect(controllerFake.calls.seek).toEqual([2_000, 2_500])
    expect(adapterFake.calls.seek).toEqual([3_000])
  })

  test('keeps unsynced video play, pause, and seek standalone', () => {
    const controllerFake = createController()
    const adapterFake = createAdapter()
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })

    adapterFake.emit({ isPlaying: true })
    adapterFake.emit({ isPlaying: false, currentTimeMs: 3_000 })
    coordinator.commitVideoSeek()

    expect(controllerFake.calls.start).toBe(0)
    expect(controllerFake.calls.pause).toBe(0)
    expect(controllerFake.calls.seek).toEqual([])
  })

  test('keeps unloaded video unsynced and does not start it from replay controls', () => {
    const controllerFake = createController({ timeMs: 1_000 })
    const adapterFake = createAdapter({ durationMs: null, metadataReady: false })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })

    coordinator.alignCurrent()
    coordinator.start()

    expect(coordinator.getSnapshot()).toMatchObject({ status: 'unsynced', isLinked: false, mapping: { status: 'unsynced' } })
    expect(adapterFake.calls.play).toBe(0)
  })

  test('pauses replay when video reports an error or reaches its ended state', () => {
    const controllerFake = createController({ timeMs: 1_000 })
    const adapterFake = createAdapter({ currentTimeMs: 2_000 })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()
    coordinator.start()

    adapterFake.emit({ error: { type: 'unsupported-media', kind: 'unsupported-media', message: 'Unsupported', code: 4 }, isPlaying: false })
    expect(controllerFake.controller.getSnapshot().isPlaying).toBe(false)

    coordinator.start()
    adapterFake.emit({ error: null, isEnded: true, isPlaying: false })

    expect(controllerFake.controller.getSnapshot().isPlaying).toBe(false)
  })

  test('reports out-of-range mapping and pauses both clocks', () => {
    const controllerFake = createController({ timeMs: 4_000 })
    const adapterFake = createAdapter({ currentTimeMs: 900, durationMs: 1_000 })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()
    coordinator.start()
    controllerFake.controller.seek(5_000)

    expect(coordinator.getSnapshot()).toMatchObject({ status: 'out-of-range', isLinked: false, mapping: { status: 'out-of-range', reason: 'target-out-of-range' } })
    expect(controllerFake.controller.getSnapshot().isPlaying).toBe(false)
    expect(adapterFake.adapter.getSnapshot().isPlaying).toBe(false)
  })

  test('resets alignment to unsynced without making the video control replay', () => {
    const controllerFake = createController({ timeMs: 1_000 })
    const adapterFake = createAdapter({ currentTimeMs: 2_000 })
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })
    coordinator.alignCurrent()
    coordinator.start()
    const startsBeforeReset = controllerFake.calls.start
    const pausesBeforeReset = controllerFake.calls.pause
    coordinator.reset()

    expect(coordinator.getSnapshot()).toMatchObject({ status: 'unsynced', syncStatus: 'unsynced', isLinked: false, model: { status: 'unsynced', anchor: null } })
    adapterFake.emit({ isPlaying: false })
    expect(controllerFake.calls.pause).toBe(pausesBeforeReset)
    adapterFake.emit({ isPlaying: true })
    expect(controllerFake.calls.start).toBe(startsBeforeReset)
  })

  test('unsubscribes from controller and adapter on disposal', () => {
    const controllerFake = createController()
    const adapterFake = createAdapter()
    const coordinator = createLocalVideoSyncCoordinator({ controller: controllerFake.controller, adapter: adapterFake.adapter, replayBounds: { startMs: 0, endMs: 5_000 } })

    coordinator.dispose()
    coordinator.dispose()
    controllerFake.emit({ timeMs: 2_000 })
    adapterFake.emit({ isPlaying: true })

    expect([controllerFake.listenerCount(), adapterFake.listenerCount(), controllerFake.unsubscribes, adapterFake.unsubscribes]).toEqual([0, 0, 1, 1])
    expect(adapterFake.calls.play).toBe(0)
  })
})

interface ControllerFake {
  readonly controller: ReplayController
  readonly calls: { readonly start: number; readonly pause: number; readonly seek: number[]; readonly setSpeed: PlaybackSpeed[] }
  readonly emit: (changes: Partial<ReplayControllerSnapshot>) => void
  readonly listenerCount: () => number
  readonly unsubscribes: number
}

function createController(initial: Partial<ReplayControllerSnapshot> = {}): ControllerFake {
  let snapshot: ReplayControllerSnapshot = Object.freeze({
    status: 'ready',
    timeMs: 1_000,
    speed: 1,
    isPlaying: false,
    committedSeekRevision: 0,
    replay: null,
    crossedEvents: Object.freeze([]),
    error: null,
    ...initial,
  })
  const listeners = new Set<() => void>()
  const calls = { start: 0, pause: 0, seek: [] as number[], setSpeed: [] as PlaybackSpeed[] }
  let unsubscribes = 0
  const notify = (): void => { listeners.forEach((listener) => listener()) }
  const emit = (changes: Partial<ReplayControllerSnapshot>): void => {
    snapshot = Object.freeze({ ...snapshot, ...changes })
    notify()
  }
  const controller: ReplayController = {
    getSnapshot: () => snapshot,
    subscribe: (listener) => {
      listeners.add(listener)
      return () => { if (listeners.delete(listener)) unsubscribes += 1 }
    },
    start: () => { calls.start += 1; emit({ isPlaying: true }) },
    pause: () => { calls.pause += 1; emit({ isPlaying: false }) },
    seek: (timeMs) => { calls.seek.push(timeMs); emit({ timeMs, committedSeekRevision: (snapshot.committedSeekRevision ?? 0) + 1 }) },
    setSpeed: (speed) => { calls.setSpeed.push(speed); emit({ speed }) },
    retry: async () => undefined,
    dispose: () => undefined,
  }
  return { controller, calls, emit, listenerCount: () => listeners.size, get unsubscribes() { return unsubscribes } }
}

interface AdapterFake {
  readonly adapter: LocalVideoAdapter
  readonly calls: { readonly play: number; readonly pause: number; readonly seek: number[]; readonly setRate: number[] }
  readonly emit: (changes: Partial<LocalVideoAdapterSnapshot>) => void
  readonly listenerCount: () => number
  readonly unsubscribes: number
}

function createAdapter(initial: Partial<LocalVideoAdapterSnapshot> = {}): AdapterFake {
  let snapshot: LocalVideoAdapterSnapshot = Object.freeze({
    currentTimeMs: 2_000,
    durationMs: 10_000,
    metadataReady: true,
    isPlaying: false,
    isEnded: false,
    playbackRate: 1,
    sourceUrl: 'blob:local-video',
    error: null,
    ...initial,
  })
  const listeners = new Set<() => void>()
  const calls = { play: 0, pause: 0, seek: [] as number[], setRate: [] as number[] }
  let playResult: Promise<boolean> = Promise.resolve(true)
  let unsubscribes = 0
  const notify = (): void => { listeners.forEach((listener) => listener()) }
  const emit = (changes: Partial<LocalVideoAdapterSnapshot>): void => {
    snapshot = Object.freeze({ ...snapshot, ...changes })
    notify()
  }
  const adapter: LocalVideoAdapter = {
    getSnapshot: () => snapshot,
    subscribe: (listener) => {
      listeners.add(listener)
      return () => { if (listeners.delete(listener)) unsubscribes += 1 }
    },
    setFile: () => undefined,
    play: () => { calls.play += 1; emit({ isPlaying: true }); return playResult },
    pause: () => { calls.pause += 1; emit({ isPlaying: false }) },
    seek: (timeMs) => { calls.seek.push(timeMs); emit({ currentTimeMs: timeMs }) },
    setRate: (rate) => { calls.setRate.push(rate); emit({ playbackRate: rate }) },
    dispose: () => undefined,
  }
  return {
    adapter,
    calls,
    emit,
    listenerCount: () => listeners.size,
    get unsubscribes() { return unsubscribes },
  }
}
