import type { CatalogV2Race } from '../../data/catalog/types'

interface RaceListProps {
  readonly races: readonly CatalogV2Race[]
  readonly onSelectRace: (raceId: string) => void
}

export function RaceList({ races, onSelectRace }: RaceListProps) {
  return (
    <div className="library-races" role="list" aria-label="Season races">
      {races.map((race) => (
        <article key={race.race_id} role="listitem" className="library-race-card">
          <button
            type="button"
            className="library-race-card__trigger"
            aria-label={`Explore round ${race.round_number}: ${race.event_name}${race.country ? `, ${race.country}` : ''}`}
            onClick={() => onSelectRace(race.race_id)}
          >
            <span className="library-race-card__round" aria-label={`Round ${race.round_number}`}>
              Round {String(race.round_number).padStart(2, '0')}
            </span>
            <span className="library-race-card__title">{race.event_name}</span>
            <span className="library-race-card__meta">
              {[race.location, race.country].filter(Boolean).join(', ') || 'Location unavailable'}
            </span>
            <span className="library-race-card__action" aria-hidden="true">
              Explore
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="m9 18 6-6-6-6" />
              </svg>
            </span>
          </button>
        </article>
      ))}
    </div>
  )
}
