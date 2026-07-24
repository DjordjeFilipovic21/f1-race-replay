import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { LapStart } from '../../../data/replay/types'

export interface ExactLapNavigationProps {
  readonly currentLap: number | null
  readonly isReady: boolean
  readonly lapStarts?: readonly LapStart[]
  readonly onSeek: (timeMs: number) => void
}

/** Navigates to an indexed lap and preserves its draft until focus leaves the field. */
export function ExactLapNavigation({ currentLap, isReady, lapStarts, onSeek }: ExactLapNavigationProps) {
  const [lapDraft, setLapDraft] = useState(() => currentLap?.toString() ?? '')
  const [lapError, setLapError] = useState<string | null>(null)
  const [isEditingLap, setIsEditingLap] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const lastLapCommit = useRef<string | null>(null)
  const finalLap = lapStarts?.reduce((maximum, entry) => Math.max(maximum, entry.lap), 0) || null

  useEffect(() => {
    if (!isEditingLap) setLapDraft(currentLap?.toString() ?? '')
  }, [currentLap, isEditingLap])

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
    const lap = /^\d+$/.test(lapDraft) ? Number(lapDraft) : Number.NaN
    const marker = Number.isSafeInteger(lap) ? lapStarts?.find((entry) => entry.lap === lap) : undefined
    if (!marker) {
      setLapError('Enter an available race lap.')
      return false
    }
    setLapError(null)
    lastLapCommit.current = lapDraft
    onSeek(marker.startMs)
    setIsEditingLap(false)
    return true
  }

  const cancelLap = () => {
    setLapError(null)
    lastLapCommit.current = null
    setLapDraft(currentLap?.toString() ?? '')
    setIsEditingLap(false)
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    commitLap()
  }

  return (
    <form className="replay-lap replay-lap-editor" aria-label="Lap navigation" onSubmit={handleSubmit} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null) && isEditingLap) commitLap() }}>
      <span className="replay-lap-display">
        <span>Lap</span>
        {isEditingLap ? (
          <input
            ref={inputRef}
            id="exact-race-lap"
            aria-label="Current lap"
            aria-describedby={!lapStarts?.length ? 'exact-lap-unavailable' : lapError === null ? undefined : 'exact-lap-error'}
            aria-invalid={lapError !== null}
            inputMode="numeric"
            value={lapDraft}
            disabled={!isReady || !lapStarts?.length}
            onChange={(event) => { lastLapCommit.current = null; setLapDraft(event.currentTarget.value) }}
            onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); commitLap() } else if (event.key === 'Escape') { event.preventDefault(); cancelLap() } }}
          />
        ) : (
          <button className="replay-lap-part" type="button" aria-label="Edit current lap" disabled={!isReady || !lapStarts?.length} onClick={() => setIsEditingLap(true)}>{lapDraft || '—'}</button>
        )}
        <span aria-hidden="true"> / {finalLap ?? '—'}</span>
      </span>
      {lapError !== null && <span id="exact-lap-error" className="replay-inline-error" role="alert">{lapError}</span>}
      {!lapStarts?.length && <span id="exact-lap-unavailable" className="replay-inline-help">Lap seek unavailable</span>}
    </form>
  )
}
