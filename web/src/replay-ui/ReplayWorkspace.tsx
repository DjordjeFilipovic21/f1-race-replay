import { DragDropProvider, DragOverlay } from '@dnd-kit/react'
import { isSortable, useSortable } from '@dnd-kit/react/sortable'
import { pointerDistance, pointerIntersection, type CollisionDetector } from '@dnd-kit/collision'
import { PointerActivationConstraints, PointerSensor } from '@dnd-kit/dom'
import { useEffect, useMemo, useState, type CSSProperties, type ReactElement, type ReactNode } from 'react'
import {
  commitReplayPanelDrag,
  isSameReplayPanelLayout,
  reconcileReplayPanelLayout,
  toggleReplayPanelVisibility,
  type ReplayPanelId,
  type ReplayPanelLayoutItem,
} from './replay-panel-layout'

export type { ReplayPanelId, ReplayPanelLayoutItem } from './replay-panel-layout'

export interface ReplayWorkspacePanel {
  readonly id: ReplayPanelId
  readonly label: string
  readonly element: ReactElement
  readonly columns: 1 | 2
}

export interface ReplayWorkspaceProps {
  readonly panels: readonly ReplayWorkspacePanel[]
}

type ReplayPanelFrameStyle = CSSProperties & Readonly<Record<'--replay-panel-columns', number>>

const panelCollisionDetector: CollisionDetector = (input) => pointerIntersection(input) ?? pointerDistance(input)

/** Keeps panel visibility independent so hidden subscription-based panels fully unmount. */
export function ReplayWorkspace({ panels }: ReplayWorkspaceProps) {
  const panelRegistryKey = panels.map((panel) => panel.id).join('|')
  const panelIds = useMemo(() => panels.map((panel) => panel.id), [panelRegistryKey])
  const [layout, setLayout] = useState<readonly ReplayPanelLayoutItem[]>(() => panelIds.map((id) => ({ id, visible: true })))
  const [activePanelId, setActivePanelId] = useState<ReplayPanelId | null>(null)

  useEffect(() => {
    setLayout((current) => {
      const reconciled = reconcileReplayPanelLayout(panelIds, current)
      return isSameReplayPanelLayout(current, reconciled) ? current : reconciled
    })
  }, [panelIds])

  const togglePanel = (id: ReplayPanelId) => {
    setLayout((current) => toggleReplayPanelVisibility(current, id))
  }

  const panelsById = new Map(panels.map((panel) => [panel.id, panel]))
  const orderedPanels = layout.flatMap((item) => {
    const panel = panelsById.get(item.id)
    return panel === undefined ? [] : [{ panel, layout: item }]
  })

  return (
    <DragDropProvider
      sensors={(defaults) => [
        ...defaults.filter((sensor) => sensor !== PointerSensor),
        PointerSensor.configure({
          activationConstraints: (event) => event.pointerType === 'touch'
            ? [new PointerActivationConstraints.Delay({ value: 250, tolerance: 5 })]
            : [new PointerActivationConstraints.Distance({ value: 6 })],
        }),
      ]}
      onDragStart={(event) => setActivePanelId(panelIdFromSortableId(event.operation.source?.id))}
      onDragEnd={(event) => {
        setActivePanelId(null)
        const source = event.operation.source
        if (event.canceled || !isSortable(source)) return
        const id = panelIdFromSortableId(source.id)
        if (id === null) return
        setLayout((current) => commitReplayPanelDrag(current, { id, index: source.index }))
      }}
    >
      <div className="replay-workspace">
        {orderedPanels.map(({ panel, layout: item }, index) => (
          <ReplayPanelFrame key={panel.id} panel={panel} visible={item.visible} index={index} onToggle={() => togglePanel(panel.id)} />
        ))}
      </div>
      <DragOverlay className="replay-panel-drag-overlay">{activePanelId === null ? null : `Moving ${panelsById.get(activePanelId)?.label ?? 'panel'}`}</DragOverlay>
    </DragDropProvider>
  )
}

function ReplayPanelFrame({ panel, visible, index, onToggle }: { readonly panel: ReplayWorkspacePanel; readonly visible: boolean; readonly index: number; readonly onToggle: () => void }) {
  const style: ReplayPanelFrameStyle = {
    '--replay-panel-columns': panel.columns,
  }
  return <SortablePanel id={panel.id} index={index} className="replay-panel-frame" style={style} label={panel.label} visible={visible} onToggle={onToggle}>
    {visible && <div className="replay-panel-frame__body">{panel.element}</div>}
  </SortablePanel>
}

function SortablePanel({ id, index, className, style, label, visible, onToggle, children }: { readonly id: ReplayPanelId; readonly index: number; readonly className: string; readonly style: CSSProperties; readonly label: string; readonly visible: boolean; readonly onToggle: () => void; readonly children: ReactNode }) {
  const { handleRef, ref } = useSortable({ id, index, collisionDetector: panelCollisionDetector })
  return <section ref={ref} className={className} style={style} aria-label={label}>
    <header className="replay-panel-frame__header">
      <button ref={handleRef} className="replay-panel-drag-handle" type="button" aria-label={`Move ${label} panel`}><span aria-hidden="true">⠿</span> {label}</button>
      <button className="replay-workspace-toggle" type="button" aria-label={`${visible ? 'Hide' : 'Show'} ${label} panel`} aria-pressed={visible} onClick={onToggle}>{visible ? 'Hide' : 'Show'}</button>
    </header>
    {children}
  </section>
}

function panelIdFromSortableId(value: unknown): ReplayPanelId | null {
  return value === 'player' || value === 'track-map' || value === 'leaderboard' ? value : null
}
