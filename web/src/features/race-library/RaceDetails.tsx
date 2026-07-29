import type { CatalogV2Race } from '../../data/catalog/types'
import type { ReplaySource } from '../../data/replay/types'
import { CircuitPreview } from './CircuitPreview'
import { RaceGlobe } from './RaceGlobe'
import { SessionSelector } from './SessionSelector'

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
  const visual = race.visual

  return (
    <>
      {visual !== undefined && (
        <section className="library-details__visuals" aria-label={`${race.event_name} visual preview`}>
          <RaceGlobe race={race} />
          {source !== undefined && visual.circuitPreview !== undefined && (
            <CircuitPreview
              source={source}
              previewPointer={visual.circuitPreview}
              circuitName={race.event_name}
            />
          )}
        </section>
      )}
      <SessionSelector
        sessions={race.sessions}
        selectedSessionCode={selectedSessionCode}
        onSelectSession={onSelectSession}
      />
      <div className="library-details__actions">
        <button
          type="button"
          className="library-open-action"
          disabled={!canOpenWorkspace}
          aria-label="Open replay workspace"
          onClick={onOpenWorkspace}
        >
          Open Workspace
        </button>
      </div>
    </>
  )
}
