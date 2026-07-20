import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { LapStart } from '../replay-data/types'

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
  const isEditingLap = useRef(false)
  const lastLapCommit = useRef<string | null>(null)

  useEffect(() => {
    if (!isEditingLap.current) setLapDraft(currentLap?.toString() ?? '')
  }, [currentLap])

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
    onSeek(marker.startMs)
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    commitLap()
  }

  return (
    <form className="replay-lap replay-lap-editor" aria-label="Lap navigation" onSubmit={handleSubmit}>
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
  )
}
