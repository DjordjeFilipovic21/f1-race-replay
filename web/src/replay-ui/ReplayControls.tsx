import { useRef, useState, useSyncExternalStore } from 'react'
import type { ReplayController } from '../replay-engine'

const PLAYBACK_SPEEDS = [0.25, 0.5, 1, 2, 4] as const

export interface ReplayControlsProps {
  readonly controller: ReplayController
  readonly startMs: number
  readonly endMs: number
}

/** A presentational adapter over the controller's cached external store. */
export function ReplayControls({ controller, startMs, endMs }: ReplayControlsProps) {
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot)
  const [seekPreviewMs, setSeekPreviewMs] = useState<number | null>(null)
  const seekPreviewRef = useRef<number | null>(null)
  const isReady = snapshot.status === 'ready'
  const displayedTimeMs = seekPreviewMs ?? snapshot.timeMs
  const elapsedMs = relativeElapsedMs(displayedTimeMs, startMs, endMs)
  const durationMs = relativeElapsedMs(endMs, startMs, endMs)
  const driver = snapshot.replay === null
    ? null
    : Object.entries(snapshot.replay.drivers)[0] ?? null

  const handlePlaybackToggle = () => {
    if (snapshot.isPlaying) controller.pause()
    else controller.start()
  }

  const handleSeekPreview = (event: React.FormEvent<HTMLInputElement>) => {
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
  }

  const handleSpeedChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    controller.setSpeed(Number(event.currentTarget.value) as (typeof PLAYBACK_SPEEDS)[number])
  }

  return (
    <section className="replay-panel" aria-labelledby="replay-panel-title">
      <header className="replay-panel__header">
        <div>
          <p className="eyebrow">Diagnostic playback</p>
          <h1 id="replay-panel-title">F1 Race Replay</h1>
        </div>
        <output className="replay-time" aria-label="Replay time">
          {formatTime(elapsedMs)} / {formatTime(durationMs)}
        </output>
      </header>

      <div className="replay-controls">
        <button
          className="control-button"
          type="button"
          aria-pressed={snapshot.isPlaying}
          disabled={!isReady && !snapshot.isPlaying}
          onClick={handlePlaybackToggle}
        >
          {snapshot.isPlaying ? 'Pause' : 'Play'}
        </button>

        <label className="seek-control">
          <span>Seek replay</span>
          <input
            type="range"
            min={startMs}
            max={endMs}
            step="1"
            value={displayedTimeMs}
            aria-valuetext={formatTime(elapsedMs)}
            disabled={!isReady}
            onInput={handleSeekPreview}
            onPointerUp={commitSeek}
            onKeyUp={commitSeek}
            onBlur={commitSeek}
          />
        </label>

        <label className="speed-control">
          <span>Playback speed</span>
          <select value={snapshot.speed} disabled={!isReady} onChange={handleSpeedChange}>
            {PLAYBACK_SPEEDS.map((speed) => <option key={speed} value={speed}>{speed}×</option>)}
          </select>
        </label>
      </div>

      {snapshot.status === 'loading' && <p className="replay-message" role="status" aria-label="Replay loading">Loading replay samples…</p>}
      {snapshot.status === 'error' && (
        <div className="replay-message replay-message--error" role="alert">
          <p>Replay data could not be loaded: {errorMessage(snapshot.error)}</p>
          <button className="retry-button" type="button" onClick={() => void controller.retry()}>Retry loading</button>
        </div>
      )}
      {isReady && <p className="replay-message" role="status" aria-label="Replay status">Replay samples ready.</p>}

      <aside className="driver-diagnostic" aria-label="Sampled driver diagnostic">
        <h2>Sampled driver</h2>
        {driver === null ? <p>No driver sample available.</p> : (
          <dl>
            <div><dt>Driver</dt><dd>{driver[0]}</dd></div>
            <div><dt>Speed</dt><dd>{formatNumber(driver[1].speed, 'km/h')}</dd></div>
            <div><dt>Gear</dt><dd>{formatNumber(driver[1].gear)}</dd></div>
            <div><dt>Position</dt><dd>{formatNumber(driver[1].position)}</dd></div>
          </dl>
        )}
      </aside>
    </section>
  )
}

function formatTime(timeMs: number): string {
  const wholeSeconds = Math.floor(timeMs / 1000)
  const minutes = Math.floor(wholeSeconds / 60)
  const seconds = wholeSeconds % 60
  const milliseconds = timeMs % 1000
  return `${minutes}:${seconds.toString().padStart(2, '0')}.${milliseconds.toString().padStart(3, '0')}`
}

function relativeElapsedMs(timeMs: number, startMs: number, endMs: number): number {
  return Math.min(Math.max(timeMs - startMs, 0), Math.max(endMs - startMs, 0))
}

function formatNumber(value: number | null, unit = ''): string {
  return value === null ? '—' : `${Math.round(value)}${unit === '' ? '' : ` ${unit}`}`
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown error'
}
