import type { CoordinateInterpolationStrategy } from '../../../engine/replay'
import { ReplayControls } from '../shell/ReplayControls'
import { ReplayErrorBoundary } from '../shell/ReplayErrorBoundary'
import { useReplayEntry } from './useReplayEntry'

export interface ReplayEntryProps {
  readonly browserBaseUrl: string
  readonly browserPointerPath: string
  readonly onChangeSession: () => void
  readonly coordinateInterpolation?: CoordinateInterpolationStrategy
}

export function ReplayEntry({ browserBaseUrl, browserPointerPath, onChangeSession, coordinateInterpolation }: ReplayEntryProps) {
  const { replay, error, retry } = useReplayEntry({ browserBaseUrl, browserPointerPath, coordinateInterpolation })

  return (
    <main className="app-shell page-transition-surface">
      <div className="app-shell__grid" aria-hidden="true" />
      <nav className="replay-shell__nav" aria-label="Replay navigation">
        <button type="button" className="race-presentation__back replay-shell__back" onClick={onChangeSession}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="m15 18-6-6 6-6" />
          </svg>
          Change session
        </button>
        <span className="replay-shell__context" aria-hidden="true">F1 / Race replay</span>
      </nav>
      {replay !== null && <ReplayErrorBoundary label="Replay workspace"><ReplayControls {...replay} /></ReplayErrorBoundary>}
      {replay === null && error === null && <p className="app-diagnostic" role="status" aria-label="Replay loading">Loading replay data…</p>}
      {error !== null && (
        <section className="app-diagnostic app-diagnostic--error" role="alert" aria-label="Replay loading error">
          <p>Replay data could not be initialized: {error instanceof Error ? error.message : 'Unknown error'}</p>
          <button type="button" onClick={retry}>Retry loading</button>
        </section>
      )}
    </main>
  )
}
