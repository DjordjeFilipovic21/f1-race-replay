/** Renders the production replay title. */
export function ReplayHeaderMetrics() {
  return (
    <header className="replay-panel__header">
      <div className="replay-panel__title-block">
        <p className="replay-panel__eyebrow">Replay workspace</p>
        <h1 id="replay-panel-title">F1 Race Replay</h1>
      </div>
      <p className="replay-panel__status">Interactive race data</p>
    </header>
  )
}
