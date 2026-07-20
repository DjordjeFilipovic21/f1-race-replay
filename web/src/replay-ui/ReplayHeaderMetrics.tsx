import type { CoordinateInterpolationStrategy, ReplayController } from '../replay-engine'
import { ReplayFpsIndicator } from './ReplayFpsIndicator'

export interface ReplayHeaderMetricsProps {
  readonly controller: ReplayController
  readonly coordinateInterpolation: CoordinateInterpolationStrategy
}

/** Composes the replay title and metrics without adding DOM wrappers. */
export function ReplayHeaderMetrics(props: ReplayHeaderMetricsProps) {
  const { controller, coordinateInterpolation } = props
  return (
    <header className="replay-panel__header">
      <div>
        <p className="eyebrow">Diagnostic playback</p>
        <h1 id="replay-panel-title">F1 Race Replay</h1>
      </div>
      <div className="replay-metrics">
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
