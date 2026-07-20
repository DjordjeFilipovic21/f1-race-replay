import type { ChangeEvent, FormEvent } from 'react'
import type { LapStart } from '../replay-data/types'
import type { ReplayController, ReplayControllerSnapshot } from '../replay-engine'
import { ExactLapNavigation } from './ExactLapNavigation'
import { ExactTimeEditor } from './ExactTimeEditor'

const PLAYBACK_SPEEDS = [0.25, 0.5, 1, 2, 4] as const

export interface PlaybackControlsProps {
  readonly controller: ReplayController
  readonly currentLap: number | null
  readonly displayedTimeMs: number
  readonly durationMs: number
  readonly elapsedMs: number
  readonly endMs: number
  readonly isReady: boolean
  readonly lapStarts?: readonly LapStart[]
  readonly onCommitSeek: () => void
  readonly onSeek: (timeMs: number) => void
  readonly onSeekPreview: (event: FormEvent<HTMLInputElement>) => void
  readonly snapshot: ReplayControllerSnapshot
  readonly startMs: number
}

/** Renders playback actions and controller status while delegating seek preview state to the adapter. */
export function PlaybackControls({ controller, currentLap, displayedTimeMs, durationMs, elapsedMs, endMs, isReady, lapStarts, onCommitSeek, onSeek, onSeekPreview, snapshot, startMs }: PlaybackControlsProps) {
  const handlePlaybackToggle = () => {
    if (snapshot.isPlaying) controller.pause()
    else controller.start()
  }

  const handleSpeedChange = (event: ChangeEvent<HTMLSelectElement>) => {
    controller.setSpeed(Number(event.currentTarget.value) as (typeof PLAYBACK_SPEEDS)[number])
  }

  return (
    <div className="replay-control-area">
      <div className="replay-navigation">
        <ExactTimeEditor durationMs={durationMs} elapsedMs={elapsedMs} isReady={isReady} onSeek={onSeek} startMs={startMs} />
        <ExactLapNavigation currentLap={currentLap} isReady={isReady} lapStarts={lapStarts} onSeek={onSeek} />
      </div>
      <div className="replay-controls">
        <button className="control-button" type="button" aria-pressed={snapshot.isPlaying} disabled={!isReady && !snapshot.isPlaying} onClick={handlePlaybackToggle}>
          {snapshot.isPlaying ? 'Pause' : 'Play'}
        </button>

        <label className="seek-control">
          <span>Seek replay</span>
          <input type="range" min={startMs} max={endMs} step="1" value={displayedTimeMs} aria-valuetext={formatTime(elapsedMs)} disabled={!isReady} onInput={onSeekPreview} onPointerUp={onCommitSeek} onKeyUp={onCommitSeek} onBlur={onCommitSeek} />
        </label>

        <label className="speed-control">
          <span>Playback speed</span>
          <select value={snapshot.speed} disabled={!isReady} onChange={handleSpeedChange}>
            {PLAYBACK_SPEEDS.map((speed) => <option key={speed} value={speed}>{speed}×</option>)}
          </select>
        </label>
      </div>

      {snapshot.status === 'loading' && <p className="replay-message" role="status" aria-label="Replay loading">Loading replay samples…</p>}
      {snapshot.status === 'error' && <div className="replay-message replay-message--error" role="alert"><p>Replay data could not be loaded: {errorMessage(snapshot.error)}</p><button className="retry-button" type="button" onClick={() => void controller.retry()}>Retry loading</button></div>}
      {isReady && <p className="replay-message" role="status" aria-label="Replay status">Replay samples ready.</p>}
    </div>
  )
}

function formatTime(timeMs: number): string {
  const wholeSeconds = Math.floor(timeMs / 1000)
  const hours = Math.floor(wholeSeconds / 3600)
  const minutes = Math.floor(wholeSeconds / 60) % 60
  const seconds = wholeSeconds % 60
  const milliseconds = timeMs % 1000
  return `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}.${milliseconds.toString().padStart(3, '0')}`
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown error'
}
