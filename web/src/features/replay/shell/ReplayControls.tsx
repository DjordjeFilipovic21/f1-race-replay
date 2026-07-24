import { useRef, useState, useSyncExternalStore, type FormEvent } from 'react'
import type { DriverMetadata, LapStart, TrackAssets } from '../../../data/replay/types'
import type { CoordinateInterpolationStrategy, ReplayController } from '../../../engine/replay'
import { DriverInfoPanel } from '../panels/DriverInfoPanel'
import { DriverTelemetryPanel } from '../panels/DriverTelemetryPanel'
import { LiveLeaderboardPanel } from '../panels/LiveLeaderboardPanel'
import { LiveTrackMap } from '../panels/LiveTrackMap'
import { PlaybackControls } from '../playback/PlaybackControls'
import { ReplayWorkspace, type ReplayWorkspacePanel } from '../workspace/ReplayWorkspace'
import { ReplayHeaderMetrics } from './ReplayHeaderMetrics'

export { parseElapsedParts } from '../playback/ExactTimeEditor'

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
export function ReplayControls({ controller, startMs, endMs, drivers, lapStarts, trackAssets }: ReplayControlsProps) {
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot)
  const [seekPreviewMs, setSeekPreviewMs] = useState<number | null>(null)
  const [leaderboardRefreshKey, setLeaderboardRefreshKey] = useState(0)
  const [explicitSelectedDriverId, setExplicitSelectedDriverId] = useState<string | null>(null)
  const seekPreviewRef = useRef<number | null>(null)
  const isReady = snapshot.status === 'ready'
  const displayedTimeMs = seekPreviewMs ?? snapshot.timeMs
  const elapsedMs = relativeElapsedMs(displayedTimeMs, startMs, endMs)
  const durationMs = relativeElapsedMs(endMs, startMs, endMs)
  const currentLap = currentLapNumber(snapshot.replay)
  const selectedDriverId = selectDriverId(explicitSelectedDriverId, snapshot.replay, drivers)

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

  const panels: readonly ReplayWorkspacePanel[] = [
    {
      id: 'player',
      label: 'Player',
      columns: 1,
      element: <PlaybackControls
        controller={controller}
        currentLap={currentLap}
        displayedTimeMs={displayedTimeMs}
        durationMs={durationMs}
        elapsedMs={elapsedMs}
        endMs={endMs}
        isReady={isReady}
        lapStarts={lapStarts}
        onCommitSeek={commitSeek}
        onSeek={seek}
        onSeekPreview={handleSeekPreview}
        snapshot={snapshot}
        startMs={startMs}
      />,
    },
    {
      id: 'track-map',
      label: 'Track map',
      columns: 2,
      element: <LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} selectedDriverId={selectedDriverId} />,
    },
    {
      id: 'leaderboard',
      label: 'Leaderboard',
      columns: 1,
      element: <LiveLeaderboardPanel controller={controller} drivers={drivers} refreshKey={leaderboardRefreshKey} selectedDriverId={selectedDriverId} onDriverSelect={setExplicitSelectedDriverId} />,
    },
    {
      id: 'driver',
      label: 'Driver',
      columns: 1,
      element: <DriverInfoPanel drivers={drivers} selectedDriverId={selectedDriverId} snapshot={snapshot.replay} />,
    },
    {
      id: 'telemetry',
      label: 'Telemetry',
      columns: 2,
      element: <DriverTelemetryPanel drivers={drivers} selectedDriverId={selectedDriverId} snapshot={snapshot.replay} />,
    },
  ]

  return (
    <section className="replay-panel" aria-labelledby="replay-panel-title">
      <ReplayHeaderMetrics />
      <ReplayWorkspace panels={panels} />
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

export function selectDriverId(explicitSelectedDriverId: string | null, replay: ReturnType<ReplayController['getSnapshot']>['replay'], drivers: readonly DriverMetadata[]): string | null {
  if (explicitSelectedDriverId !== null) return explicitSelectedDriverId
  return replay?.leaderboardOrder?.[0] ?? drivers[0]?.id ?? null
}
