import { DragDropProvider, DragOverlay } from '@dnd-kit/react'
import { isSortable, useSortable } from '@dnd-kit/react/sortable'
import { pointerDistance, pointerIntersection, type CollisionDetector } from '@dnd-kit/collision'
import { PointerActivationConstraints, PointerSensor } from '@dnd-kit/dom'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactElement, type ReactNode } from 'react'
import {
  commitReplayPanelDrag,
  isSameReplayPanelLayout,
  isReplayPanelId,
  reconcileReplayPanelLayout,
  toggleReplayPanelVisibility,
  type ReplayPanelId,
  type ReplayPanelLayoutItem,
} from './replay-panel-layout'
import { masonryRowSpan } from './replay-workspace-masonry'
import { columnStartFromDropCenter, columnStartWithHysteresis, previewMasonryRow, resolveVerticalInsertionIndex, responsiveColumnStart, workspaceColumnCount, type MasonryPlacementItem, type PanelVerticalGeometry } from './replay-workspace-placement'

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

type ReplayPanelFrameStyle = CSSProperties & Readonly<Record<'--replay-panel-columns' | '--replay-panel-row-span' | '--replay-panel-tablet-column' | '--replay-panel-desktop-column', number>>
type ReplayDropPreviewStyle = CSSProperties & Readonly<Record<'--replay-preview-column' | '--replay-preview-columns' | '--replay-preview-row' | '--replay-preview-row-span' | '--replay-preview-column-count', number>>

interface DragMoveState {
  readonly id: ReplayPanelId
  readonly index: number
  readonly centerX: number
  readonly centerY: number
}

interface ReplayDropPreview extends DragMoveState {
  readonly desktopColumnStart: number
  readonly columnStart: number
  readonly columns: 1 | 2
  readonly rowSpan: number
  readonly rowStart: number
}

interface GhostSlot {
  readonly left: number
  readonly top: number
  readonly width: number
  readonly height: number
}

const panelCollisionDetector: CollisionDetector = (input) => pointerIntersection(input) ?? pointerDistance(input)

/** Keeps panel visibility independent so hidden subscription-based panels fully unmount. */
export function ReplayWorkspace({ panels }: ReplayWorkspaceProps) {
  const panelRegistryKey = panels.map((panel) => panel.id).join('|')
  const panelIds = useMemo(() => panels.map((panel) => panel.id), [panelRegistryKey])
  const [layout, setLayout] = useState<readonly ReplayPanelLayoutItem[]>(() => reconcileReplayPanelLayout(panelIds, []))
  const [activePanelId, setActivePanelId] = useState<ReplayPanelId | null>(null)
  const [rowSpans, setRowSpans] = useState<Readonly<Record<ReplayPanelId, number>>>({ player: 1, 'track-map': 1, leaderboard: 1, driver: 1, telemetry: 1 })
  const [columnCount, setColumnCount] = useState(() => workspaceColumnCount(typeof window === 'undefined' ? 1 : window.innerWidth))
  const [dropPreview, setDropPreview] = useState<ReplayDropPreview | null>(null)
  const [measuredGhostSlot, setMeasuredGhostSlot] = useState<GhostSlot | null>(null)
  const workspaceRef = useRef<HTMLDivElement | null>(null)
  const dragMoveRef = useRef<DragMoveState | null>(null)
  const dropPreviewRef = useRef<ReplayDropPreview | null>(null)
  const panelElementsRef = useRef(new Map<ReplayPanelId, HTMLElement>())

  useEffect(() => {
    setLayout((current) => {
      const reconciled = reconcileReplayPanelLayout(panelIds, current)
      return isSameReplayPanelLayout(current, reconciled) ? current : reconciled
    })
  }, [panelIds])

  const togglePanel = (id: ReplayPanelId) => {
    setLayout((current) => toggleReplayPanelVisibility(current, id))
  }

  const updateRowSpan = useCallback((id: ReplayPanelId, height: number) => {
    const nextSpan = masonryRowSpan(height)
    setRowSpans((current) => current[id] === nextSpan ? current : { ...current, [id]: nextSpan })
  }, [])

  const updatePanelElement = useCallback((id: ReplayPanelId, element: HTMLElement | null) => {
    if (element === null) panelElementsRef.current.delete(id)
    else panelElementsRef.current.set(id, element)
  }, [])

  const updateDropPreview = useCallback((preview: ReplayDropPreview | null) => {
    const activePreviewId = dropPreviewRef.current?.id ?? null
    dropPreviewRef.current = preview
    setDropPreview(preview)
    if (preview === null || preview.id !== activePreviewId) setMeasuredGhostSlot(null)
  }, [])

  const panelsById = useMemo(() => new Map(panels.map((panel) => [panel.id, panel])), [panels])
  const displayedLayout = dropPreview === null
    ? layout
    : commitReplayPanelDrag(layout, { id: dropPreview.id, index: dropPreview.index, desktopColumnStart: dropPreview.desktopColumnStart })
  const orderedPanels = displayedLayout.flatMap((item) => {
    const panel = panelsById.get(item.id)
    return panel === undefined ? [] : [{ panel, layout: item }]
  })

  useLayoutEffect(() => {
    if (dropPreview === null) return
    const panel = panelElementsRef.current.get(dropPreview.id)
    const workspace = workspaceRef.current
    const slot = panel === undefined || workspace === null ? null : measureGhostSlot(panel, workspace)
    setMeasuredGhostSlot((current) => isSameGhostSlot(current, slot) ? current : slot)
  })

  const createDropPreview = useCallback((move: DragMoveState, nextColumnCount: number): ReplayDropPreview | null => {
    const panel = panelsById.get(move.id)
    const workspaceBounds = workspaceRef.current?.getBoundingClientRect()
    if (panel === undefined || workspaceBounds === undefined) return null
    const nextColumnStart = columnStartFromDropCenter(move.centerX, workspaceBounds.left, workspaceBounds.width, nextColumnCount, panel.columns)
    if (nextColumnStart === null) return null
    const committedColumnStart = layout.find((item) => item.id === move.id)?.desktopColumnStart ?? null
    const desktopColumnStart = columnStartWithHysteresis(dropPreviewRef.current?.id === move.id ? dropPreviewRef.current.desktopColumnStart : committedColumnStart, nextColumnStart, move.centerX, workspaceBounds.left, workspaceBounds.width, nextColumnCount, panel.columns)
    const columns: 1 | 2 = panel.columns === 2 && nextColumnCount > 1 ? 2 : 1
    const columnStart = responsiveColumnStart(desktopColumnStart, panel.columns, nextColumnCount)
    const rowSpan = rowSpans[move.id] ?? 1
    const items: readonly MasonryPlacementItem[] = layout.flatMap((item) => {
      const registered = panelsById.get(item.id)
      return registered === undefined ? [] : [{
        id: item.id,
        columnStart: responsiveColumnStart(item.desktopColumnStart, registered.columns, nextColumnCount),
        columns: (registered.columns === 2 && nextColumnCount > 1 ? 2 : 1) as 1 | 2,
        rowSpan: rowSpans[item.id] ?? 1,
      }]
    })
    const geometryById = new Map<string, PanelVerticalGeometry>()
    panelElementsRef.current.forEach((element, id) => {
      const { bottom, top } = element.getBoundingClientRect()
      geometryById.set(id, { bottom, top })
    })
    const index = resolveVerticalInsertionIndex(items, move.id, columnStart, columns, geometryById, move.centerY, move.index)
    const rowStart = previewMasonryRow(items, { id: move.id, index, columnStart, columns, rowSpan }, nextColumnCount)
    return { ...move, index, desktopColumnStart, columnStart, columns, rowSpan, rowStart }
  }, [layout, panelsById, rowSpans])

  useEffect(() => {
    const updateColumnCount = () => {
      const nextColumnCount = workspaceColumnCount(window.innerWidth)
      setColumnCount(nextColumnCount)
      const move = dragMoveRef.current
      if (move !== null) updateDropPreview(createDropPreview(move, nextColumnCount))
    }
    window.addEventListener('resize', updateColumnCount)
    return () => window.removeEventListener('resize', updateColumnCount)
  }, [createDropPreview, updateDropPreview])

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
      onDragStart={(event) => {
        dragMoveRef.current = null
        updateDropPreview(null)
        setActivePanelId(panelIdFromSortableId(event.operation.source?.id))
      }}
      onDragMove={(event) => {
        const source = event.operation.source
        if (!isSortable(source)) {
          dragMoveRef.current = null
          updateDropPreview(null)
          return
        }
        const id = panelIdFromSortableId(source.id)
        const centerX = event.operation.shape?.current.center.x
        const centerY = event.operation.shape?.current.center.y
        if (id === null || centerX === undefined || centerY === undefined) {
          dragMoveRef.current = null
          updateDropPreview(null)
          return
        }
        const move = { id, index: source.index, centerX, centerY }
        dragMoveRef.current = move
        updateDropPreview(createDropPreview(move, columnCount))
      }}
      onDragEnd={(event) => {
        setActivePanelId(null)
        dragMoveRef.current = null
        const source = event.operation.source
        if (event.canceled || !isSortable(source)) {
          updateDropPreview(null)
          return
        }
        const id = panelIdFromSortableId(source.id)
        if (id === null) {
          updateDropPreview(null)
          return
        }
        const center = event.operation.shape?.current.center
        const destination = dropPreviewRef.current?.id === id
          ? dropPreviewRef.current
          : center === undefined
          ? null
          : createDropPreview({ id, index: source.index, centerX: center.x, centerY: center.y }, columnCount)
        updateDropPreview(null)
        setLayout((current) => commitReplayPanelDrag(current, {
          id,
          index: destination?.index ?? source.index,
          desktopColumnStart: destination?.desktopColumnStart ?? null,
        }))
      }}
    >
      <div ref={workspaceRef} className="replay-workspace">
        {orderedPanels.map(({ panel, layout: item }, index) => (
          <ReplayPanelFrame key={panel.id} panel={panel} visible={item.visible} index={index} rowSpan={rowSpans[panel.id] ?? 1} desktopColumnStart={item.desktopColumnStart} isDragging={panel.id === activePanelId} onMeasure={updateRowSpan} onPanelElement={updatePanelElement} onToggle={() => togglePanel(panel.id)} />
        ))}
        {dropPreview !== null && <ReplayDropPreview preview={dropPreview} columnCount={columnCount} measuredSlot={measuredGhostSlot} />}
      </div>
      <DragOverlay className="replay-panel-drag-overlay">{(source) => {
        const id = panelIdFromSortableId(source.id)
        return id === null ? null : <ReplayPanelDragSnapshot source={panelElementsRef.current.get(id) ?? null} />
      }}</DragOverlay>
    </DragDropProvider>
  )
}

function ReplayPanelDragSnapshot({ source }: { readonly source: HTMLElement | null }) {
  const snapshotRef = useRef<HTMLDivElement | null>(null)
  const bounds = source?.getBoundingClientRect()

  useLayoutEffect(() => {
    const snapshot = snapshotRef.current
    if (snapshot === null || source === null) return
    const clone = source.cloneNode(true) as HTMLElement
    clone.classList.remove('replay-panel-frame--drag-source')
    clone.inert = true
    snapshot.replaceChildren(clone)
    return () => snapshot.replaceChildren()
  }, [source])

  return <div ref={snapshotRef} className="replay-panel-drag-snapshot" style={bounds === undefined ? undefined : { height: bounds.height, width: bounds.width }} aria-hidden="true" />
}

function ReplayDropPreview({ preview, columnCount, measuredSlot }: { readonly preview: ReplayDropPreview; readonly columnCount: number; readonly measuredSlot: GhostSlot | null }) {
  const fallbackStyle: ReplayDropPreviewStyle = {
    '--replay-preview-column': preview.columnStart,
    '--replay-preview-columns': preview.columns,
    '--replay-preview-row': preview.rowStart,
    '--replay-preview-row-span': preview.rowSpan,
    '--replay-preview-column-count': columnCount,
  }
  const ghostStyle: ReplayDropPreviewStyle = measuredSlot === null
    ? fallbackStyle
    : { ...fallbackStyle, height: measuredSlot.height, left: measuredSlot.left, top: measuredSlot.top, width: measuredSlot.width }
  return <>
    <div className="replay-workspace__lane-highlight" style={fallbackStyle} aria-hidden="true" />
    <div className="replay-workspace__drop-preview" style={ghostStyle} aria-hidden="true">Drop {preview.id} panel</div>
  </>
}

function measureGhostSlot(panel: HTMLElement, workspace: HTMLElement): GhostSlot | null {
  let left = 0
  let top = 0
  let current: HTMLElement | null = panel
  while (current !== null && current !== workspace) {
    left += current.offsetLeft
    top += current.offsetTop
    current = current.offsetParent as HTMLElement | null
  }
  const { offsetHeight: height, offsetWidth: width } = panel
  return current === workspace && width > 0 && height > 0 ? { left, top, width, height } : null
}

function isSameGhostSlot(left: GhostSlot | null, right: GhostSlot | null): boolean {
  return left === right || (left !== null && right !== null && left.left === right.left && left.top === right.top && left.width === right.width && left.height === right.height)
}

function ReplayPanelFrame({ panel, visible, index, rowSpan, desktopColumnStart, isDragging, onMeasure, onPanelElement, onToggle }: { readonly panel: ReplayWorkspacePanel; readonly visible: boolean; readonly index: number; readonly rowSpan: number; readonly desktopColumnStart: number; readonly isDragging: boolean; readonly onMeasure: (id: ReplayPanelId, height: number) => void; readonly onPanelElement: (id: ReplayPanelId, element: HTMLElement | null) => void; readonly onToggle: () => void }) {
  const style: ReplayPanelFrameStyle = {
    '--replay-panel-columns': panel.columns,
    '--replay-panel-row-span': rowSpan,
    '--replay-panel-tablet-column': responsiveColumnStart(desktopColumnStart, panel.columns, 2),
    '--replay-panel-desktop-column': responsiveColumnStart(desktopColumnStart, panel.columns, 4),
  }
  return <SortablePanel id={panel.id} index={index} className="replay-panel-frame" style={style} label={panel.label} visible={visible} isDragging={isDragging} onMeasure={onMeasure} onPanelElement={onPanelElement} onToggle={onToggle}>
    {visible && <div className="replay-panel-frame__body">{panel.element}</div>}
  </SortablePanel>
}

function SortablePanel({ id, index, className, style, label, visible, isDragging, onMeasure, onPanelElement, onToggle, children }: { readonly id: ReplayPanelId; readonly index: number; readonly className: string; readonly style: CSSProperties; readonly label: string; readonly visible: boolean; readonly isDragging: boolean; readonly onMeasure: (id: ReplayPanelId, height: number) => void; readonly onPanelElement: (id: ReplayPanelId, element: HTMLElement | null) => void; readonly onToggle: () => void; readonly children: ReactNode }) {
  const { handleRef, isDropping, ref } = useSortable({ id, index, collisionDetector: panelCollisionDetector })
  const elementRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    const element = elementRef.current
    if (element === null) return
    const observer = new ResizeObserver(() => onMeasure(id, element.getBoundingClientRect().height))
    observer.observe(element)
    onMeasure(id, element.getBoundingClientRect().height)
    return () => observer.disconnect()
  }, [id, onMeasure])

  const setPanelRef = (element: HTMLElement | null) => {
    elementRef.current = element
    ref(element)
    onPanelElement(id, element)
  }

  return <section ref={setPanelRef} className={`${className}${isDragging || isDropping ? ' replay-panel-frame--drag-source' : ''}`} style={style} aria-label={label}>
    <header className="replay-panel-frame__header">
      <button ref={handleRef} className="replay-panel-drag-handle" type="button" aria-label={`Move ${label} panel`}><span aria-hidden="true">⠿</span> {label}</button>
      <button className="replay-workspace-toggle" type="button" aria-label={`${visible ? 'Hide' : 'Show'} ${label} panel`} aria-pressed={visible} onClick={onToggle}>{visible ? 'Hide' : 'Show'}</button>
    </header>
    {children}
  </section>
}

function panelIdFromSortableId(value: unknown): ReplayPanelId | null {
  return isReplayPanelId(value) ? value : null
}
