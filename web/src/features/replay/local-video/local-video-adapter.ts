export type LocalVideoErrorType =
  | 'play-rejected'
  | 'unsupported-media'
  | 'missing-metadata'
  | 'media-error'
  | 'object-url-error'
  | 'operation-error'

export interface LocalVideoMediaError {
  readonly type: LocalVideoErrorType
  readonly kind: LocalVideoErrorType
  readonly message: string
  readonly code: number | null
  readonly cause?: unknown
}

export interface LocalVideoAdapterSnapshot {
  readonly currentTimeMs: number
  readonly durationMs: number | null
  readonly metadataReady: boolean
  readonly isPlaying: boolean
  readonly isEnded: boolean
  readonly playbackRate: number
  readonly sourceUrl: string | null
  readonly error: LocalVideoMediaError | null
}

export interface LocalVideoUrlApi {
  readonly createObjectURL: (file: File) => string
  readonly revokeObjectURL: (url: string) => void
}

export interface LocalVideoEventTarget {
  readonly addEventListener: (type: string, listener: EventListener) => void
  readonly removeEventListener: (type: string, listener: EventListener) => void
}

export interface LocalVideoAdapterSeams {
  readonly url?: LocalVideoUrlApi
  readonly urlApi?: LocalVideoUrlApi
  readonly events?: LocalVideoEventTarget
  readonly eventTarget?: LocalVideoEventTarget
}

export interface LocalVideoAdapterOptions extends LocalVideoAdapterSeams {
  readonly video: HTMLVideoElement
}

export interface LocalVideoAdapter {
  readonly getSnapshot: () => LocalVideoAdapterSnapshot
  readonly subscribe: (listener: () => void) => () => void
  readonly setFile: (file: File | null) => void
  readonly play: () => Promise<boolean>
  readonly pause: () => void
  readonly seek: (timeMs: number) => void
  readonly setRate: (rate: number) => void
  readonly dispose: () => void
}

const MEDIA_EVENTS = [
  'loadedmetadata',
  'durationchange',
  'timeupdate',
  'play',
  'playing',
  'pause',
  'ratechange',
  'ended',
  'error',
  'emptied',
] as const

type VideoSource = HTMLVideoElement | LocalVideoAdapterOptions

export function createLocalVideoAdapter(video: HTMLVideoElement, seams?: LocalVideoAdapterSeams): LocalVideoAdapter
export function createLocalVideoAdapter(options: LocalVideoAdapterOptions): LocalVideoAdapter
export function createLocalVideoAdapter(videoOrOptions: VideoSource, seams: LocalVideoAdapterSeams = {}): LocalVideoAdapter {
  const options = isAdapterOptions(videoOrOptions) ? videoOrOptions : { ...seams, video: videoOrOptions }
  const video = options.video
  const eventTarget = options.events ?? options.eventTarget ?? video
  const urlApi = options.url ?? options.urlApi ?? createBrowserUrlApi()
  const listeners = new Set<() => void>()
  let disposed = false
  let sourceUrl: string | null = null
  let error: LocalVideoMediaError | null = null
  let snapshot = readSnapshot(video, sourceUrl, error)

  const publish = (): void => {
    const nextSnapshot = readSnapshot(video, sourceUrl, error)
    if (isSameSnapshot(snapshot, nextSnapshot)) return
    snapshot = nextSnapshot
    listeners.forEach((listener) => listener())
  }

  const setError = (nextError: LocalVideoMediaError | null): void => {
    error = nextError
    publish()
  }

  const handleMediaEvent: EventListener = (event) => {
    if (disposed) return
    if (event.type === 'error') {
      setError(createMediaEventError(video.error))
      return
    }
    if (event.type === 'loadedmetadata' || event.type === 'durationchange') {
      const durationMs = readDurationMs(video.duration)
      setError(durationMs === null ? createError('missing-metadata', 'Video duration is unavailable.') : null)
      return
    }
    publish()
  }

  MEDIA_EVENTS.forEach((eventType) => eventTarget.addEventListener(eventType, handleMediaEvent))

  const revokeSource = (): void => {
    if (sourceUrl === null) return
    const previousUrl = sourceUrl
    sourceUrl = null
    urlApi.revokeObjectURL(previousUrl)
  }

  const clearVideoSource = (): void => {
    try { video.pause() } catch { /* Releasing a source must remain best effort. */ }
    try { video.removeAttribute('src') } catch { /* The injected element may already be detached. */ }
    try { video.load() } catch { /* A detached media element has nothing left to load. */ }
  }

  const setFile = (file: File | null): void => {
    if (disposed) return
    revokeSource()
    clearVideoSource()
    error = null
    if (file === null) {
      publish()
      return
    }
    let nextUrl: string
    try {
      nextUrl = urlApi.createObjectURL(file)
    } catch (cause: unknown) {
      setError(createError('object-url-error', 'The local video could not be opened.', null, cause))
      return
    }
    sourceUrl = nextUrl
    try {
      video.src = nextUrl
      video.load()
      publish()
    } catch (cause: unknown) {
      clearVideoSource()
      revokeSource()
      setError(createError('operation-error', 'The local video could not be loaded.', null, cause))
    }
  }

  const play = async (): Promise<boolean> => {
    if (disposed) return false
    if (readDurationMs(video.duration) === null) {
      setError(createError('missing-metadata', 'Video metadata is not ready.'))
      return false
    }
    try {
      await video.play()
      if (!disposed) {
        error = null
        publish()
      }
      return !disposed
    } catch (cause: unknown) {
      if (!disposed) setError(createError('play-rejected', 'Video playback was rejected.', null, cause))
      return false
    }
  }

  const pause = (): void => {
    if (disposed) return
    try {
      video.pause()
      publish()
    } catch (cause: unknown) {
      setError(createError('operation-error', 'Video playback could not be paused.', null, cause))
    }
  }

  const seek = (timeMs: number): void => {
    if (disposed) return
    assertIntegerMilliseconds(timeMs)
    const durationMs = readDurationMs(video.duration)
    if (durationMs === null) {
      setError(createError('missing-metadata', 'Video metadata is not ready.'))
      return
    }
    const clampedTimeMs = Math.min(durationMs, Math.max(0, timeMs))
    try {
      video.currentTime = clampedTimeMs / 1_000
      publish()
    } catch (cause: unknown) {
      setError(createError('operation-error', 'Video position could not be changed.', null, cause))
    }
  }

  const setRate = (rate: number): void => {
    if (disposed) return
    if (!Number.isFinite(rate) || rate <= 0) throw new RangeError('Video playback rate must be positive and finite')
    try {
      video.playbackRate = rate
      publish()
    } catch (cause: unknown) {
      setError(createError('operation-error', 'Video playback speed could not be changed.', null, cause))
    }
  }

  return Object.freeze({
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      if (disposed) return () => undefined
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    setFile,
    play,
    pause,
    seek,
    setRate,
    dispose: () => {
      if (disposed) return
      disposed = true
      MEDIA_EVENTS.forEach((eventType) => eventTarget.removeEventListener(eventType, handleMediaEvent))
      listeners.clear()
      try {
        clearVideoSource()
      } finally {
        revokeSource()
      }
    },
  })
}

function isAdapterOptions(value: VideoSource): value is LocalVideoAdapterOptions {
  return 'video' in value
}

function createBrowserUrlApi(): LocalVideoUrlApi {
  return {
    createObjectURL: (file) => URL.createObjectURL(file),
    revokeObjectURL: (url) => URL.revokeObjectURL(url),
  }
}

function readSnapshot(video: HTMLVideoElement, sourceUrl: string | null, error: LocalVideoMediaError | null): LocalVideoAdapterSnapshot {
  const durationMs = readDurationMs(video.duration)
  return Object.freeze({
    currentTimeMs: readTimeMs(video.currentTime),
    durationMs,
    metadataReady: video.readyState >= 1 && durationMs !== null,
    isPlaying: !video.paused && !video.ended,
    isEnded: video.ended,
    playbackRate: video.playbackRate,
    sourceUrl,
    error,
  })
}

function readTimeMs(seconds: number): number {
  return Number.isFinite(seconds) && seconds >= 0 ? Math.round(seconds * 1_000) : 0
}

function readDurationMs(seconds: number): number | null {
  return Number.isFinite(seconds) && seconds >= 0 ? Math.round(seconds * 1_000) : null
}

function assertIntegerMilliseconds(timeMs: number): void {
  if (!Number.isSafeInteger(timeMs)) throw new RangeError('Video time must be an integer millisecond')
}

function createError(type: LocalVideoErrorType, message: string, code: number | null = null, cause?: unknown): LocalVideoMediaError {
  return Object.freeze({ type, kind: type, message, code, ...(cause === undefined ? {} : { cause }) })
}

function createMediaEventError(mediaError: MediaError | null): LocalVideoMediaError {
  if (mediaError?.code === 4) return createError('unsupported-media', 'This video format is not supported.', mediaError.code)
  const code = mediaError?.code ?? null
  const message = mediaError === null ? 'The local video could not be played.' : mediaError.message || 'The local video could not be played.'
  return createError('media-error', message, code)
}

function isSameSnapshot(previous: LocalVideoAdapterSnapshot, next: LocalVideoAdapterSnapshot): boolean {
  return previous.currentTimeMs === next.currentTimeMs
    && previous.durationMs === next.durationMs
    && previous.metadataReady === next.metadataReady
    && previous.isPlaying === next.isPlaying
    && previous.isEnded === next.isEnded
    && previous.playbackRate === next.playbackRate
    && previous.sourceUrl === next.sourceUrl
    && previous.error === next.error
}
