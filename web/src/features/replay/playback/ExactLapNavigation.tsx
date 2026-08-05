import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { LapStart, QualifyingPhase, QualifyingPhaseBoundary } from '../../../data/replay/types'

export interface ExactLapNavigationProps {
  readonly currentLap: number | null
  readonly isReady: boolean
  readonly lapStarts?: readonly LapStart[]
  readonly currentPhase?: QualifyingPhase | null
  readonly phaseBoundaries?: readonly QualifyingPhaseBoundary[]
  readonly onSeek: (timeMs: number) => void
  readonly sessionLabel?: string
}

/** Navigates to an indexed lap and preserves its draft until focus leaves the field. */
export function ExactLapNavigation({ currentLap, isReady, lapStarts, currentPhase = null, phaseBoundaries, onSeek, sessionLabel = 'Race' }: ExactLapNavigationProps) {
  const isQualifying = phaseBoundaries !== undefined
  const currentValue = isQualifying ? qualifyingNumber(currentPhase) : currentLap?.toString() ?? ''
  const [lapDraft, setLapDraft] = useState(currentValue)
  const [lapError, setLapError] = useState<string | null>(null)
  const [isEditingLap, setIsEditingLap] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const lastLapCommit = useRef<string | null>(null)
  const finalLap = isQualifying ? 'Q3' : lapStarts?.reduce((maximum, entry) => Math.max(maximum, entry.lap), 0) || null
  const markers = isQualifying ? phaseBoundaries : lapStarts
  const hasMarkers = markers !== undefined && markers.length > 0

  useEffect(() => {
    if (!isEditingLap) setLapDraft(currentValue)
  }, [currentValue, isEditingLap])

  useEffect(() => {
    if (isEditingLap) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [isEditingLap])

  const commitLap = (): boolean => {
    if (lastLapCommit.current === lapDraft) {
      setIsEditingLap(false)
      return true
    }
    const number = /^\d+$/.test(lapDraft) ? Number(lapDraft) : Number.NaN
    const qualifyingMarker = isQualifying && Number.isSafeInteger(number)
      ? phaseBoundaries?.find((entry) => entry.phase === `Q${number}`)
      : undefined
    const lapMarker = !isQualifying && Number.isSafeInteger(number)
      ? lapStarts?.find((entry) => entry.lap === number)
      : undefined
    const marker = qualifyingMarker ?? lapMarker
    if (!marker) {
      setLapError(isQualifying ? 'Enter an available qualifying phase.' : `Enter an available ${sessionLabel.toLowerCase()} lap.`)
      setIsEditingLap(false)
      return false
    }
    setLapError(null)
    const selectedValue = isQualifying ? qualifyingMarker?.phase : number.toString()
    if (selectedValue === (isQualifying ? currentPhase : currentLap?.toString())) {
      lastLapCommit.current = lapDraft
      setIsEditingLap(false)
      return true
    }
    lastLapCommit.current = lapDraft
    onSeek(marker.startMs)
    setIsEditingLap(false)
    return true
  }

  const cancelLap = () => {
    setLapError(null)
    lastLapCommit.current = null
    setLapDraft(currentValue)
    setIsEditingLap(false)
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    commitLap()
  }

  return (
    <form className="replay-lap replay-lap-editor" aria-label={isQualifying ? 'Qualifying phase navigation' : 'Lap navigation'} onSubmit={handleSubmit} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null) && isEditingLap) commitLap() }}>
      <span className={`replay-lap-display${isQualifying ? ' replay-lap-display--qualifying' : ''}`}>
        {!isQualifying && <span>Lap</span>}
        {isEditingLap ? (
          <input
            ref={inputRef}
            id={isQualifying ? 'exact-qualifying-phase' : 'exact-race-lap'}
            aria-label={isQualifying ? 'Current qualifying phase' : 'Current lap'}
            aria-describedby={!hasMarkers ? (isQualifying ? 'exact-phase-unavailable' : 'exact-lap-unavailable') : lapError === null ? undefined : 'exact-lap-error'}
            aria-invalid={lapError !== null}
            inputMode="numeric"
            value={lapDraft}
            disabled={!isReady || !hasMarkers}
            onChange={(event) => { lastLapCommit.current = null; setLapDraft(event.currentTarget.value) }}
            onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); commitLap() } else if (event.key === 'Escape') { event.preventDefault(); cancelLap() } }}
          />
        ) : (
          <button className="replay-lap-part" type="button" aria-label={isQualifying ? 'Edit current qualifying phase' : 'Edit current lap'} disabled={!isReady || !hasMarkers} onClick={() => setIsEditingLap(true)}>{isQualifying && lapDraft ? `Q${lapDraft}` : lapDraft || '—'}</button>
        )}
        <span aria-hidden="true"> / {finalLap ?? '—'}</span>
      </span>
      {lapError !== null && <span id="exact-lap-error" className="replay-inline-error" role="alert">{lapError}</span>}
      {!hasMarkers && <span id={isQualifying ? 'exact-phase-unavailable' : 'exact-lap-unavailable'} className="replay-inline-help">{isQualifying ? 'Qualifying phase seek unavailable' : 'Lap seek unavailable'}</span>}
    </form>
  )
}

function qualifyingNumber(phase: QualifyingPhase | null): string {
  return phase?.slice(1) ?? ''
}
