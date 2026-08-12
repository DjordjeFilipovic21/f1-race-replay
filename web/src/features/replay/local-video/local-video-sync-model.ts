export const LOCAL_VIDEO_SYNC_DEFAULT_RATE = 1

export interface LocalVideoSyncRange {
  readonly startMs: number
  readonly endMs: number
}

export interface LocalVideoSyncBounds {
  readonly replay: LocalVideoSyncRange
  readonly video: LocalVideoSyncRange
}

export interface LocalVideoSyncAnchor {
  readonly replayTimeMs: number
  readonly videoTimeMs: number
  readonly rate: number
}

export interface LocalVideoSyncAnchorInput {
  readonly replayTimeMs: number
  readonly videoTimeMs: number
  readonly rate?: number
}

export interface UnsyncedLocalVideoSyncModel {
  readonly status: 'unsynced'
  readonly anchor: null
}

export interface SyncedLocalVideoSyncModel {
  readonly status: 'synced'
  readonly anchor: LocalVideoSyncAnchor
}

export type LocalVideoSyncModel = UnsyncedLocalVideoSyncModel | SyncedLocalVideoSyncModel

export interface LocalVideoSyncMappedResult {
  readonly status: 'mapped'
  readonly timeMs: number
}

export interface LocalVideoSyncOutOfRangeResult {
  readonly status: 'out-of-range'
  readonly requestedTimeMs: number
  readonly mappedTimeMs: number | null
  readonly bounds: LocalVideoSyncRange
  readonly reason: 'source-out-of-range' | 'target-out-of-range' | 'overflow'
}

export interface LocalVideoSyncUnsyncedResult {
  readonly status: 'unsynced'
}

export type LocalVideoSyncMappingResult =
  | LocalVideoSyncMappedResult
  | LocalVideoSyncOutOfRangeResult
  | LocalVideoSyncUnsyncedResult

const UNSYNCED_MODEL: UnsyncedLocalVideoSyncModel = Object.freeze({ status: 'unsynced', anchor: null })
const UNSYNCED_MAPPING: LocalVideoSyncUnsyncedResult = Object.freeze({ status: 'unsynced' })

export function createUnsyncedLocalVideoSyncModel(): UnsyncedLocalVideoSyncModel {
  return UNSYNCED_MODEL
}

export function createLocalVideoSyncModel(anchor?: LocalVideoSyncAnchorInput): LocalVideoSyncModel {
  return anchor === undefined
    ? createUnsyncedLocalVideoSyncModel()
    : alignLocalVideoSync(anchor.replayTimeMs, anchor.videoTimeMs, anchor.rate)
}

export function alignLocalVideoSync(replayTimeMs: number, videoTimeMs: number, rate = LOCAL_VIDEO_SYNC_DEFAULT_RATE): SyncedLocalVideoSyncModel {
  assertIntegerMilliseconds(replayTimeMs, 'Replay anchor time')
  assertIntegerMilliseconds(videoTimeMs, 'Video anchor time')
  assertRate(rate)
  return Object.freeze({
    status: 'synced' as const,
    anchor: Object.freeze({ replayTimeMs, videoTimeMs, rate }),
  })
}

/** Fine adjustment moves the video side of the anchor; the replay anchor is unchanged. */
export function adjustLocalVideoSync(model: LocalVideoSyncModel, deltaMs: number): LocalVideoSyncModel {
  assertIntegerMilliseconds(deltaMs, 'Alignment adjustment')
  if (model.status === 'unsynced') return model
  const videoTimeMs = model.anchor.videoTimeMs + deltaMs
  assertIntegerMilliseconds(videoTimeMs, 'Adjusted video anchor time')
  return alignLocalVideoSync(model.anchor.replayTimeMs, videoTimeMs, model.anchor.rate)
}

export function resetLocalVideoSync(): UnsyncedLocalVideoSyncModel {
  return createUnsyncedLocalVideoSyncModel()
}

export function mapReplayToVideo(model: LocalVideoSyncModel, replayTimeMs: number, bounds?: LocalVideoSyncBounds): LocalVideoSyncMappingResult {
  assertIntegerMilliseconds(replayTimeMs, 'Replay time')
  assertBounds(bounds)
  if (model.status === 'unsynced') return UNSYNCED_MAPPING
  return mapTime(model.anchor, replayTimeMs, bounds?.replay, bounds?.video, (anchor, timeMs) => anchor.videoTimeMs + (timeMs - anchor.replayTimeMs) * anchor.rate)
}

export function mapVideoToReplay(model: LocalVideoSyncModel, videoTimeMs: number, bounds?: LocalVideoSyncBounds): LocalVideoSyncMappingResult {
  assertIntegerMilliseconds(videoTimeMs, 'Video time')
  assertBounds(bounds)
  if (model.status === 'unsynced') return UNSYNCED_MAPPING
  return mapTime(model.anchor, videoTimeMs, bounds?.video, bounds?.replay, (anchor, timeMs) => anchor.replayTimeMs + (timeMs - anchor.videoTimeMs) / anchor.rate)
}

function mapTime(
  anchor: LocalVideoSyncAnchor,
  requestedTimeMs: number,
  sourceBounds: LocalVideoSyncRange | undefined,
  targetBounds: LocalVideoSyncRange | undefined,
  calculateTime: (anchor: LocalVideoSyncAnchor, timeMs: number) => number,
): LocalVideoSyncMappingResult {
  const mappedTimeMs = calculateTime(anchor, requestedTimeMs)
  if (!Number.isSafeInteger(mappedTimeMs)) {
    return createOutOfRangeResult(requestedTimeMs, null, targetBounds ?? { startMs: Number.MIN_SAFE_INTEGER, endMs: Number.MAX_SAFE_INTEGER }, 'overflow')
  }
  if (sourceBounds !== undefined && !isWithinRange(requestedTimeMs, sourceBounds)) {
    return createOutOfRangeResult(requestedTimeMs, mappedTimeMs, sourceBounds, 'source-out-of-range')
  }
  if (targetBounds !== undefined && !isWithinRange(mappedTimeMs, targetBounds)) {
    return createOutOfRangeResult(requestedTimeMs, mappedTimeMs, targetBounds, 'target-out-of-range')
  }
  return Object.freeze({ status: 'mapped' as const, timeMs: mappedTimeMs })
}

function createOutOfRangeResult(
  requestedTimeMs: number,
  mappedTimeMs: number | null,
  bounds: LocalVideoSyncRange,
  reason: LocalVideoSyncOutOfRangeResult['reason'],
): LocalVideoSyncOutOfRangeResult {
  return Object.freeze({ status: 'out-of-range' as const, requestedTimeMs, mappedTimeMs, bounds: Object.freeze({ ...bounds }), reason })
}

function isWithinRange(timeMs: number, bounds: LocalVideoSyncRange): boolean {
  return timeMs >= bounds.startMs && timeMs <= bounds.endMs
}

function assertBounds(bounds: LocalVideoSyncBounds | undefined): void {
  if (bounds === undefined) return
  assertRange(bounds.replay)
  assertRange(bounds.video)
}

function assertRange(bounds: LocalVideoSyncRange): void {
  assertIntegerMilliseconds(bounds.startMs, 'Range start')
  assertIntegerMilliseconds(bounds.endMs, 'Range end')
  if (bounds.startMs > bounds.endMs) throw new RangeError('Range bounds must be ordered')
}

function assertIntegerMilliseconds(value: number, label: string): void {
  if (!Number.isSafeInteger(value)) throw new RangeError(`${label} must be a safe integer millisecond`)
}

function assertRate(rate: number): void {
  if (!Number.isFinite(rate) || rate <= 0) throw new RangeError('Sync rate must be a positive finite number')
}
