import type { FormEvent } from 'react'
import type { DnfMarker, LapStart, TimelineInterval, TimelineSummary } from '../../../data/replay/types'
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
  readonly timelineSummary?: TimelineSummary
}

/** Renders playback actions and controller status while delegating seek preview state to the adapter. */
export function PlaybackControls({ controller, currentLap, displayedTimeMs, durationMs, elapsedMs, endMs, isReady, lapStarts, onCommitSeek, onSeek, onSeekPreview, snapshot, startMs, timelineSummary }: PlaybackControlsProps) {
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
          <div className="seek-control__track">
            {timelineSummary !== undefined && <RaceTimeline summary={timelineSummary} displayedTimeMs={displayedTimeMs} startMs={startMs} endMs={endMs} />}
            <input type="range" min={startMs} max={endMs} step="1" value={displayedTimeMs} aria-label="Seek replay" aria-valuetext={formatTime(elapsedMs)} disabled={!isReady} onInput={onSeekPreview} onPointerUp={onCommitSeek} onKeyUp={onCommitSeek} onBlur={onCommitSeek} />
          </div>
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

interface RaceTimelineProps {
  readonly displayedTimeMs: number
  readonly summary: TimelineSummary
  readonly startMs: number
  readonly endMs: number
}

/** Visual context for the native seek control; it deliberately has no seek handlers. */
function RaceTimeline({ displayedTimeMs, summary, startMs, endMs }: RaceTimelineProps) {
  const elapsedWidth = `${timelinePercentage(displayedTimeMs, startMs, endMs)}%`
  return (
    <div className="race-timeline" role="group" aria-label="Race status timeline">
      <span className="race-timeline__elapsed" style={{ width: elapsedWidth }} aria-hidden="true" />
      <span className="race-timeline__remaining" style={{ left: elapsedWidth }} aria-hidden="true" />
      {summary.intervals.map((interval) => <TimelineBand key={`${interval.kind}-${interval.startMs}-${interval.endMs}`} interval={interval} startMs={startMs} endMs={endMs} />)}
      {summary.dnfMarkers.map((marker) => <DnfTimelineMarker key={`${marker.driverId}-${marker.timeMs}`} marker={marker} startMs={startMs} endMs={endMs} />)}
    </div>
  )
}

function TimelineBand({ interval, startMs, endMs }: { readonly interval: TimelineInterval; readonly startMs: number; readonly endMs: number }) {
  const label = `${timelineKindLabel(interval.kind)} from ${formatTime(interval.startMs - startMs)} to ${formatTime(interval.endMs - startMs)}`
  return <span className={`race-timeline__band race-timeline__band--${interval.kind}`} role="img" style={timelineIntervalStyle(interval, startMs, endMs)} aria-label={label} title={label} />
}

function DnfTimelineMarker({ marker, startMs, endMs }: { readonly marker: DnfMarker; readonly startMs: number; readonly endMs: number }) {
  const label = `DNF: ${marker.driverId} at ${formatTime(marker.timeMs - startMs)}`
  return <span className="race-timeline__dnf-marker" role="img" style={{ left: `${timelinePercentage(marker.timeMs, startMs, endMs)}%` }} aria-label={label} title={label} />
}

function timelineIntervalStyle(interval: TimelineInterval, startMs: number, endMs: number) {
  const left = timelinePercentage(interval.startMs, startMs, endMs)
  return { left: `${left}%`, width: `${Math.max(timelinePercentage(interval.endMs, startMs, endMs) - left, 0)}%` }
}

function timelinePercentage(timeMs: number, startMs: number, endMs: number): number {
  if (endMs <= startMs) return 0
  return Math.min(Math.max(((timeMs - startMs) / (endMs - startMs)) * 100, 0), 100)
}

function timelineKindLabel(kind: TimelineInterval['kind']): string {
  return ({ yellow: 'Yellow flag', sc: 'Safety car', red: 'Red flag', vsc: 'Virtual safety car' })[kind]
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
