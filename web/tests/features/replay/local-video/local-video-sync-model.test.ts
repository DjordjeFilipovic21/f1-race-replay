import { describe, expect, test } from 'vitest'
import {
  adjustLocalVideoSync,
  alignLocalVideoSync,
  createUnsyncedLocalVideoSyncModel,
  mapReplayToVideo,
  mapVideoToReplay,
  resetLocalVideoSync,
} from '../../../../src/features/replay/local-video/local-video-sync-model'

describe('local video sync model', () => {
  test('returns an unsynced result in both mapping directions before alignment', () => {
    const model = createUnsyncedLocalVideoSyncModel()

    expect(model).toStrictEqual({ status: 'unsynced', anchor: null })
    expect(mapReplayToVideo(model, 1_000)).toStrictEqual({ status: 'unsynced' })
    expect(mapVideoToReplay(model, 1_000)).toStrictEqual({ status: 'unsynced' })
  })

  test('maps aligned replay and video anchors in both directions at integer boundaries', () => {
    const model = alignLocalVideoSync(0, 100, 1)
    const bounds = {
      replay: { startMs: 0, endMs: 2 },
      video: { startMs: 100, endMs: 102 },
    }

    expect(mapReplayToVideo(model, 0, bounds)).toStrictEqual({ status: 'mapped', timeMs: 100 })
    expect(mapReplayToVideo(model, 2, bounds)).toStrictEqual({ status: 'mapped', timeMs: 102 })
    expect(mapVideoToReplay(model, 100, bounds)).toStrictEqual({ status: 'mapped', timeMs: 0 })
    expect(mapVideoToReplay(model, 102, bounds)).toStrictEqual({ status: 'mapped', timeMs: 2 })
  })

  test('preserves a negative video offset while mapping with a non-default rate', () => {
    const model = alignLocalVideoSync(5_000, 4_000, 2)

    expect(mapReplayToVideo(model, 4_000)).toStrictEqual({ status: 'mapped', timeMs: 2_000 })
    expect(mapVideoToReplay(model, 2_000)).toStrictEqual({ status: 'mapped', timeMs: 4_000 })
  })

  test('reports source and target range violations with the mapped value', () => {
    const model = alignLocalVideoSync(1_000, 2_000)
    const bounds = {
      replay: { startMs: 0, endMs: 3_000 },
      video: { startMs: 0, endMs: 3_000 },
    }

    expect(mapReplayToVideo(model, 4_000, bounds)).toMatchObject({
      status: 'out-of-range',
      requestedTimeMs: 4_000,
      mappedTimeMs: 5_000,
      bounds: bounds.replay,
      reason: 'source-out-of-range',
    })
    expect(mapReplayToVideo(model, 3_000, bounds)).toMatchObject({
      status: 'out-of-range',
      requestedTimeMs: 3_000,
      mappedTimeMs: 4_000,
      bounds: bounds.video,
      reason: 'target-out-of-range',
    })
  })

  test('reports safe-integer overflow instead of returning an unsafe mapped time', () => {
    const model = alignLocalVideoSync(0, 0, 2)

    expect(mapReplayToVideo(model, Number.MAX_SAFE_INTEGER)).toMatchObject({
      status: 'out-of-range',
      requestedTimeMs: Number.MAX_SAFE_INTEGER,
      mappedTimeMs: null,
      reason: 'overflow',
    })
  })

  test('applies fine adjustments to the video anchor without changing replay time', () => {
    const model = alignLocalVideoSync(2_000, 3_000)

    expect(adjustLocalVideoSync(model, -250)).toStrictEqual({
      status: 'synced',
      anchor: { replayTimeMs: 2_000, videoTimeMs: 2_750, rate: 1 },
    })
  })

  test('leaves an unsynced model unchanged during adjustment and reset', () => {
    const model = createUnsyncedLocalVideoSyncModel()

    expect(adjustLocalVideoSync(model, 100)).toBe(model)
    expect(resetLocalVideoSync()).toStrictEqual({ status: 'unsynced', anchor: null })
  })

  test('rejects fractional millisecond anchors, mapping inputs, and adjustments', () => {
    expect(() => alignLocalVideoSync(1.5, 0)).toThrow(RangeError)
    expect(() => mapReplayToVideo(alignLocalVideoSync(0, 0), 1.5)).toThrow(RangeError)
    expect(() => adjustLocalVideoSync(alignLocalVideoSync(0, 0), 0.5)).toThrow(RangeError)
  })
})
