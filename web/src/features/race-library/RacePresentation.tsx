import type { CatalogV2Race } from '../../data/catalog/types'
import type { ReplaySource } from '../../data/replay/types'
import { RaceDetails } from './RaceDetails'
import { RaceGlobe } from './RaceGlobe'

interface RacePresentationProps {
  readonly races: readonly CatalogV2Race[]
  readonly race: CatalogV2Race
  readonly source?: ReplaySource
  readonly selectedSessionCode: string | null
  readonly onSelectRace: (raceId: string) => void
  readonly onSelectSession: (sessionCode: string) => void
  readonly canOpenWorkspace: boolean
  readonly onOpenWorkspace: () => void
  readonly onShowAllRaces: () => void
}

export function RacePresentation({
  races,
  race,
  source,
  selectedSessionCode,
  onSelectRace,
  onSelectSession,
  canOpenWorkspace,
  onOpenWorkspace,
  onShowAllRaces,
}: RacePresentationProps) {
  const raceIndex = races.findIndex(({ race_id: raceId }) => raceId === race.race_id)
  const previousRace = raceIndex > 0 ? races[raceIndex - 1] : null
  const nextRace = raceIndex >= 0 && raceIndex < races.length - 1 ? races[raceIndex + 1] : null

  return (
    <main className="race-presentation page-transition-surface">
      <nav className="race-presentation__nav" aria-label="Race library navigation">
        <button type="button" className="race-presentation__back" onClick={onShowAllRaces}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="m15 18-6-6 6-6" />
          </svg>
          All races
        </button>
        <label className="race-presentation__picker">
          <span>Change circuit</span>
          <select
            aria-label="Change circuit"
            value={race.race_id}
            onChange={(event) => onSelectRace(event.currentTarget.value)}
          >
            {races.map((option) => (
              <option key={option.race_id} value={option.race_id}>
                R{option.round_number} · {option.event_name}
              </option>
            ))}
          </select>
        </label>
      </nav>

      <div className="race-presentation__layout">
        <section className="race-presentation__world" aria-label={`${race.event_name} location`}>
          <div className="race-presentation__world-copy">
            <span>FIA Formula One World Championship</span>
            <strong>{race.country || 'Global circuit'}</strong>
          </div>
          <RaceGlobe race={race} />
          <div className="race-presentation__round-navigation">
            <RaceNavigationButton direction="previous" race={previousRace} onSelectRace={onSelectRace} />
            <span aria-live="polite">{raceIndex + 1} / {races.length}</span>
            <RaceNavigationButton direction="next" race={nextRace} onSelectRace={onSelectRace} />
          </div>
        </section>

        <RaceDetails
          race={race}
          source={source}
          selectedSessionCode={selectedSessionCode}
          onSelectSession={onSelectSession}
          canOpenWorkspace={canOpenWorkspace}
          onOpenWorkspace={onOpenWorkspace}
        />
      </div>
    </main>
  )
}

interface RaceNavigationButtonProps {
  readonly direction: 'previous' | 'next'
  readonly race: CatalogV2Race | null
  readonly onSelectRace: (raceId: string) => void
}

function RaceNavigationButton({ direction, race, onSelectRace }: RaceNavigationButtonProps) {
  const label = race === null
    ? `No ${direction} round`
    : `${direction === 'previous' ? 'Previous' : 'Next'} round: ${race.event_name}`
  return (
    <button
      type="button"
      className={`race-round-action race-round-action--${direction}`}
      aria-label={label}
      disabled={race === null}
      onClick={() => { if (race !== null) onSelectRace(race.race_id) }}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <path d={direction === 'previous' ? 'm15 18-6-6 6-6' : 'm9 18 6-6-6-6'} />
      </svg>
      <span>{direction === 'previous' ? 'Prev' : 'Next'}</span>
    </button>
  )
}
