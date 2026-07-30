import { selectReplaySession } from '../../data/catalog/guards'
import type { CatalogV2 } from '../../data/catalog/types'
import type { ReplaySource } from '../../data/replay/types'
import { LibraryMessage } from './LibraryMessage'
import { RaceList } from './RaceList'
import { RacePresentation } from './RacePresentation'

export interface RaceLibraryPageProps {
  readonly catalog: CatalogV2 | null
  readonly source?: ReplaySource
  readonly availableYears?: readonly number[]
  readonly selectedYear?: number
  readonly selectedRaceId: string | null
  readonly selectedSessionCode: string | null
  readonly isLoading: boolean
  readonly error: Error | null
  readonly onSelectYear?: (year: number) => void
  readonly onSelectRace: (raceId: string | null) => void
  readonly onSelectSession: (sessionCode: string | null) => void
  readonly onOpenWorkspace: () => void
  readonly onRetry?: () => void
  readonly selectionError?: string
}

export function RaceLibraryPage({
  catalog,
  source,
  availableYears,
  selectedYear,
  selectedRaceId,
  selectedSessionCode,
  isLoading,
  error,
  onSelectYear,
  onSelectRace,
  onSelectSession,
  onOpenWorkspace,
  onRetry,
  selectionError,
}: RaceLibraryPageProps) {
  const selectedRace = catalog?.races.find(({ race_id: raceId }) => raceId === selectedRaceId) ?? null
  const selection = catalog !== null ? selectReplaySession(catalog, selectedRaceId, selectedSessionCode) : null

  function selectRace(raceId: string): void {
    onSelectRace(raceId)
    onSelectSession(null)
  }

  function showAllRaces(): void {
    onSelectRace(null)
    onSelectSession(null)
  }

  if (catalog !== null && selectedRace !== null) {
    return (
      <RacePresentation
        races={catalog.races}
        race={selectedRace}
        source={source}
        selectedSessionCode={selectedSessionCode}
        onSelectRace={selectRace}
        onSelectSession={onSelectSession}
        canOpenWorkspace={selection !== null}
        onOpenWorkspace={onOpenWorkspace}
        onShowAllRaces={showAllRaces}
      />
    )
  }

  const years = availableYears ?? (catalog === null ? [] : [catalog.year])
  const activeYear = selectedYear ?? catalog?.year ?? years[0]

  return (
    <main className="landing-shell">
      <div className="landing-shell__grid" aria-hidden="true" />
      <header className="landing-header">
        <span className="landing-header__eyebrow">Formula One archive experience</span>
        <h1 className="landing-header__title">
          Race <span>Replay</span> Library
        </h1>
        <p className="landing-header__subtitle">
          Choose a season and circuit. Revisit every available session, lap by lap.
        </p>
      </header>

      <section className="library-container" aria-label="Race library">
        <header className="library-container__header">
          <div>
            <span className="library-container__step">01 / Select a season</span>
            <h2 className="library-container__title">Choose your race</h2>
          </div>
          <label className="library-year-selector">
            <span>Season</span>
            <select
              aria-label="Season year"
              value={activeYear}
              disabled={years.length < 2 || onSelectYear === undefined}
              onChange={(event) => onSelectYear?.(Number(event.currentTarget.value))}
            >
              {years.map((year) => <option key={year} value={year}>{year}</option>)}
            </select>
          </label>
        </header>

        {selectionError !== undefined && (
          <LibraryMessage
            variant="error"
            title="Replay Selection Unavailable"
            message={selectionError}
          />
        )}

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
            <div className="library-container__summary">
              <span>{catalog.races.length} {catalog.races.length === 1 ? 'event' : 'events'}</span>
              <span>Season {catalog.year}</span>
            </div>
            <RaceList races={catalog.races} onSelectRace={selectRace} />
          </>
        ) : null}
      </section>

      <footer className="landing-footer">
        <span>Telemetry-led race reconstruction</span>
        <span aria-hidden="true">F1 / {activeYear ?? '—'}</span>
      </footer>
    </main>
  )
}
