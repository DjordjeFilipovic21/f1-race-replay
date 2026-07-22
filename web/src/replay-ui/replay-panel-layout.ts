export type ReplayPanelId = 'player' | 'track-map' | 'leaderboard' | 'driver'

export function isReplayPanelId(value: unknown): value is ReplayPanelId {
  return value === 'player' || value === 'track-map' || value === 'leaderboard' || value === 'driver'
}

export interface ReplayPanelLayoutItem {
  readonly id: ReplayPanelId
  readonly visible: boolean
  readonly desktopColumnStart: number
}

export interface ReplayPanelDragCommit {
  readonly id: ReplayPanelId
  readonly index: number
  readonly desktopColumnStart?: number | null
}

export function defaultReplayPanelColumn(id: ReplayPanelId): number {
  return id === 'track-map' ? 2 : id === 'leaderboard' ? 4 : 1
}

/** Reconciles panel registry changes without replacing a user's local layout choices. */
export function reconcileReplayPanelLayout(panelIds: readonly ReplayPanelId[], layout: readonly ReplayPanelLayoutItem[]): readonly ReplayPanelLayoutItem[] {
  const registeredIds = new Set(panelIds)
  const retained = layout.filter((item, index) => registeredIds.has(item.id) && layout.findIndex(({ id }) => id === item.id) === index)
  const retainedIds = new Set(retained.map(({ id }) => id))
  return [
    ...retained.map((item) => ({ ...item, desktopColumnStart: normalizeDesktopColumn(item.desktopColumnStart, item.id) })),
    ...panelIds.filter((id) => !retainedIds.has(id)).map((id) => ({ id, visible: true, desktopColumnStart: defaultReplayPanelColumn(id) })),
  ]
}

export function toggleReplayPanelVisibility(layout: readonly ReplayPanelLayoutItem[], id: ReplayPanelId): readonly ReplayPanelLayoutItem[] {
  return layout.map((item) => item.id === id ? { ...item, visible: !item.visible } : item)
}

export function reorderReplayPanelLayout(layout: readonly ReplayPanelLayoutItem[], id: ReplayPanelId, destinationIndex: number): readonly ReplayPanelLayoutItem[] {
  const sourceIndex = layout.findIndex((item) => item.id === id)
  if (sourceIndex < 0) return layout
  const next = [...layout]
  const [item] = next.splice(sourceIndex, 1)
  next.splice(clampIndex(destinationIndex, next.length), 0, item)
  return next
}

/** Applies the sortable workspace index to the canonical panel order. */
export function commitReplayPanelDrag(layout: readonly ReplayPanelLayoutItem[], commit: ReplayPanelDragCommit): readonly ReplayPanelLayoutItem[] {
  const reordered = reorderReplayPanelLayout(layout, commit.id, commit.index)
  const desktopColumnStart = commit.desktopColumnStart
  if (desktopColumnStart === null || desktopColumnStart === undefined) return reordered
  return reordered.map((item) => item.id === commit.id ? { ...item, desktopColumnStart: normalizeDesktopColumn(desktopColumnStart, item.id) } : item)
}

export function isSameReplayPanelLayout(left: readonly ReplayPanelLayoutItem[], right: readonly ReplayPanelLayoutItem[]): boolean {
  return left.length === right.length && left.every((item, index) => item.id === right[index]?.id && item.visible === right[index]?.visible && item.desktopColumnStart === right[index]?.desktopColumnStart)
}

function clampIndex(index: number, length: number): number {
  return Number.isInteger(index) ? Math.min(Math.max(index, 0), length) : length
}

function normalizeDesktopColumn(value: number, id: ReplayPanelId): number {
  return Number.isInteger(value) ? Math.min(Math.max(value, 1), id === 'track-map' ? 3 : 4) : defaultReplayPanelColumn(id)
}
