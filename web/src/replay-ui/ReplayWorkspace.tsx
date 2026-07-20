import { Fragment, useState, type ReactElement } from 'react'

export type ReplayPanelId = 'player' | 'track-map' | 'leaderboard'

export interface ReplayWorkspacePanel {
  readonly id: ReplayPanelId
  readonly label: string
  readonly element: ReactElement
}

export interface ReplayWorkspaceProps {
  readonly panels: readonly ReplayWorkspacePanel[]
}

/** Keeps panel visibility independent so hidden subscription-based panels fully unmount. */
export function ReplayWorkspace({ panels }: ReplayWorkspaceProps) {
  const [visiblePanelIds, setVisiblePanelIds] = useState<readonly ReplayPanelId[]>(() => panels.map((panel) => panel.id))

  const togglePanel = (id: ReplayPanelId) => {
    setVisiblePanelIds((current) => current.includes(id)
      ? current.filter((panelId) => panelId !== id)
      : [...current, id])
  }

  return (
    <>
      <div className="replay-workspace-toolbar" role="group" aria-label="Replay workspace panels">
        {panels.map((panel) => {
          const isVisible = visiblePanelIds.includes(panel.id)
          return (
            <button className="replay-workspace-toggle" type="button" key={panel.id} aria-pressed={isVisible} onClick={() => togglePanel(panel.id)}>
              {panel.label}
            </button>
          )
        })}
      </div>
      <div className="replay-workspace">
        {panels.map((panel) => visiblePanelIds.includes(panel.id) && <Fragment key={panel.id}>{panel.element}</Fragment>)}
      </div>
    </>
  )
}
