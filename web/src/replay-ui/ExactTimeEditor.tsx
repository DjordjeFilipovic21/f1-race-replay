import { useEffect, useRef, useState, type FormEvent } from 'react'

export interface ExactTimeEditorProps {
  readonly durationMs: number
  readonly elapsedMs: number
  readonly isReady: boolean
  readonly onSeek: (timeMs: number) => void
  readonly startMs: number
}

interface TimeParts {
  readonly hours: string
  readonly minutes: string
  readonly seconds: string
  readonly milliseconds: string
}

/** Edits elapsed replay time while retaining invalid input until the user commits it. */
export function ExactTimeEditor({ durationMs, elapsedMs, isReady, onSeek, startMs }: ExactTimeEditorProps) {
  const [timeParts, setTimeParts] = useState(() => splitTime(elapsedMs))
  const [timeError, setTimeError] = useState<string | null>(null)
  const isEditingTime = useRef(false)
  const lastTimeCommit = useRef<string | null>(null)

  useEffect(() => {
    if (!isEditingTime.current) setTimeParts(splitTime(elapsedMs))
  }, [elapsedMs])

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
    onSeek(startMs + elapsed)
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    commitExactTime()
  }

  return (
    <form
      className="replay-time replay-time-editor"
      aria-label="Replay time"
      onSubmit={handleSubmit}
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
  )
}

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

function formatTime(timeMs: number): string {
  const wholeSeconds = Math.floor(timeMs / 1000)
  const hours = Math.floor(wholeSeconds / 3600)
  const minutes = Math.floor(wholeSeconds / 60) % 60
  const seconds = wholeSeconds % 60
  const milliseconds = timeMs % 1000
  return `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}.${milliseconds.toString().padStart(3, '0')}`
}
