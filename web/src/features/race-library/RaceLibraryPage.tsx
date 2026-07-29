import { selectReplaySession } from '../../data/catalog/guards'
import type { CatalogV2 } from '../../data/catalog/types'
import { LibraryMessage } from './LibraryMessage'
import { RaceList } from './RaceList'

export interface RaceLibraryPageProps {
  readonly catalog: CatalogV2 | null
  readonly selectedRaceId: string | null
  readonly selectedSessionCode: string | null
  readonly isLoading: boolean
  readonly error: Error | null
  readonly onSelectRace: (raceId: string | null) => void
  readonly onSelectSession: (sessionCode: string | null) => void
  readonly onOpenWorkspace: () => void
  readonly onRetry?: () => void
  readonly selectionError?: string
}

export function RaceLibraryPage({
  catalog,
  selectedRaceId,
  selectedSessionCode,
  isLoading,
  error,
  onSelectRace,
  onSelectSession,
  onOpenWorkspace,
  onRetry,
  selectionError,
}: RaceLibraryPageProps) {
  const selection = catalog !== null ? selectReplaySession(catalog, selectedRaceId, selectedSessionCode) : null
  const canOpenWorkspace = selection !== null

  function handleSelectRace(raceId: string) {
    if (raceId === selectedRaceId) {
      onSelectRace(null)
      onSelectSession(null)
    } else {
      onSelectRace(raceId)
      onSelectSession(null)
    }
  }

  function handleSelectSession(sessionCode: string) {
    onSelectSession(sessionCode)
  }

  return (
    <div className="landing-shell">
      <header className="landing-header">
        <h1 className="landing-header__title">Race Replay Library</h1>
        <p className="landing-header__subtitle">
          {catalog !== null ? `Season ${catalog.year}` : 'Season catalog'}
        </p>
      </header>
      <section className="library-container" aria-label="Race library">
        <header className="library-container__header">
          <h2 className="library-container__title">
            {catalog !== null ? `${catalog.year} Races` : 'Races'}
          </h2>
          {catalog !== null && (
            <span className="library-container__count">
              {catalog.races.length} {catalog.races.length === 1 ? 'event' : 'events'}
            </span>
          )}
        </header>
        {error !== null && catalog === null ? (
          <LibraryMessage
            variant="error"
            title="Unable to Load Season"
            message={error.message}
            onRetry={onRetry}
          />
        ) : isLoading && catalog === null ? (
          <LibraryMessage
            variant="loading"
            title="Loading Season"
            message="Fetching the race catalog…"
          />
        ) : catalog !== null && catalog.races.length === 0 ? (
          <LibraryMessage
            variant="empty"
            title="No Races Available"
            message="This season has no races in the catalog yet."
          />
        ) : catalog !== null ? (
          <>
            {selectionError !== undefined && (
              <LibraryMessage
                variant="error"
                title="Replay Selection Unavailable"
                message={selectionError}
              />
            )}
            <RaceList
              races={catalog.races}
              selectedRaceId={selectedRaceId}
              selectedSessionCode={selectedSessionCode}
              onSelectRace={handleSelectRace}
              onSelectSession={handleSelectSession}
              canOpenWorkspace={canOpenWorkspace}
              onOpenWorkspace={onOpenWorkspace}
            />
          </>
        ) : null}
      </section>
    </div>
  )
}
