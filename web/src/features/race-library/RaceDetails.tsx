import type { CatalogV2Race } from '../../data/catalog/types'
import type { ReplaySource } from '../../data/replay/types'
import { CircuitPreview } from './CircuitPreview'
import { SessionSelector } from './SessionSelector'

const TEMPORARY_CIRCUIT_PATH = [
  'M 42 134',
  'C 62 64 116 28 180 39',
  'C 238 49 262 105 318 94',
  'C 359 86 377 47 353 28',
  'C 320 3 274 38 254 73',
  'C 229 116 210 174 154 181',
  'C 103 188 58 170 42 134',
  'Z',
].join(' ')

interface RaceDetailsProps {
  readonly race: CatalogV2Race
  readonly source?: ReplaySource
  readonly selectedSessionCode: string | null
  readonly onSelectSession: (sessionCode: string) => void
  readonly canOpenWorkspace: boolean
  readonly onOpenWorkspace: () => void
}

export function RaceDetails({
  race,
  source,
  selectedSessionCode,
  onSelectSession,
  canOpenWorkspace,
  onOpenWorkspace,
}: RaceDetailsProps) {
  const previewPointer = race.visual?.circuitPreview
  const eventHeading = splitEventHeading(race.event_name)

  return (
    <section className="race-presentation__details" aria-label={`${race.event_name} details`}>
      <header className="race-presentation__header">
        <span className="race-presentation__eyebrow">
          Round {String(race.round_number).padStart(2, '0')}
        </span>
        <h1 className="race-presentation__title" aria-label={race.event_name}>
          {eventHeading.title}
        </h1>
        <p className="race-presentation__subtitle" aria-hidden="true">
          {eventHeading.subtitle}
        </p>
        <p className="race-presentation__location">
          {[race.location, race.country].filter(Boolean).join(' · ') || 'Location unavailable'}
        </p>
      </header>

      <div className="race-presentation__circuit" aria-label={`${race.event_name} circuit`}>
        {source !== undefined && previewPointer !== undefined ? (
          <CircuitPreview
            source={source}
            previewPointer={previewPointer}
            circuitName={race.event_name}
          />
        ) : (
          <TemporaryCircuitPreview circuitName={race.event_name} />
        )}
        <dl className="race-presentation__facts">
          <div>
            <dt>Date</dt>
            <dd>{formatSeasonDate(race.event_date)}</dd>
          </div>
          <div>
            <dt>Venue</dt>
            <dd>{race.location || 'TBA'}</dd>
          </div>
          <div>
            <dt>Sessions</dt>
            <dd>{race.sessions.length}</dd>
          </div>
        </dl>
      </div>

      <div className="race-presentation__sessions">
        <div>
          <span className="race-presentation__section-index">01</span>
          <h2>Choose session</h2>
        </div>
        <SessionSelector
          sessions={race.sessions}
          selectedSessionCode={selectedSessionCode}
          onSelectSession={onSelectSession}
        />
      </div>

      <button
        type="button"
        className="library-open-action"
        disabled={!canOpenWorkspace}
        aria-label="Open replay workspace"
        onClick={onOpenWorkspace}
      >
        <span>{canOpenWorkspace ? 'Open replay' : 'Select a session'}</span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <path d="m9 18 6-6-6-6" />
        </svg>
      </button>
    </section>
  )
}

function splitEventHeading(eventName: string): { readonly title: string; readonly subtitle: string } {
  const normalizedName = eventName.trim()
  const grandPrixMatch = normalizedName.match(/^(.*?)\s+(Grand Prix)$/i)
  if (grandPrixMatch === null) {
    return { title: normalizedName, subtitle: 'Formula One' }
  }
  return { title: grandPrixMatch[1], subtitle: grandPrixMatch[2] }
}

function TemporaryCircuitPreview({ circuitName }: { readonly circuitName: string }) {
  const label = `${circuitName} circuit preview`
  return (
    <div className="circuit-preview circuit-preview--resolved circuit-preview--temporary">
      <svg
        className="circuit-preview__canvas"
        role="img"
        aria-label={label}
        viewBox="-24 -24 448 268"
        preserveAspectRatio="xMidYMid meet"
        data-preview-source="temporary"
      >
        <title>{label}</title>
        <g className="circuit-preview__geometry">
          <path
            className="circuit-preview__glow"
            d={TEMPORARY_CIRCUIT_PATH}
            pathLength={1}
            aria-hidden="true"
          />
          <path
            className="circuit-preview__path"
            d={TEMPORARY_CIRCUIT_PATH}
            pathLength={1}
          />
        </g>
      </svg>
    </div>
  )
}

function formatSeasonDate(eventDate: string | null | undefined): string {
  if (!eventDate) return 'Date TBA'
  const calendarDate = eventDate.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (calendarDate === null) return eventDate
  const date = new Date(Date.UTC(
    Number(calendarDate[1]),
    Number(calendarDate[2]) - 1,
    Number(calendarDate[3]),
    12,
  ))
  if (Number.isNaN(date.getTime())) return eventDate
  return new Intl.DateTimeFormat('en', {
    day: '2-digit',
    month: 'short',
    timeZone: 'UTC',
  }).format(date)
}
