import { useId } from 'react'
import type { CatalogV2Race } from '../../data/catalog/types'
import { RaceDetails } from './RaceDetails'

interface RaceListProps {
  readonly races: readonly CatalogV2Race[]
  readonly selectedRaceId: string | null
  readonly selectedSessionCode: string | null
  readonly onSelectRace: (raceId: string) => void
  readonly onSelectSession: (sessionCode: string) => void
  readonly canOpenWorkspace: boolean
  readonly onOpenWorkspace: () => void
}

export function RaceList({
  races,
  selectedRaceId,
  selectedSessionCode,
  onSelectRace,
  onSelectSession,
  canOpenWorkspace,
  onOpenWorkspace,
}: RaceListProps) {
  const raceListId = useId().replace(/[^A-Za-z0-9_-]/g, '')

  return (
    <div className="library-races" role="list" aria-label="Season races">
      {races.map((race) => {
        const isSelected = race.race_id === selectedRaceId
        const detailsId = `race-details-${raceListId}-${race.race_id}`
        return (
          <div
            key={race.race_id}
            role="listitem"
            className={`library-race-card${isSelected ? ' library-race-card--selected' : ''}`}
          >
            <button
              type="button"
              className="library-race-card__trigger"
              aria-expanded={isSelected}
              aria-controls={detailsId}
              aria-label={`Round ${race.round_number}: ${race.event_name}${race.country ? `, ${race.country}` : ''}`}
              onClick={() => onSelectRace(race.race_id)}
            >
              <div className="library-race-card__header">
                <h3 className="library-race-card__title">{race.event_name}</h3>
                <span className="library-race-card__round" aria-label={`Round ${race.round_number}`}>
                  R{race.round_number}
                </span>
              </div>
              {(race.country || race.location || race.event_date) && (
                <div className="library-race-card__meta">
                  {race.country && (
                    <span className="library-race-card__meta-item" aria-label={`Country: ${race.country}`}>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                        <circle cx="12" cy="12" r="10" />
                        <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
                      </svg>
                      {race.country}
                    </span>
                  )}
                  {race.location && (
                    <span className="library-race-card__meta-item" aria-label={`Location: ${race.location}`}>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                        <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
                        <circle cx="12" cy="10" r="3" />
                      </svg>
                      {race.location}
                    </span>
                  )}
                  {race.event_date && (
                    <span className="library-race-card__meta-item" aria-label={`Date: ${race.event_date}`}>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                        <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
                        <line x1="16" y1="2" x2="16" y2="6" />
                        <line x1="8" y1="2" x2="8" y2="6" />
                        <line x1="3" y1="10" x2="21" y2="10" />
                      </svg>
                      {race.event_date}
                    </span>
                  )}
                </div>
              )}
            </button>
            <div
              id={detailsId}
              className="library-race-card__details"
              role="region"
              aria-label={`${race.event_name} details`}
              hidden={!isSelected}
            >
              <RaceDetails
                race={race}
                selectedSessionCode={selectedSessionCode}
                onSelectSession={onSelectSession}
                canOpenWorkspace={canOpenWorkspace}
                onOpenWorkspace={onOpenWorkspace}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}
