import type { PlaybackSpeed } from '../../../engine/replay/clock'
import type { ReplayController, ReplayControllerSnapshot } from '../../../engine/replay/controller'
import type { LocalVideoAdapter, LocalVideoAdapterSnapshot, LocalVideoMediaError } from './local-video-adapter'
import {
  adjustLocalVideoSync,
  alignLocalVideoSync,
  createUnsyncedLocalVideoSyncModel,
  mapReplayToVideo,
  mapVideoToReplay,
  resetLocalVideoSync,
  type LocalVideoSyncMappingResult,
  type LocalVideoSyncModel,
  type LocalVideoSyncRange,
} from './local-video-sync-model'

export interface LocalVideoSyncCoordinatorOptions {
  readonly controller: ReplayController
  readonly adapter: LocalVideoAdapter
  /** Replay bounds are the only boundary the coordinator is allowed to clamp. */
  readonly replayBounds?: LocalVideoSyncRange
  readonly replayRange?: LocalVideoSyncRange
  readonly replayStartMs?: number
  readonly replayEndMs?: number
}

export type LocalVideoSyncCoordinatorStatus = 'unsynced' | 'synced' | 'out-of-range'

export const LOCAL_VIDEO_DRIFT_IGNORE_MS = 250
export const LOCAL_VIDEO_DRIFT_HARD_SEEK_MS = 750
export const LOCAL_VIDEO_DRIFT_CHECK_INTERVAL_MS = 1_000

export interface LocalVideoSyncCoordinatorSnapshot {
  readonly status: LocalVideoSyncCoordinatorStatus
  readonly syncStatus: LocalVideoSyncCoordinatorStatus
  readonly isLinked: boolean
  readonly model: LocalVideoSyncModel
  readonly mapping: LocalVideoSyncMappingResult
  /** The latest committed native-video to replay mapping result, retained across replay updates. */
  readonly videoMapping: LocalVideoSyncMappingResult
  readonly mappedVideoTimeMs: number | null
  readonly replay: ReplayControllerSnapshot
  readonly video: LocalVideoAdapterSnapshot
  readonly error: LocalVideoMediaError | null
}

export interface LocalVideoSyncCoordinator {
  readonly getSnapshot: () => LocalVideoSyncCoordinatorSnapshot
  readonly subscribe: (listener: () => void) => () => void
  readonly alignCurrent: () => LocalVideoSyncModel
  readonly align: () => LocalVideoSyncModel
  readonly adjustAlignment: (deltaMs: number) => LocalVideoSyncModel
  readonly adjust: (deltaMs: number) => LocalVideoSyncModel
  readonly reset: () => void
  readonly start: () => void
  readonly pause: () => void
  readonly seek: (timeMs: number) => void
  readonly setSpeed: (speed: PlaybackSpeed) => void
  /** Propagates a committed native-video seek; previews must not call this. */
  readonly commitVideoSeek: (timeMs?: number) => LocalVideoSyncMappingResult
  readonly dispose: () => void
}

export function createLocalVideoSyncCoordinator(options: LocalVideoSyncCoordinatorOptions): LocalVideoSyncCoordinator {
  const replayBounds = resolveReplayBounds(options)
  const { controller, adapter } = options
  const listeners = new Set<() => void>()
  let model: LocalVideoSyncModel = createUnsyncedLocalVideoSyncModel()
  let disposed = false
  let playAttempt = 0
  let pendingPlayAttempt: number | null = null
  let expectedVideoChange: ExpectedVideoChange | null = null
  let suppressedNativeSeekMs: number | null = null
  let lastDriftCheckAt: number | null = null
  let previousReplayPlaying = controller.getSnapshot().isPlaying
  let previousReplayStatus = controller.getSnapshot().status
  let previousCommittedSeekRevision = controller.getSnapshot().committedSeekRevision ?? 0
  let latestVideoMapping = getVideoMapping(model, adapter.getSnapshot().currentTimeMs, adapter.getSnapshot(), replayBounds)
  let snapshot = createSnapshot(model, controller.getSnapshot(), adapter.getSnapshot(), replayBounds, latestVideoMapping)

  const publish = (): void => {
    if (disposed) return
    const nextSnapshot = createSnapshot(model, controller.getSnapshot(), adapter.getSnapshot(), replayBounds, latestVideoMapping)
    if (isSameSnapshot(snapshot, nextSnapshot)) return
    snapshot = nextSnapshot
    listeners.forEach((listener) => listener())
  }

  const applyVideoAction = (change: ExpectedVideoChange, action: () => void): boolean => {
    expectedVideoChange = change
    try {
      action()
      if (change.currentTimeMs !== undefined) suppressedNativeSeekMs = change.currentTimeMs
      if (matchesExpectedChange(adapter.getSnapshot(), change)) expectedVideoChange = null
      return true
    } catch {
      expectedVideoChange = null
      return false
    }
  }

  const pauseReplayIfPlaying = (): void => {
    if (controller.getSnapshot().isPlaying) controller.pause()
  }

  const startDriftCheckGate = (): void => {
    lastDriftCheckAt = Date.now()
  }

  const synchronizeReplayToVideo = (explicitReplaySeek = false): void => {
    if (disposed || model.status === 'unsynced') return
    const replay = controller.getSnapshot()
    const video = adapter.getSnapshot()
    if (replay.status !== 'ready') {
      pendingPlayAttempt = null
      playAttempt += 1
      if (video.isPlaying) applyVideoAction({ isPlaying: false }, () => adapter.pause())
      publish()
      return
    }
    const mapping = getReplayMapping(model, replay.timeMs, video, replayBounds)
    if (video.playbackRate !== replay.speed) {
      applyVideoAction({ playbackRate: replay.speed }, () => adapter.setRate(replay.speed))
    }
    if (mapping.status !== 'mapped') {
      pendingPlayAttempt = null
      playAttempt += 1
      applyVideoAction({ isPlaying: false }, () => adapter.pause())
      if (mapping.status === 'out-of-range') pauseReplayIfPlaying()
      publish()
      return
    }

    const bothClocksPlaying = replay.isPlaying && video.isPlaying
    const positionSyncRequired = explicitReplaySeek || !bothClocksPlaying || shouldCheckDrift()
    const driftMs = Math.abs(video.currentTimeMs - mapping.timeMs)
    const driftRequiresHardSeek = driftMs > LOCAL_VIDEO_DRIFT_IGNORE_MS && driftMs >= LOCAL_VIDEO_DRIFT_HARD_SEEK_MS
    if ((explicitReplaySeek || positionSyncRequired) && video.currentTimeMs !== mapping.timeMs && (explicitReplaySeek || !bothClocksPlaying || driftRequiresHardSeek)) {
      applyVideoAction({ currentTimeMs: mapping.timeMs }, () => adapter.seek(mapping.timeMs))
    }

    if (replay.isPlaying) {
      if (!video.isPlaying) startVideoForReplay()
    } else if (video.isPlaying) {
      pendingPlayAttempt = null
      playAttempt += 1
      applyVideoAction({ isPlaying: false }, () => adapter.pause())
    }
    publish()
  }

  const shouldCheckDrift = (): boolean => {
    const now = Date.now()
    if (lastDriftCheckAt !== null && now - lastDriftCheckAt < LOCAL_VIDEO_DRIFT_CHECK_INTERVAL_MS) return false
    lastDriftCheckAt = now
    return true
  }

  const startVideoForReplay = (): void => {
    if (pendingPlayAttempt !== null) return
    const attempt = ++playAttempt
    pendingPlayAttempt = attempt
    const accepted = applyVideoAction({ isPlaying: true }, () => {
      void adapter.play().then(
        (played) => {
          if (disposed || pendingPlayAttempt !== attempt || attempt !== playAttempt) return
          pendingPlayAttempt = null
          expectedVideoChange = null
          if (!played) pauseReplayIfPlaying()
          publish()
        },
        () => {
          if (disposed || pendingPlayAttempt !== attempt || attempt !== playAttempt) return
          pendingPlayAttempt = null
          expectedVideoChange = null
          pauseReplayIfPlaying()
          publish()
        },
      )
    })
    if (!accepted) {
      pendingPlayAttempt = null
      pauseReplayIfPlaying()
    }
  }

  const onControllerChange = (): void => {
    if (disposed) return
    const replay = controller.getSnapshot()
    if (model.status === 'synced' && replay.isPlaying && !previousReplayPlaying) startDriftCheckGate()
    const committedSeekRevision = replay.committedSeekRevision ?? previousCommittedSeekRevision
    const explicitReplaySeek = committedSeekRevision !== previousCommittedSeekRevision
    const readyAfterLoading = replay.status === 'ready' && previousReplayStatus !== 'ready'
    previousReplayPlaying = replay.isPlaying
    previousReplayStatus = replay.status
    previousCommittedSeekRevision = committedSeekRevision
    publish()
    synchronizeReplayToVideo(explicitReplaySeek || readyAfterLoading)
  }

  const onVideoChange = (): void => {
    if (disposed) return
    const video = adapter.getSnapshot()
    const suppressed = (pendingPlayAttempt !== null && video.isPlaying)
      || (expectedVideoChange !== null && matchesExpectedChange(video, expectedVideoChange))
    if (suppressed) {
      expectedVideoChange = null
      publish()
      return
    }

    expectedVideoChange = null
    latestVideoMapping = getVideoMapping(model, video.currentTimeMs, video, replayBounds)
    publish()
    if (video.error !== null || video.isEnded) {
      if (model.status === 'synced') pauseReplayIfPlaying()
      return
    }
    if (model.status === 'unsynced') {
      return
    }

    if (controller.getSnapshot().status !== 'ready') {
      if (video.isPlaying) applyVideoAction({ isPlaying: false }, () => adapter.pause())
      return
    }

    const mapping = getReplayMapping(model, controller.getSnapshot().timeMs, video, replayBounds)
    if (mapping.status !== 'mapped') {
      if (video.isPlaying) applyVideoAction({ isPlaying: false }, () => adapter.pause())
      if (mapping.status === 'out-of-range') pauseReplayIfPlaying()
      publish()
      return
    }
    const replay = controller.getSnapshot()
    if (video.isPlaying === replay.isPlaying) return
    if (video.isPlaying) {
      controller.start()
      if (!controller.getSnapshot().isPlaying) applyVideoAction({ isPlaying: false }, () => adapter.pause())
    } else {
      controller.pause()
    }
  }

  const unsubscribeController = controller.subscribe(onControllerChange)
  const unsubscribeAdapter = adapter.subscribe(onVideoChange)

  const alignCurrent = (): LocalVideoSyncModel => {
    if (disposed) return model
    const video = adapter.getSnapshot()
    if (video.durationMs === null || !video.metadataReady) {
      publish()
      return model
    }
    model = alignLocalVideoSync(controller.getSnapshot().timeMs, video.currentTimeMs)
    latestVideoMapping = getVideoMapping(model, video.currentTimeMs, video, replayBounds)
    startDriftCheckGate()
    publish()
    synchronizeReplayToVideo()
    return model
  }

  const adjustAlignment = (deltaMs: number): LocalVideoSyncModel => {
    if (disposed) return model
    model = adjustLocalVideoSync(model, deltaMs)
    latestVideoMapping = getVideoMapping(model, adapter.getSnapshot().currentTimeMs, adapter.getSnapshot(), replayBounds)
    publish()
    synchronizeReplayToVideo(true)
    return model
  }

  const reset = (): void => {
    if (disposed) return
    model = resetLocalVideoSync()
    pendingPlayAttempt = null
    playAttempt += 1
    expectedVideoChange = null
    suppressedNativeSeekMs = null
    latestVideoMapping = getVideoMapping(model, adapter.getSnapshot().currentTimeMs, adapter.getSnapshot(), replayBounds)
    lastDriftCheckAt = null
    publish()
  }

  const commitVideoSeek = (requestedTimeMs = adapter.getSnapshot().currentTimeMs): LocalVideoSyncMappingResult => {
    if (disposed) return snapshot.videoMapping
    const video = adapter.getSnapshot()
    const mapping = getVideoMapping(model, requestedTimeMs, video, replayBounds)
    latestVideoMapping = mapping
    if (model.status !== 'synced') {
      publish()
      return mapping
    }
    if (suppressedNativeSeekMs === requestedTimeMs) {
      suppressedNativeSeekMs = null
      publish()
      return mapping
    }
    suppressedNativeSeekMs = requestedTimeMs
    if (mapping.status === 'mapped' && controller.getSnapshot().timeMs !== mapping.timeMs) controller.seek(mapping.timeMs)
    else if (mapping.status === 'out-of-range') pauseReplayIfPlaying()
    publish()
    return mapping
  }

  return Object.freeze({
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      if (disposed) return () => undefined
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    alignCurrent,
    align: alignCurrent,
    adjustAlignment,
    adjust: adjustAlignment,
    reset,
    start: () => { if (!disposed && model.status === 'synced') controller.start() },
    pause: () => { if (!disposed && model.status === 'synced') controller.pause() },
    seek: (timeMs: number) => { if (!disposed) controller.seek(timeMs) },
    setSpeed: (speed: PlaybackSpeed) => { if (!disposed && model.status === 'synced') controller.setSpeed(speed) },
    commitVideoSeek,
    dispose: () => {
      if (disposed) return
      disposed = true
      playAttempt += 1
      pendingPlayAttempt = null
      expectedVideoChange = null
      suppressedNativeSeekMs = null
      lastDriftCheckAt = null
      unsubscribeController()
      unsubscribeAdapter()
      try { adapter.pause() } catch { /* Disposal must finish even if an injected adapter is already detached. */ }
      snapshot = createSnapshot(model, controller.getSnapshot(), adapter.getSnapshot(), replayBounds, latestVideoMapping)
      listeners.clear()
    },
  })
}

interface ExpectedVideoChange {
  readonly currentTimeMs?: number
  readonly isPlaying?: boolean
  readonly playbackRate?: number
}

function createSnapshot(model: LocalVideoSyncModel, replay: ReplayControllerSnapshot, video: LocalVideoAdapterSnapshot, replayBounds: LocalVideoSyncRange, videoMapping: LocalVideoSyncMappingResult): LocalVideoSyncCoordinatorSnapshot {
  const mapping = getReplayMapping(model, replay.timeMs, video, replayBounds)
  const status: LocalVideoSyncCoordinatorStatus = mapping.status === 'out-of-range'
    ? 'out-of-range'
    : mapping.status === 'mapped' && model.status === 'synced' ? 'synced' : 'unsynced'
  return Object.freeze({
    status,
    syncStatus: status,
    isLinked: status === 'synced',
    model,
    mapping,
    videoMapping,
    mappedVideoTimeMs: mapping.status === 'mapped' ? mapping.timeMs : null,
    replay,
    video,
    error: video.error,
  })
}

function getReplayMapping(model: LocalVideoSyncModel, timeMs: number, video: LocalVideoAdapterSnapshot, replayBounds: LocalVideoSyncRange): LocalVideoSyncMappingResult {
  if (video.durationMs === null || !video.metadataReady) return { status: 'unsynced' }
  return mapReplayToVideo(model, timeMs, { replay: replayBounds, video: { startMs: 0, endMs: video.durationMs } })
}

function getVideoMapping(model: LocalVideoSyncModel, timeMs: number, video: LocalVideoAdapterSnapshot, replayBounds: LocalVideoSyncRange): LocalVideoSyncMappingResult {
  if (video.durationMs === null || !video.metadataReady) return { status: 'unsynced' }
  return mapVideoToReplay(model, timeMs, { replay: replayBounds, video: { startMs: 0, endMs: video.durationMs } })
}

function matchesExpectedChange(video: LocalVideoAdapterSnapshot, expected: ExpectedVideoChange): boolean {
  return (expected.currentTimeMs === undefined || video.currentTimeMs === expected.currentTimeMs)
    && (expected.isPlaying === undefined || video.isPlaying === expected.isPlaying)
    && (expected.playbackRate === undefined || video.playbackRate === expected.playbackRate)
}

function resolveReplayBounds(options: LocalVideoSyncCoordinatorOptions): LocalVideoSyncRange {
  const bounds = options.replayBounds ?? options.replayRange
  if (bounds !== undefined) return validateReplayBounds(bounds)
  if (options.replayStartMs !== undefined && options.replayEndMs !== undefined) {
    return validateReplayBounds({ startMs: options.replayStartMs, endMs: options.replayEndMs })
  }
  throw new RangeError('Replay bounds are required for local video synchronization')
}

function validateReplayBounds(bounds: LocalVideoSyncRange): LocalVideoSyncRange {
  if (!Number.isSafeInteger(bounds.startMs) || !Number.isSafeInteger(bounds.endMs) || bounds.startMs > bounds.endMs) {
    throw new RangeError('Replay bounds must be ordered integer milliseconds')
  }
  return Object.freeze({ startMs: bounds.startMs, endMs: bounds.endMs })
}

function isSameSnapshot(previous: LocalVideoSyncCoordinatorSnapshot, next: LocalVideoSyncCoordinatorSnapshot): boolean {
  return previous.status === next.status
    && previous.model === next.model
    && isSameMapping(previous.mapping, next.mapping)
    && isSameMapping(previous.videoMapping, next.videoMapping)
    && previous.replay === next.replay
    && previous.video === next.video
}

function isSameMapping(previous: LocalVideoSyncMappingResult, next: LocalVideoSyncMappingResult): boolean {
  if (previous.status !== next.status) return false
  if (previous.status === 'unsynced' || next.status === 'unsynced') return true
  if (previous.status === 'mapped' && next.status === 'mapped') return previous.timeMs === next.timeMs
  if (previous.status !== 'out-of-range' || next.status !== 'out-of-range') return false
  return previous.requestedTimeMs === next.requestedTimeMs
    && previous.mappedTimeMs === next.mappedTimeMs
    && previous.reason === next.reason
    && previous.bounds.startMs === next.bounds.startMs
    && previous.bounds.endMs === next.bounds.endMs
}
