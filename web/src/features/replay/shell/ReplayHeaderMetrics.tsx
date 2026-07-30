/** Renders the production replay title. */
export function ReplayHeaderMetrics() {
  return (
    <header className="replay-panel__header">
      <div className="replay-panel__title-block">
        <p className="replay-panel__eyebrow">Replay workspace</p>
        <h1 id="replay-panel-title">F1 Race <span>Replay</span></h1>
      </div>
      <span className="replay-panel__index" aria-hidden="true">01 / Live session</span>
    </header>
  )
}
