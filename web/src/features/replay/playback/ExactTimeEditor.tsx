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

type TimePartName = keyof TimeParts

/** Edits elapsed replay time while retaining invalid input until the user commits it. */
export function ExactTimeEditor({ durationMs, elapsedMs, isReady, onSeek, startMs }: ExactTimeEditorProps) {
  const [timeParts, setTimeParts] = useState(() => splitTime(elapsedMs))
  const [timeError, setTimeError] = useState<string | null>(null)
  const [editingPart, setEditingPart] = useState<TimePartName | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const timePartsRef = useRef(timeParts)
  const lastCommitRef = useRef<string | null>(null)
  const durationParts = splitTime(durationMs)

  useEffect(() => {
    if (editingPart === null) {
      const nextTimeParts = splitTime(elapsedMs)
      timePartsRef.current = nextTimeParts
      lastCommitRef.current = null
      setTimeParts(nextTimeParts)
    }
  }, [editingPart, elapsedMs])

  useEffect(() => {
    if (editingPart !== null) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [editingPart])

  const commitExactTime = (): boolean => {
    const draftKey = formatTimeParts(timePartsRef.current)
    if (lastCommitRef.current === draftKey) {
      setEditingPart(null)
      return true
    }
    const elapsed = parseElapsedParts(timePartsRef.current, durationMs)
    if (typeof elapsed === 'string') {
      setTimeError(elapsed)
      return false
    }
    setTimeError(null)
    lastCommitRef.current = draftKey
    onSeek(startMs + elapsed)
    setEditingPart(null)
    return true
  }

  const cancelExactTime = () => {
    setTimeError(null)
    const nextTimeParts = splitTime(elapsedMs)
    timePartsRef.current = nextTimeParts
    lastCommitRef.current = null
    setTimeParts(nextTimeParts)
    setEditingPart(null)
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
      onBlur={(event) => {
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
        if (editingPart !== null) commitExactTime()
      }}
    >
      <span className="replay-time-display">
        <span className="replay-time-fields">
          {renderTimePart('hours', 'Hours')}
          <span aria-hidden="true">:</span>
          {renderTimePart('minutes', 'Minutes')}
          <span aria-hidden="true">:</span>
          {renderTimePart('seconds', 'Seconds')}
          <span aria-hidden="true">.</span>
          {renderTimePart('milliseconds', 'Milliseconds')}
        </span>
        <span className="replay-time-duration" aria-hidden="true"><span className="replay-time-separator">/</span><span className="replay-time-duration-hours">{durationParts.hours}</span>:<span className="replay-time-duration-segment">{durationParts.minutes}</span>:<span className="replay-time-duration-segment">{durationParts.seconds}</span></span>
      </span>
      {timeError !== null && <span id="exact-time-error" className="replay-inline-error" role="alert">{timeError}</span>}
    </form>
  )

  function renderTimePart(part: TimePartName, label: string) {
    if (editingPart === part) {
      return <input key={part} ref={inputRef} aria-label={label} aria-invalid={timeError !== null} aria-describedby={timeError === null ? undefined : 'exact-time-error'} inputMode="numeric" value={timeParts[part]} disabled={!isReady} onChange={(event) => { const nextTimeParts = { ...timePartsRef.current, [part]: event.currentTarget.value }; timePartsRef.current = nextTimeParts; lastCommitRef.current = null; setTimeParts(nextTimeParts) }} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); commitExactTime() } else if (event.key === 'Escape') { event.preventDefault(); cancelExactTime() } }} />
    }
    return <button key={part} className={`replay-time-part${part === 'hours' ? ' replay-time-part--hours' : ''}`} type="button" aria-label={`Edit ${label}`} disabled={!isReady} onClick={() => setEditingPart(part)}>{timeParts[part]}</button>
  }
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

function formatTimeParts(parts: TimeParts): string {
  return `${parts.hours}:${parts.minutes}:${parts.seconds}.${parts.milliseconds}`
}
