import { memo, useEffect, useState } from 'react'
import type { ReplayController } from '../../../engine/replay'
import { TARGET_PLAYBACK_FPS } from '../../../engine/replay/clock'

export interface ReplayFpsIndicatorProps {
  readonly controller: ReplayController
  readonly now?: () => number
}

interface FrameMetrics {
  readonly fps: number
  readonly maxFrameMs: number
  readonly droppedFrames: number
}

/** Reports controller-driven visual update cadence without creating another animation loop. */
export const ReplayFpsIndicator = memo(function ReplayFpsIndicator({ controller, now = defaultNow }: ReplayFpsIndicatorProps) {
  const [metrics, setMetrics] = useState<FrameMetrics | null>(null)

  useEffect(() => {
    let wasPlaying = controller.getSnapshot().isPlaying
    let windowStartedAt = now()
    let previousFrameAt = windowStartedAt
    let frames = 0
    let maxFrameMs = 0
    let droppedFrames = 0
    return controller.subscribe(() => {
      const isPlaying = controller.getSnapshot().isPlaying
      const frameAt = now()
      if (!isPlaying) {
        frames = 0
        windowStartedAt = frameAt
        previousFrameAt = frameAt
        maxFrameMs = 0
        droppedFrames = 0
        if (wasPlaying) setMetrics(null)
        wasPlaying = false
        return
      }
      if (!wasPlaying) {
        frames = 0
        windowStartedAt = frameAt
        previousFrameAt = frameAt
        maxFrameMs = 0
        droppedFrames = 0
        wasPlaying = true
        return
      }
      const frameDurationMs = Math.max(0, frameAt - previousFrameAt)
      previousFrameAt = frameAt
      maxFrameMs = Math.max(maxFrameMs, frameDurationMs)
      droppedFrames += Math.max(0, Math.round(frameDurationMs / (1_000 / TARGET_PLAYBACK_FPS)) - 1)
      frames += 1
      const elapsedMs = frameAt - windowStartedAt
      if (elapsedMs < 1_000) return
      setMetrics({ fps: Math.round(frames * 1_000 / elapsedMs), maxFrameMs: Math.round(maxFrameMs), droppedFrames })
      frames = 0
      windowStartedAt = frameAt
      maxFrameMs = 0
      droppedFrames = 0
    })
  }, [controller, now])

  return <output className="replay-fps" aria-label="Replay frame rate">{metrics === null ? '— FPS' : `${metrics.fps} FPS · max ${metrics.maxFrameMs} ms · ${metrics.droppedFrames} dropped`}</output>
})

function defaultNow(): number {
  return globalThis.performance.now()
}
