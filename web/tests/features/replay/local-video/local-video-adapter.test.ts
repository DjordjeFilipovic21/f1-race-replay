import { describe, expect, test } from 'vitest'
import { createLocalVideoAdapter, type LocalVideoEventTarget, type LocalVideoUrlApi } from '../../../../src/features/replay/local-video/local-video-adapter'

describe('local video adapter', () => {
  test('creates, replaces, and revokes object URLs while removing media listeners on disposal', () => {
    const fakeVideo = createFakeVideo({ duration: 10, readyState: 1 })
    const events = createEventTarget()
    const fileA = { name: 'race-a.mp4' } as File
    const fileB = { name: 'race-b.mp4' } as File
    const urls = createUrlApi()
    const adapter = createLocalVideoAdapter(fakeVideo.video, { events, url: urls.api })

    adapter.setFile(fileA)
    adapter.setFile(fileB)
    adapter.dispose()
    adapter.dispose()

    expect(urls.created).toEqual([fileA, fileB])
    expect(urls.revoked).toEqual(['blob:local-1', 'blob:local-2'])
    expect(events.added).toHaveLength(10)
    expect(events.removed).toEqual(events.added)
    expect(events.listenerCount()).toBe(0)
  })

  test('publishes integer millisecond snapshots for metadata, time, rate, and ended events', () => {
    const fakeVideo = createFakeVideo({ currentTime: 1.2346, duration: 12.3456, readyState: 1 })
    const events = createEventTarget()
    const adapter = createLocalVideoAdapter(fakeVideo.video, { events })
    let notifications = 0
    adapter.subscribe(() => { notifications += 1 })

    events.emit('loadedmetadata')
    expect(adapter.getSnapshot()).toMatchObject({ currentTimeMs: 1_235, durationMs: 12_346, metadataReady: true, error: null })

    fakeVideo.state.currentTime = 2.3454
    fakeVideo.state.ended = true
    events.emit('ended')

    expect(adapter.getSnapshot()).toMatchObject({ currentTimeMs: 2_345, isEnded: true, isPlaying: false })
    expect(notifications).toBe(1)
  })

  test('represents missing metadata and unsupported media errors', async () => {
    const fakeVideo = createFakeVideo({ duration: Number.NaN, readyState: 0 })
    const events = createEventTarget()
    const adapter = createLocalVideoAdapter(fakeVideo.video, { events })

    events.emit('loadedmetadata')
    expect(adapter.getSnapshot().error).toMatchObject({ type: 'missing-metadata' })
    await expect(adapter.play()).resolves.toBe(false)
    expect(adapter.getSnapshot().error).toMatchObject({ type: 'missing-metadata' })

    fakeVideo.state.error = { code: 4, message: 'Unsupported container' } as MediaError
    events.emit('error')

    expect(adapter.getSnapshot().error).toMatchObject({ type: 'unsupported-media', code: 4 })
  })

  test('reports a rejected play and clamps integer millisecond seeks to media duration', async () => {
    const fakeVideo = createFakeVideo({ duration: 10, readyState: 1, rejectPlay: true })
    const adapter = createLocalVideoAdapter(fakeVideo.video, { events: createEventTarget() })

    await expect(adapter.play()).resolves.toBe(false)
    expect(adapter.getSnapshot().error).toMatchObject({ type: 'play-rejected' })

    adapter.seek(1_235)
    expect(fakeVideo.state.currentTime).toBe(1.235)
    adapter.seek(20_000)
    expect(fakeVideo.state.currentTime).toBe(10)
    expect(() => adapter.seek(1.5)).toThrow(RangeError)
  })

  test('does not notify subscribers or touch the source after disposal', () => {
    const fakeVideo = createFakeVideo({ duration: 10, readyState: 1 })
    const events = createEventTarget()
    const urls = createUrlApi()
    const adapter = createLocalVideoAdapter(fakeVideo.video, { events, url: urls.api })
    let notifications = 0
    adapter.subscribe(() => { notifications += 1 })
    adapter.setFile({ name: 'race.mp4' } as File)
    const notificationsBeforeDispose = notifications

    adapter.dispose()
    events.emit('timeupdate')
    adapter.setFile({ name: 'ignored.mp4' } as File)

    expect(notifications).toBe(notificationsBeforeDispose)
    expect(urls.created).toHaveLength(1)
    expect(urls.revoked).toEqual(['blob:local-1'])
  })
})

interface FakeVideoState {
  currentTime: number
  duration: number
  readyState: number
  paused: boolean
  ended: boolean
  playbackRate: number
  error: MediaError | null
}

interface FakeVideo {
  readonly video: HTMLVideoElement
  readonly state: FakeVideoState
}

function createFakeVideo(options: Partial<FakeVideoState> & { readonly rejectPlay?: boolean } = {}): FakeVideo {
  const state: FakeVideoState = {
    currentTime: 0,
    duration: Number.NaN,
    readyState: 0,
    paused: true,
    ended: false,
    playbackRate: 1,
    error: null,
    ...options,
  }
  const rejectPlay = options.rejectPlay ?? false
  const video = {
    get currentTime() { return state.currentTime },
    set currentTime(value: number) { state.currentTime = value },
    get duration() { return state.duration },
    get readyState() { return state.readyState },
    get paused() { return state.paused },
    get ended() { return state.ended },
    get playbackRate() { return state.playbackRate },
    set playbackRate(value: number) { state.playbackRate = value },
    get error() { return state.error },
    set src(_value: string) {},
    play: () => rejectPlay
      ? Promise.reject(new Error('autoplay denied'))
      : Promise.resolve().then(() => { state.paused = false }),
    pause: () => { state.paused = true },
    removeAttribute: (_name: string) => undefined,
    load: () => undefined,
  } as unknown as HTMLVideoElement
  return { video, state }
}

function createEventTarget(): LocalVideoEventTarget & { readonly added: readonly string[]; readonly removed: readonly string[]; readonly emit: (type: string) => void; readonly listenerCount: () => number } {
  const listeners = new Map<string, Set<EventListener>>()
  const added: string[] = []
  const removed: string[] = []
  return {
    added,
    removed,
    addEventListener: (type, listener) => {
      added.push(type)
      const typeListeners = listeners.get(type) ?? new Set<EventListener>()
      typeListeners.add(listener)
      listeners.set(type, typeListeners)
    },
    removeEventListener: (type, listener) => {
      removed.push(type)
      listeners.get(type)?.delete(listener)
    },
    emit: (type) => {
      listeners.get(type)?.forEach((listener) => listener({ type } as Event))
    },
    listenerCount: () => [...listeners.values()].reduce((count, typeListeners) => count + typeListeners.size, 0),
  }
}

function createUrlApi(): { readonly api: LocalVideoUrlApi; readonly created: readonly File[]; readonly revoked: readonly string[] } {
  const created: File[] = []
  const revoked: string[] = []
  return {
    created,
    revoked,
    api: {
      createObjectURL: (file) => { created.push(file); return `blob:local-${created.length}` },
      revokeObjectURL: (url) => { revoked.push(url) },
    },
  }
}
