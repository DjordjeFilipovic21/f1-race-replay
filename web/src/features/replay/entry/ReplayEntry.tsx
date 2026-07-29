import type { CoordinateInterpolationStrategy } from '../../../engine/replay'
import { ReplayControls } from '../shell/ReplayControls'
import { ReplayErrorBoundary } from '../shell/ReplayErrorBoundary'
import { useReplayEntry } from './useReplayEntry'

export interface ReplayEntryProps {
  readonly browserBaseUrl: string
  readonly browserPointerPath: string
  readonly onChangeRace: () => void
  readonly coordinateInterpolation?: CoordinateInterpolationStrategy
}

export function ReplayEntry({ browserBaseUrl, browserPointerPath, onChangeRace, coordinateInterpolation }: ReplayEntryProps) {
  const { replay, error, retry } = useReplayEntry({ browserBaseUrl, browserPointerPath, coordinateInterpolation })

  return (
    <main className="app-shell">
      <button type="button" onClick={onChangeRace}>Change race</button>
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
