import { useRef, type KeyboardEvent } from 'react'
import { isSessionReplayReady } from '../../data/catalog/guards'
import type { CatalogV2Session } from '../../data/catalog/types'

interface SessionSelectorProps {
  readonly sessions: readonly CatalogV2Session[]
  readonly selectedSessionCode: string | null
  readonly onSelectSession: (sessionCode: string) => void
}

export function SessionSelector({ sessions, selectedSessionCode, onSelectSession }: SessionSelectorProps) {
  const sessionButtonRefs = useRef<Array<HTMLButtonElement | null>>([])
  const readySessionIndexes = sessions.reduce<number[]>((indexes, session, index) => {
    if (isSessionReplayReady(session)) indexes.push(index)
    return indexes
  }, [])
  const selectedReadyIndex = readySessionIndexes.find((index) => sessions[index].session_code === selectedSessionCode)
  const tabStopIndex = selectedReadyIndex ?? readySessionIndexes[0]

  function focusSession(index: number): void {
    sessionButtonRefs.current[index]?.focus()
  }

  function selectSession(index: number): void {
    const session = sessions[index]
    if (!session || !isSessionReplayReady(session)) return
    onSelectSession(session.session_code)
    focusSession(index)
  }

  function handleSessionKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number): void {
    if (!isSessionReplayReady(sessions[index])) return

    const readyIndex = readySessionIndexes.indexOf(index)
    if (readyIndex < 0) return

    let nextReadyIndex: number | null = null
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        nextReadyIndex = (readyIndex + 1) % readySessionIndexes.length
        break
      case 'ArrowLeft':
      case 'ArrowUp':
        nextReadyIndex = (readyIndex - 1 + readySessionIndexes.length) % readySessionIndexes.length
        break
      case 'Home':
        nextReadyIndex = 0
        break
      case 'End':
        nextReadyIndex = readySessionIndexes.length - 1
        break
      default:
        return
    }

    if (nextReadyIndex === null) return
    event.preventDefault()
    selectSession(readySessionIndexes[nextReadyIndex])
  }

  return (
    <div className="library-race-card__sessions" role="radiogroup" aria-label="Available sessions">
      {sessions.map((session, index) => {
        const isReady = isSessionReplayReady(session)
        const isSelected = session.session_code === selectedSessionCode
        return (
          <button
            key={session.session_code}
            type="button"
            role="radio"
            aria-checked={isSelected}
            aria-disabled={!isReady}
            aria-label={`${session.session_name}${!isReady ? ' — not yet available for replay' : ''}`}
            className="library-session-button"
            disabled={!isReady}
            ref={(button) => { sessionButtonRefs.current[index] = button }}
            tabIndex={isReady && index === tabStopIndex ? 0 : -1}
            onClick={() => selectSession(index)}
            onKeyDown={(event) => handleSessionKeyDown(event, index)}
          >
            <div className="library-session-button__info">
              <span className="library-session-button__name">{session.session_name}</span>
              <span className="library-session-button__status" aria-live="polite">
                {describeSessionStatus(session, isReady)}
              </span>
            </div>
            <span className="library-session-button__action" aria-hidden="true">
              {isReady ? 'Select' : 'Unavailable'}
            </span>
          </button>
        )
      })}
    </div>
  )
}

function describeSessionStatus(session: CatalogV2Session, isReady: boolean): string {
  if (isReady) return 'Ready to replay'
  if (!session.validated) return 'Awaiting validation'
  return 'Incomplete replay data'
}
