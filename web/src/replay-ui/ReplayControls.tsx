import { useRef, useState, useSyncExternalStore, type FormEvent } from 'react'
import type { DriverMetadata, LapStart, TrackAssets } from '../replay-data/types'
import type { CoordinateInterpolationStrategy, ReplayController } from '../replay-engine'
import { LiveLeaderboardPanel } from './LiveLeaderboardPanel'
import { LiveTrackMap } from './LiveTrackMap'
import { PlaybackControls } from './PlaybackControls'
import { ReplayHeaderMetrics } from './ReplayHeaderMetrics'

export { parseElapsedParts } from './ExactTimeEditor'

export interface ReplayControlsProps {
  readonly controller: ReplayController
  readonly startMs: number
  readonly endMs: number
  readonly drivers: readonly DriverMetadata[]
  readonly lapStarts?: readonly LapStart[]
  readonly trackAssets: TrackAssets
  readonly coordinateInterpolation?: CoordinateInterpolationStrategy
}

/** A presentational adapter over the controller's cached external store. */
export function ReplayControls({ controller, startMs, endMs, drivers, lapStarts, trackAssets, coordinateInterpolation = 'linear' }: ReplayControlsProps) {
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot)
  const [seekPreviewMs, setSeekPreviewMs] = useState<number | null>(null)
  const [leaderboardRefreshKey, setLeaderboardRefreshKey] = useState(0)
  const seekPreviewRef = useRef<number | null>(null)
  const isReady = snapshot.status === 'ready'
  const displayedTimeMs = seekPreviewMs ?? snapshot.timeMs
  const elapsedMs = relativeElapsedMs(displayedTimeMs, startMs, endMs)
  const durationMs = relativeElapsedMs(endMs, startMs, endMs)
  const currentLap = currentLapNumber(snapshot.replay)

  const handleSeekPreview = (event: FormEvent<HTMLInputElement>) => {
    const value = event.currentTarget.valueAsNumber
    seekPreviewRef.current = value
    setSeekPreviewMs(value)
  }

  const commitSeek = () => {
    const value = seekPreviewRef.current
    if (value === null) return
    seekPreviewRef.current = null
    setSeekPreviewMs(null)
    controller.seek(value)
    setLeaderboardRefreshKey((revision) => revision + 1)
  }

  const seek = (timeMs: number) => {
    controller.seek(timeMs)
    setLeaderboardRefreshKey((revision) => revision + 1)
  }

  return (
    <section className="replay-panel" aria-labelledby="replay-panel-title">
      <ReplayHeaderMetrics
        controller={controller}
        coordinateInterpolation={coordinateInterpolation}
        currentLap={currentLap}
        durationMs={durationMs}
        elapsedMs={elapsedMs}
        isReady={isReady}
        lapStarts={lapStarts}
        onSeek={seek}
        startMs={startMs}
      />

      <div className="replay-workspace">
        <PlaybackControls
          controller={controller}
          displayedTimeMs={displayedTimeMs}
          elapsedMs={elapsedMs}
          endMs={endMs}
          isReady={isReady}
          onCommitSeek={commitSeek}
          onSeekPreview={handleSeekPreview}
          snapshot={snapshot}
          startMs={startMs}
        />
        <LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} />
        <LiveLeaderboardPanel controller={controller} drivers={drivers} refreshKey={leaderboardRefreshKey} />
      </div>
    </section>
  )
}

function currentLapNumber(replay: ReturnType<ReplayController['getSnapshot']>['replay']): number | null {
  if (replay === null) return null
  const leaderId = replay.leaderboardOrder?.[0]
  const leaderLap = leaderId === undefined ? null : replay.drivers[leaderId]?.lap
  const validLaps = Object.values(replay.drivers)
    .map((driver) => driver.lap)
    .filter((lap): lap is number => typeof lap === 'number' && Number.isInteger(lap) && lap > 0)
  const lap = typeof leaderLap === 'number' && Number.isInteger(leaderLap) && leaderLap > 0
    ? leaderLap
    : Math.max(0, ...validLaps)
  return lap < 1 ? null : lap
}

function relativeElapsedMs(timeMs: number, startMs: number, endMs: number): number {
  return Math.min(Math.max(timeMs - startMs, 0), Math.max(endMs - startMs, 0))
}
