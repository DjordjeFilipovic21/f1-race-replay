import type { CatalogV2Race } from '../../data/catalog/types'
import { SessionSelector } from './SessionSelector'

interface RaceDetailsProps {
  readonly race: CatalogV2Race
  readonly selectedSessionCode: string | null
  readonly onSelectSession: (sessionCode: string) => void
  readonly canOpenWorkspace: boolean
  readonly onOpenWorkspace: () => void
}

export function RaceDetails({
  race,
  selectedSessionCode,
  onSelectSession,
  canOpenWorkspace,
  onOpenWorkspace,
}: RaceDetailsProps) {
  return (
    <>
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
