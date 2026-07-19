import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { DriverMetadata, LapStart, TrackAssets } from '../replay-data/types'
import type { CoordinateInterpolationStrategy, ReplayController } from '../replay-engine'
import { LiveLeaderboardPanel } from './LiveLeaderboardPanel'
import { LiveTrackMap } from './LiveTrackMap'
import { ReplayFpsIndicator } from './ReplayFpsIndicator'

const PLAYBACK_SPEEDS = [0.25, 0.5, 1, 2, 4] as const

export interface ReplayControlsProps {
  readonly controller: ReplayController
  readonly startMs: number
  readonly endMs: number
  readonly drivers: readonly DriverMetadata[]
  readonly lapStarts?: readonly LapStart[]
  readonly trackAssets: TrackAssets
  readonly coordinateInterpolation?: CoordinateInterpolationStrategy
}

/** A presentational adapter over the controller's cached external store. */
export function ReplayControls({ controller, startMs, endMs, drivers, lapStarts, trackAssets, coordinateInterpolation = 'linear' }: ReplayControlsProps) {
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot)
  const [seekPreviewMs, setSeekPreviewMs] = useState<number | null>(null)
  const [leaderboardRefreshKey, setLeaderboardRefreshKey] = useState(0)
  const seekPreviewRef = useRef<number | null>(null)
  const isReady = snapshot.status === 'ready'
  const displayedTimeMs = seekPreviewMs ?? snapshot.timeMs
  const elapsedMs = relativeElapsedMs(displayedTimeMs, startMs, endMs)
  const durationMs = relativeElapsedMs(endMs, startMs, endMs)
  const currentLap = currentLapNumber(snapshot.replay)
  const [timeParts, setTimeParts] = useState(() => splitTime(elapsedMs))
  const [timeError, setTimeError] = useState<string | null>(null)
  const [lapDraft, setLapDraft] = useState(() => currentLap?.toString() ?? '')
  const [lapError, setLapError] = useState<string | null>(null)
  const isEditingTime = useRef(false)
  const isEditingLap = useRef(false)
  const lastTimeCommit = useRef<string | null>(null)
  const lastLapCommit = useRef<string | null>(null)

  useEffect(() => {
    if (!isEditingTime.current) setTimeParts(splitTime(elapsedMs))
  }, [elapsedMs])

  useEffect(() => {
    if (!isEditingLap.current) setLapDraft(currentLap?.toString() ?? '')
  }, [currentLap])

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
    setLeaderboardRefreshKey((revision) => revision + 1)
  }

  const handleSpeedChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    controller.setSpeed(Number(event.currentTarget.value) as (typeof PLAYBACK_SPEEDS)[number])
  }

  const seek = (timeMs: number) => {
    controller.seek(timeMs)
    setLeaderboardRefreshKey((revision) => revision + 1)
  }

  const commitExactTime = () => {
    const draftKey = `${timeParts.hours}:${timeParts.minutes}:${timeParts.seconds}.${timeParts.milliseconds}`
    if (lastTimeCommit.current === draftKey) return
    const elapsed = parseElapsedParts(timeParts, durationMs)
    if (typeof elapsed === 'string') {
      setTimeError(elapsed)
      return
    }
    setTimeError(null)
    lastTimeCommit.current = draftKey
    seek(startMs + elapsed)
  }

  const handleExactTimeSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    commitExactTime()
  }

  const commitLap = () => {
    if (lastLapCommit.current === lapDraft) return
    const lap = /^\d+$/.test(lapDraft) ? Number(lapDraft) : Number.NaN
    const marker = Number.isSafeInteger(lap) ? lapStarts?.find((entry) => entry.lap === lap) : undefined
    if (!marker) {
      setLapError('Enter an available race lap.')
      return
    }
    setLapError(null)
    lastLapCommit.current = lapDraft
    seek(marker.startMs)
  }

  const handleLapSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    commitLap()
  }

  return (
    <section className="replay-panel" aria-labelledby="replay-panel-title">
      <header className="replay-panel__header">
        <div>
          <p className="eyebrow">Diagnostic playback</p>
          <h1 id="replay-panel-title">F1 Race Replay</h1>
        </div>
        <div className="replay-metrics">
          <form
            className="replay-time replay-time-editor"
            aria-label="Replay time"
            onSubmit={handleExactTimeSubmit}
            onFocus={() => { isEditingTime.current = true }}
            onBlur={(event) => {
              if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
              isEditingTime.current = false
              commitExactTime()
            }}
          >
            <span className="replay-time-fields">
              <input aria-label="Hours" aria-invalid={timeError !== null} aria-describedby={timeError === null ? undefined : 'exact-time-error'} inputMode="numeric" value={timeParts.hours} disabled={!isReady} onChange={(event) => { lastTimeCommit.current = null; setTimeParts({ ...timeParts, hours: event.currentTarget.value }) }} />
              <span aria-hidden="true">:</span>
              <input aria-label="Minutes" aria-invalid={timeError !== null} aria-describedby={timeError === null ? undefined : 'exact-time-error'} inputMode="numeric" value={timeParts.minutes} disabled={!isReady} onChange={(event) => { lastTimeCommit.current = null; setTimeParts({ ...timeParts, minutes: event.currentTarget.value }) }} />
              <span aria-hidden="true">:</span>
              <input aria-label="Seconds" aria-invalid={timeError !== null} aria-describedby={timeError === null ? undefined : 'exact-time-error'} inputMode="numeric" value={timeParts.seconds} disabled={!isReady} onChange={(event) => { lastTimeCommit.current = null; setTimeParts({ ...timeParts, seconds: event.currentTarget.value }) }} />
              <span aria-hidden="true">.</span>
              <input aria-label="Milliseconds" aria-invalid={timeError !== null} aria-describedby={timeError === null ? undefined : 'exact-time-error'} inputMode="numeric" value={timeParts.milliseconds} disabled={!isReady} onChange={(event) => { lastTimeCommit.current = null; setTimeParts({ ...timeParts, milliseconds: event.currentTarget.value }) }} />
            </span>
            <span aria-hidden="true"> / {formatTime(durationMs)}</span>
            {timeError !== null && <span id="exact-time-error" className="replay-inline-error" role="alert">{timeError}</span>}
          </form>
          <form className="replay-lap replay-lap-editor" aria-label="Lap navigation" onSubmit={handleLapSubmit}>
            <label htmlFor="exact-race-lap">Lap</label>
            <input
              id="exact-race-lap"
              aria-label="Current lap"
              aria-describedby={!lapStarts?.length ? 'exact-lap-unavailable' : lapError === null ? undefined : 'exact-lap-error'}
              aria-invalid={lapError !== null}
              inputMode="numeric"
              placeholder="—"
              value={lapDraft}
              disabled={!isReady || !lapStarts?.length}
              onFocus={() => { isEditingLap.current = true }}
              onChange={(event) => { lastLapCommit.current = null; setLapDraft(event.currentTarget.value) }}
              onBlur={() => { isEditingLap.current = false; commitLap() }}
            />
            {lapError !== null && <span id="exact-lap-error" className="replay-inline-error" role="alert">{lapError}</span>}
            {!lapStarts?.length && <span id="exact-lap-unavailable" className="replay-inline-help">Lap seek unavailable</span>}
          </form>
          <ReplayFpsIndicator controller={controller} />
          <span className="trajectory-mode">Trajectory: {trajectoryLabel(coordinateInterpolation)}</span>
        </div>
      </header>

      <div className="replay-workspace">
        <div className="replay-control-area">
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
              <input type="range" min={startMs} max={endMs} step="1" value={displayedTimeMs} aria-valuetext={formatTime(elapsedMs)} disabled={!isReady} onInput={handleSeekPreview} onPointerUp={commitSeek} onKeyUp={commitSeek} onBlur={commitSeek} />
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
        <LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} />
        <LiveLeaderboardPanel controller={controller} drivers={drivers} refreshKey={leaderboardRefreshKey} />
      </div>
    </section>
  )
}

interface TimeParts { readonly hours: string; readonly minutes: string; readonly seconds: string; readonly milliseconds: string }

/** Parse segmented elapsed replay time without clamping malformed user input. */
export function parseElapsedParts(parts: TimeParts, durationMs: number): number | string {
  const values = [parts.hours, parts.minutes, parts.seconds, parts.milliseconds]
  if (values.some((value) => !/^\d+$/.test(value))) return 'Enter numeric hours, minutes, seconds, and milliseconds.'
  const [hours, minutes, seconds, milliseconds] = values.map(Number)
  if (minutes > 59 || seconds > 59 || milliseconds > 999) return 'Minutes and seconds must be 0–59; milliseconds must be 0–999.'
  const elapsedMs = ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds
  if (!Number.isSafeInteger(elapsedMs) || elapsedMs > durationMs) return 'Enter a time within the replay duration.'
  return elapsedMs
}

function splitTime(timeMs: number): TimeParts {
  const wholeSeconds = Math.floor(timeMs / 1000)
  return {
    hours: Math.floor(wholeSeconds / 3600).toString(),
    minutes: (Math.floor(wholeSeconds / 60) % 60).toString().padStart(2, '0'),
    seconds: (wholeSeconds % 60).toString().padStart(2, '0'),
    milliseconds: (timeMs % 1000).toString().padStart(3, '0'),
  }
}

function trajectoryLabel(strategy: CoordinateInterpolationStrategy): string {
  if (strategy === 'smooth') return 'Smooth filter experimental'
  return 'Linear baseline'
}

function formatTime(timeMs: number): string {
  const wholeSeconds = Math.floor(timeMs / 1000)
  const hours = Math.floor(wholeSeconds / 3600)
  const minutes = Math.floor(wholeSeconds / 60) % 60
  const seconds = wholeSeconds % 60
  const milliseconds = timeMs % 1000
  return `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}.${milliseconds.toString().padStart(3, '0')}`
}

function currentLapNumber(replay: ReturnType<ReplayController['getSnapshot']>['replay']): number | null {
  if (replay === null) return null
  const leaderId = replay.leaderboardOrder?.[0]
  const leaderLap = leaderId === undefined ? null : replay.drivers[leaderId]?.lap
  const validLaps = Object.values(replay.drivers)
    .map((driver) => driver.lap)
    .filter((lap): lap is number => typeof lap === 'number' && Number.isInteger(lap) && lap > 0)
  const lap = typeof leaderLap === 'number' && Number.isInteger(leaderLap) && leaderLap > 0
    ? leaderLap
    : Math.max(0, ...validLaps)
  return lap < 1 ? null : lap
}

function relativeElapsedMs(timeMs: number, startMs: number, endMs: number): number {
  return Math.min(Math.max(timeMs - startMs, 0), Math.max(endMs - startMs, 0))
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown error'
}
