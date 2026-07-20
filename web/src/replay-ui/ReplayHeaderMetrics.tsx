import type { LapStart } from '../replay-data/types'
import type { CoordinateInterpolationStrategy, ReplayController } from '../replay-engine'
import { ReplayFpsIndicator } from './ReplayFpsIndicator'
import { ExactLapNavigation } from './ExactLapNavigation'
import { ExactTimeEditor } from './ExactTimeEditor'

export interface ReplayHeaderMetricsProps {
  readonly controller: ReplayController
  readonly coordinateInterpolation: CoordinateInterpolationStrategy
  readonly currentLap: number | null
  readonly durationMs: number
  readonly elapsedMs: number
  readonly isReady: boolean
  readonly lapStarts?: readonly LapStart[]
  readonly onSeek: (timeMs: number) => void
  readonly startMs: number
}

/** Composes the replay title and metrics without adding DOM wrappers. */
export function ReplayHeaderMetrics(props: ReplayHeaderMetricsProps) {
  const { controller, coordinateInterpolation, currentLap, durationMs, elapsedMs, isReady, lapStarts, onSeek, startMs } = props
  return (
    <header className="replay-panel__header">
      <div>
        <p className="eyebrow">Diagnostic playback</p>
        <h1 id="replay-panel-title">F1 Race Replay</h1>
      </div>
      <div className="replay-metrics">
        <ExactTimeEditor durationMs={durationMs} elapsedMs={elapsedMs} isReady={isReady} onSeek={onSeek} startMs={startMs} />
        <ExactLapNavigation currentLap={currentLap} isReady={isReady} lapStarts={lapStarts} onSeek={onSeek} />
        <ReplayFpsIndicator controller={controller} />
        <span className="trajectory-mode">Trajectory: {trajectoryLabel(coordinateInterpolation)}</span>
      </div>
    </header>
  )
}

function trajectoryLabel(strategy: CoordinateInterpolationStrategy): string {
  if (strategy === 'smooth') return 'Smooth filter experimental'
  return 'Linear baseline'
}
