import { DragDropProvider, DragOverlay } from '@dnd-kit/react'
import { isSortable, useSortable } from '@dnd-kit/react/sortable'
import { pointerDistance, pointerIntersection, type CollisionDetector } from '@dnd-kit/collision'
import { PointerActivationConstraints, PointerSensor } from '@dnd-kit/dom'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactElement, type ReactNode } from 'react'
import { ReplayErrorBoundary } from '../shell/ReplayErrorBoundary'
import {
  commitReplayPanelDrag,
  isSameReplayPanelLayout,
  isReplayPanelId,
  reconcileReplayPanelLayout,
  toggleReplayPanelPinning,
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

interface ReplayPanelExitSnapshotData {
  readonly key: string
  readonly source: HTMLElement
  readonly slot: GhostSlot | null
}

interface ReplayPanelFlipAnimationRecord {
  readonly animation: Animation
  timeout: number
  cleanup: () => void
}

const panelCollisionDetector: CollisionDetector = (input) => pointerIntersection(input) ?? pointerDistance(input)
const PANEL_ENTRY_ANIMATION_DURATION_MS = 240
const PANEL_ENTRY_ANIMATION_FALLBACK_MS = PANEL_ENTRY_ANIMATION_DURATION_MS + 60
const PANEL_EXIT_ANIMATION_DURATION_MS = 120
const PANEL_EXIT_ANIMATION_FALLBACK_MS = PANEL_EXIT_ANIMATION_DURATION_MS + 60
const FLIP_ANIMATION_DURATION_MS = 240
const FLIP_ANIMATION_FALLBACK_MS = FLIP_ANIMATION_DURATION_MS + 60

export interface ReplayPanelRect {
  readonly left: number
  readonly top: number
  readonly width: number
  readonly height: number
}

export function computeReplayPanelFlipKeyframes(from: ReplayPanelRect, to: ReplayPanelRect): Keyframe[] {
  const scaleX = from.width > 0 && to.width > 0 ? from.width / to.width : 1
  const scaleY = from.height > 0 && to.height > 0 ? from.height / to.height : 1
  return [
    { transform: `translate(${from.left - to.left}px, ${from.top - to.top}px) scale(${scaleX}, ${scaleY})` },
    { transform: 'translate(0px, 0px) scale(1, 1)' },
  ]
}

export function animateReplayPanelFlip(element: HTMLElement, from: ReplayPanelRect, to: ReplayPanelRect): Animation | null {
  if (prefersReducedMotion() || typeof element.animate !== 'function') return null
  const animation = element.animate(computeReplayPanelFlipKeyframes(from, to), {
    duration: FLIP_ANIMATION_DURATION_MS,
    easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
    fill: 'forwards',
    id: 'replay-panel-flip',
  })
  animation.finished.catch(() => undefined)
  return animation
}

/** Keeps pinning independent so unpinned subscription-based panels fully unmount. */
export function ReplayWorkspace({ panels }: ReplayWorkspaceProps) {
  const panelRegistryKey = panels.map((panel) => panel.id).join('|')
  const panelIds = useMemo(() => panels.map((panel) => panel.id), [panelRegistryKey])
  const [layout, setLayout] = useState<readonly ReplayPanelLayoutItem[]>(() => reconcileReplayPanelLayout(panelIds, []))
  const [activePanelId, setActivePanelId] = useState<ReplayPanelId | null>(null)
  const [rowSpans, setRowSpans] = useState<Readonly<Record<ReplayPanelId, number>>>({ player: 1, 'track-map': 1, leaderboard: 1, 'race-control': 1, driver: 1, telemetry: 1, 'lap-analysis': 1, strategy: 1 })
  const [columnCount, setColumnCount] = useState(() => workspaceColumnCount(typeof window === 'undefined' ? 1 : window.innerWidth))
  const [dropPreview, setDropPreview] = useState<ReplayDropPreview | null>(null)
  const [measuredGhostSlot, setMeasuredGhostSlot] = useState<GhostSlot | null>(null)
  const [isPanelManagerOpen, setPanelManagerOpen] = useState(false)
  const [entryPanelIds, setEntryPanelIds] = useState<ReadonlySet<ReplayPanelId>>(() => new Set())
  const [exitSnapshots, setExitSnapshots] = useState<readonly ReplayPanelExitSnapshotData[]>([])
  const [flipRevision, setFlipRevision] = useState(0)
  const workspaceRef = useRef<HTMLDivElement | null>(null)
  const panelManagerRef = useRef<HTMLDivElement | null>(null)
  const panelManagerToggleRef = useRef<HTMLButtonElement | null>(null)
  const panelManagerCloseRef = useRef<HTMLButtonElement | null>(null)
  const dragMoveRef = useRef<DragMoveState | null>(null)
  const dropPreviewRef = useRef<ReplayDropPreview | null>(null)
  const panelElementsRef = useRef(new Map<ReplayPanelId, HTMLElement>())
  const exitSnapshotSequenceRef = useRef(0)
  const flipFirstRectsRef = useRef<ReadonlyMap<ReplayPanelId, ReplayPanelRect>>(new Map())
  const flipAnimationsRef = useRef(new Map<ReplayPanelId, ReplayPanelFlipAnimationRecord>())

  useEffect(() => {
    setLayout((current) => {
      const reconciled = reconcileReplayPanelLayout(panelIds, current)
      return isSameReplayPanelLayout(current, reconciled) ? current : reconciled
    })
  }, [panelIds])

  const completePanelEntry = useCallback((id: ReplayPanelId) => {
    setEntryPanelIds((current) => {
      if (!current.has(id)) return current
      const next = new Set(current)
      next.delete(id)
      return next
    })
  }, [])

  const removeExitSnapshot = useCallback((key: string) => {
    setExitSnapshots((current) => current.filter((snapshot) => snapshot.key !== key))
  }, [])

  const captureExitSnapshot = useCallback((id: ReplayPanelId) => {
    const source = panelElementsRef.current.get(id)
    const workspace = workspaceRef.current
    if (source === undefined) return
    const key = `${id}-${exitSnapshotSequenceRef.current++}`
    setExitSnapshots((current) => [...current, { key, source, slot: workspace === null ? null : measureGhostSlot(source, workspace) }])
  }, [])

  const cancelFlipAnimations = useCallback(() => {
    const records = [...flipAnimationsRef.current.values()]
    records.forEach((record) => record.cleanup())
  }, [])

  const captureFlipPositions = useCallback(() => {
    const next = new Map<ReplayPanelId, ReplayPanelRect>()
    panelElementsRef.current.forEach((element, id) => next.set(id, readReplayPanelRect(element)))
    flipFirstRectsRef.current = next
  }, [])

  const togglePanelPinning = (id: ReplayPanelId) => {
    const item = layout.find((candidate) => candidate.id === id)
    if (item === undefined) return
    const shouldAnimate = !prefersReducedMotion()
    cancelFlipAnimations()
    if (shouldAnimate) captureFlipPositions()
    if (item.pinned) {
      completePanelEntry(id)
      if (shouldAnimate) captureExitSnapshot(id)
    }
    else if (shouldAnimate) setEntryPanelIds((current) => new Set([...current, id]))
    setLayout((current) => toggleReplayPanelPinning(current, id))
    if (shouldAnimate) setFlipRevision((revision) => revision + 1)
  }

  const closePanelManager = () => {
    setPanelManagerOpen(false)
    panelManagerToggleRef.current?.focus()
  }

  useEffect(() => {
    if (!isPanelManagerOpen) return
    panelManagerCloseRef.current?.focus()
    const dismissOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Node && !panelManagerRef.current?.contains(target)) setPanelManagerOpen(false)
    }
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        closePanelManager()
      }
    }
    document.addEventListener('pointerdown', dismissOnOutsidePointer)
    document.addEventListener('keydown', dismissOnEscape)
    return () => {
      document.removeEventListener('pointerdown', dismissOnOutsidePointer)
      document.removeEventListener('keydown', dismissOnEscape)
    }
  }, [isPanelManagerOpen])

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
    if (!item.pinned) return []
    const panel = panelsById.get(item.id)
    return panel === undefined ? [] : [{ panel, layout: item }]
  })

  useLayoutEffect(() => {
    const firstRects = flipFirstRectsRef.current
    flipFirstRectsRef.current = new Map()
    if (firstRects.size === 0 || prefersReducedMotion()) return

    panelElementsRef.current.forEach((element, id) => {
      const from = firstRects.get(id)
      if (from === undefined || entryPanelIds.has(id)) return
      const to = readReplayPanelRect(element)
      if (isSameReplayPanelRect(from, to)) return
      const animation = animateReplayPanelFlip(element, from, to)
      if (animation === null) return
      const record: ReplayPanelFlipAnimationRecord = { animation, cleanup: () => undefined, timeout: 0 }
      const cleanup = () => {
        if (flipAnimationsRef.current.get(id) !== record) return
        flipAnimationsRef.current.delete(id)
        window.clearTimeout(record.timeout)
        animation.removeEventListener('finish', cleanup)
        animation.cancel()
      }
      record.cleanup = cleanup
      flipAnimationsRef.current.set(id, record)
      record.timeout = window.setTimeout(cleanup, FLIP_ANIMATION_FALLBACK_MS)
      animation.addEventListener('finish', cleanup)
      animation.finished.then(cleanup, () => undefined)
    })
  }, [entryPanelIds, flipRevision])

  useEffect(() => () => cancelFlipAnimations(), [cancelFlipAnimations])

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
      if (!item.pinned) return []
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
    <>
      <div ref={panelManagerRef} className="replay-workspace__manager">
        <button ref={panelManagerToggleRef} className="replay-panel-manager-toggle" type="button" aria-expanded={isPanelManagerOpen} aria-controls="replay-panel-manager" onClick={() => setPanelManagerOpen((current) => !current)}>
          Panel Manager
        </button>
        {isPanelManagerOpen && <PanelManager panels={panels} layout={layout} closeButtonRef={(element) => { panelManagerCloseRef.current = element }} onTogglePinning={togglePanelPinning} onClose={closePanelManager} />}
      </div>
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
          {orderedPanels.length === 0
            ? <EmptyReplayWorkspace onOpenPanelManager={() => setPanelManagerOpen(true)} />
            : orderedPanels.map(({ panel, layout: item }, index) => (
              <ReplayPanelFrame key={panel.id} panel={panel} index={index} rowSpan={rowSpans[panel.id] ?? 1} desktopColumnStart={item.desktopColumnStart} isDragging={panel.id === activePanelId} isEntering={entryPanelIds.has(panel.id)} onMeasure={updateRowSpan} onPanelElement={updatePanelElement} onEntryComplete={completePanelEntry} onUnpin={() => togglePanelPinning(panel.id)} />
            ))}
          {exitSnapshots.map((snapshot) => <ReplayPanelExitSnapshot key={snapshot.key} snapshot={snapshot} onComplete={removeExitSnapshot} />)}
          {dropPreview !== null && <ReplayDropPreview preview={dropPreview} columnCount={columnCount} measuredSlot={measuredGhostSlot} />}
        </div>
        <DragOverlay className="replay-panel-drag-overlay">{(source) => {
          const id = panelIdFromSortableId(source.id)
          return id === null ? null : <ReplayPanelDragSnapshot source={panelElementsRef.current.get(id) ?? null} />
        }}</DragOverlay>
      </DragDropProvider>
    </>
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

function readReplayPanelRect(element: HTMLElement): ReplayPanelRect {
  const { height, left, top, width } = element.getBoundingClientRect()
  return { height, left, top, width }
}

function isSameReplayPanelRect(left: ReplayPanelRect, right: ReplayPanelRect): boolean {
  return left.left === right.left && left.top === right.top && left.width === right.width && left.height === right.height
}

function EmptyReplayWorkspace({ onOpenPanelManager }: { readonly onOpenPanelManager: () => void }) {
  return <div className="replay-workspace__empty" role="status">
    <h2>No panels pinned</h2>
    <p>Pin a replay panel to start building your workspace.</p>
    <button className="replay-panel-manager-action" type="button" onClick={onOpenPanelManager}>Open Panel Manager</button>
  </div>
}

function ReplayPanelExitSnapshot({ snapshot, onComplete }: { readonly snapshot: ReplayPanelExitSnapshotData; readonly onComplete: (key: string) => void }) {
  const snapshotRef = useRef<HTMLDivElement | null>(null)
  const style = snapshot.slot === null
    ? undefined
    : { height: snapshot.slot.height, left: snapshot.slot.left, top: snapshot.slot.top, width: snapshot.slot.width }

  useLayoutEffect(() => {
    const container = snapshotRef.current
    if (container === null) return
    const clone = snapshot.source.cloneNode(true) as HTMLElement
    clone.classList.remove('replay-panel-frame--drag-source', 'replay-panel-frame--entering')
    clone.inert = true
    clone.setAttribute('aria-hidden', 'true')
    container.replaceChildren(clone)
    return () => container.replaceChildren()
  }, [snapshot])

  useEffect(() => {
    const element = snapshotRef.current
    if (element === null) return
    let completed = false
    const complete = () => {
      if (completed) return
      completed = true
      onComplete(snapshot.key)
    }
    if (prefersReducedMotion()) {
      complete()
      return
    }
    const handleAnimationEnd = (event: AnimationEvent) => {
      if (event.target === element) complete()
    }
    element.addEventListener('animationend', handleAnimationEnd)
    const fallback = window.setTimeout(complete, PANEL_EXIT_ANIMATION_FALLBACK_MS)
    return () => {
      element.removeEventListener('animationend', handleAnimationEnd)
      window.clearTimeout(fallback)
    }
  }, [onComplete, snapshot.key])

  return <div ref={snapshotRef} className="replay-panel-exit-snapshot" style={style} aria-hidden="true" inert />
}

function PanelManager({ panels, layout, closeButtonRef, onTogglePinning, onClose }: { readonly panels: readonly ReplayWorkspacePanel[]; readonly layout: readonly ReplayPanelLayoutItem[]; readonly closeButtonRef: (element: HTMLButtonElement | null) => void; readonly onTogglePinning: (id: ReplayPanelId) => void; readonly onClose: () => void }) {
  return <div id="replay-panel-manager" className="replay-panel-manager" role="dialog" aria-labelledby="replay-panel-manager-title">
    <div className="replay-panel-manager__header">
      <h2 id="replay-panel-manager-title">Panel Manager</h2>
      <button ref={closeButtonRef} className="replay-panel-manager__close" type="button" aria-label="Close Panel Manager" onClick={onClose}>×</button>
    </div>
    <p className="replay-panel-manager__description">Choose which replay panels appear in the workspace.</p>
    <ul className="replay-panel-manager__list">
      {panels.map((panel) => {
        const item = layout.find((candidate) => candidate.id === panel.id)
        if (item === undefined) return null
        return <li key={panel.id}>
          <button className="replay-panel-manager__item" type="button" aria-pressed={item.pinned} aria-label={`${item.pinned ? 'Unpin' : 'Pin'} ${panel.label} panel`} title={`${item.pinned ? 'Unpin' : 'Pin'} ${panel.label} panel`} onClick={() => onTogglePinning(panel.id)}>
            <span className="replay-panel-manager__label">{panel.label}</span>
            <PanelPinIcon pinned={item.pinned} />
          </button>
        </li>
      })}
    </ul>
  </div>
}

function ReplayPanelFrame({ panel, index, rowSpan, desktopColumnStart, isDragging, isEntering, onMeasure, onPanelElement, onEntryComplete, onUnpin }: { readonly panel: ReplayWorkspacePanel; readonly index: number; readonly rowSpan: number; readonly desktopColumnStart: number; readonly isDragging: boolean; readonly isEntering: boolean; readonly onMeasure: (id: ReplayPanelId, height: number) => void; readonly onPanelElement: (id: ReplayPanelId, element: HTMLElement | null) => void; readonly onEntryComplete: (id: ReplayPanelId) => void; readonly onUnpin: () => void }) {
  const style: ReplayPanelFrameStyle = {
    '--replay-panel-columns': panel.columns,
    '--replay-panel-row-span': rowSpan,
    '--replay-panel-tablet-column': responsiveColumnStart(desktopColumnStart, panel.columns, 2),
    '--replay-panel-desktop-column': responsiveColumnStart(desktopColumnStart, panel.columns, 4),
  }
  return <SortablePanel id={panel.id} index={index} className="replay-panel-frame" style={style} label={panel.label} isDragging={isDragging} isEntering={isEntering} onMeasure={onMeasure} onPanelElement={onPanelElement} onEntryComplete={onEntryComplete} onUnpin={onUnpin}>
    <div className="replay-panel-frame__body"><ReplayErrorBoundary label={`${panel.label} panel`}>{panel.element}</ReplayErrorBoundary></div>
  </SortablePanel>
}

function SortablePanel({ id, index, className, style, label, isDragging, isEntering, onMeasure, onPanelElement, onEntryComplete, onUnpin, children }: { readonly id: ReplayPanelId; readonly index: number; readonly className: string; readonly style: CSSProperties; readonly label: string; readonly isDragging: boolean; readonly isEntering: boolean; readonly onMeasure: (id: ReplayPanelId, height: number) => void; readonly onPanelElement: (id: ReplayPanelId, element: HTMLElement | null) => void; readonly onEntryComplete: (id: ReplayPanelId) => void; readonly onUnpin: () => void; readonly children: ReactNode }) {
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

  useEffect(() => {
    if (!isEntering) return
    const element = elementRef.current
    if (element === null) {
      onEntryComplete(id)
      return
    }
    let completed = false
    const complete = () => {
      if (completed) return
      completed = true
      onEntryComplete(id)
    }
    if (prefersReducedMotion()) {
      complete()
      return
    }
    const handleAnimationEnd = (event: AnimationEvent) => {
      if (event.target === element) complete()
    }
    element.addEventListener('animationend', handleAnimationEnd)
    const fallback = window.setTimeout(complete, PANEL_ENTRY_ANIMATION_FALLBACK_MS)
    return () => {
      element.removeEventListener('animationend', handleAnimationEnd)
      window.clearTimeout(fallback)
    }
  }, [id, isEntering, onEntryComplete])

  const setPanelRef = (element: HTMLElement | null) => {
    elementRef.current = element
    ref(element)
    onPanelElement(id, element)
  }

  return <section ref={setPanelRef} className={`${className}${isEntering ? ' replay-panel-frame--entering' : ''}${isDragging || isDropping ? ' replay-panel-frame--drag-source' : ''}`} style={style} aria-label={label}>
    <header className="replay-panel-frame__header">
      <button ref={handleRef} className="replay-panel-drag-handle" type="button" aria-label={`Move ${label} panel`}><DragHandleIcon /> <span>{label}</span></button>
      <button className="replay-panel-unpin" type="button" aria-label={`Unpin ${label} panel`} title={`Unpin ${label} panel`} onClick={onUnpin}><PanelPinIcon /></button>
    </header>
    {children}
  </section>
}

function DragHandleIcon() {
  return <svg aria-hidden="true" focusable="false" viewBox="0 0 16 16">
    <path d="M4 2h2v2H4V2Zm6 0h2v2h-2V2ZM4 7h2v2H4V7Zm6 0h2v2h-2V7Zm-6 5h2v2H4v-2Zm6 0h2v2h-2v-2Z" />
  </svg>
}

function PanelPinIcon({ pinned = false }: { readonly pinned?: boolean }) {
  return <svg aria-hidden="true" focusable="false" viewBox="0 0 16 16">
    <path d="M5 2h6L10 6l2 2v1H8.5v5h-1V9H4V8l2-2-1-4Z" />
    {!pinned && <path d="m2.5 2.5 11 11" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.5" />}
  </svg>
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function panelIdFromSortableId(value: unknown): ReplayPanelId | null {
  return isReplayPanelId(value) ? value : null
}
