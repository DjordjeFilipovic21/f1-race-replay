import type { FormEvent } from 'react'
import type { LapStart } from '../../../data/replay/types'
import type { ReplayController, ReplayControllerSnapshot } from '../../../engine/replay'
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

  const seekBy = (offsetMs: number) => {
    onSeek(Math.min(Math.max(displayedTimeMs + offsetMs, startMs), endMs))
  }

  const lapMarker = (offset: -1 | 1) => lapStarts?.find((entry) => entry.lap === (currentLap ?? 0) + offset)
  const previousLap = lapMarker(-1)
  const nextLap = lapMarker(1)

  return (
    <div className="replay-control-area" aria-busy={snapshot.status === 'loading'}>
      <div className="replay-navigation">
        <ExactTimeEditor durationMs={durationMs} elapsedMs={elapsedMs} isReady={isReady} onSeek={onSeek} startMs={startMs} />
        <ExactLapNavigation currentLap={currentLap} isReady={isReady} lapStarts={lapStarts} onSeek={onSeek} />
      </div>
      <div className="replay-controls">
        <div className="transport-controls" aria-label="Replay transport" role="group">
          <button className="transport-button transport-button--jump" type="button" aria-label="Previous lap" disabled={!isReady || previousLap === undefined} onClick={() => previousLap && onSeek(previousLap.startMs)}>
            <JumpIcon direction="back" label="1L" />
          </button>
          <button className="transport-button" type="button" aria-label="Rewind 10 seconds" disabled={!isReady} onClick={() => seekBy(-10_000)}>
            <JumpIcon direction="back" label="10s" />
          </button>
          <button className="transport-button transport-button--primary" type="button" aria-label={snapshot.isPlaying ? 'Pause' : 'Play'} aria-pressed={snapshot.isPlaying} disabled={!isReady && !snapshot.isPlaying} onClick={handlePlaybackToggle}>
            {snapshot.isPlaying ? <PauseIcon /> : <PlayIcon />}
          </button>
          <button className="transport-button" type="button" aria-label="Forward 10 seconds" disabled={!isReady} onClick={() => seekBy(10_000)}>
            <JumpIcon direction="forward" label="10s" />
          </button>
          <button className="transport-button transport-button--jump" type="button" aria-label="Next lap" disabled={!isReady || nextLap === undefined} onClick={() => nextLap && onSeek(nextLap.startMs)}>
            <JumpIcon direction="forward" label="1L" />
          </button>
        </div>

        <div className="seek-control">
          <input type="range" min={startMs} max={endMs} step="1" value={displayedTimeMs} aria-label="Seek replay" aria-valuetext={formatTime(elapsedMs)} disabled={!isReady} onInput={onSeekPreview} onPointerUp={onCommitSeek} onKeyUp={onCommitSeek} onBlur={onCommitSeek} />
        </div>

        <div className="speed-control">
          <span>Playback speed</span>
          <div className="speed-options" role="group" aria-label="Playback speed">
            {PLAYBACK_SPEEDS.map((speed) => <button key={speed} type="button" aria-pressed={snapshot.speed === speed} disabled={!isReady} onClick={() => controller.setSpeed(speed)}>{speed}×</button>)}
          </div>
        </div>
      </div>

      {snapshot.status === 'error' && <div className="replay-message replay-message--error" role="alert"><p>Replay data could not be loaded: {errorMessage(snapshot.error)}</p><button className="retry-button" type="button" onClick={() => void controller.retry()}>Retry loading</button></div>}
    </div>
  )
}

function PlayIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m8 5 11 7-11 7V5Z" fill="currentColor" /></svg>
}

function PauseIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M7 5h4v14H7zm6 0h4v14h-4z" fill="currentColor" /></svg>
}

function JumpIcon({ direction, label }: { readonly direction: 'back' | 'forward'; readonly label: string }) {
  return <span className={`transport-jump-icon transport-jump-icon--${direction}`} aria-hidden="true"><svg viewBox="0 0 24 24"><path d={direction === 'back' ? 'm15 5-7 7 7 7' : 'm9 5 7 7-7 7'} fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" /></svg><span>{label}</span></span>
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
