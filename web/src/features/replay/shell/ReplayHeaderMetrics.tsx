import type { SessionMode } from '../../../data/replay/types'
import { getSessionLabel } from '../session-capabilities'

interface ReplayHeaderMetricsProps {
  readonly sessionMode?: SessionMode
}

/** Renders the production replay title with the truthful session mode. */
export function ReplayHeaderMetrics({ sessionMode = 'race' }: ReplayHeaderMetricsProps) {
  const sessionLabel = getSessionLabel(sessionMode)
  return (
    <header className="replay-panel__header">
      <div className="replay-panel__title-block">
        <p className="replay-panel__eyebrow">Replay workspace</p>
        <h1 id="replay-panel-title">F1 {sessionLabel} <span>Replay</span></h1>
      </div>
      <span className="replay-panel__index" aria-hidden="true">01 / {sessionLabel} session</span>
    </header>
  )
}
