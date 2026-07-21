export type ReplayPanelId = 'player' | 'track-map' | 'leaderboard'

export interface ReplayPanelLayoutItem {
  readonly id: ReplayPanelId
  readonly visible: boolean
}

export interface ReplayPanelDragCommit {
  readonly id: ReplayPanelId
  readonly index: number
}

/** Reconciles panel registry changes without replacing a user's local layout choices. */
export function reconcileReplayPanelLayout(panelIds: readonly ReplayPanelId[], layout: readonly ReplayPanelLayoutItem[]): readonly ReplayPanelLayoutItem[] {
  const registeredIds = new Set(panelIds)
  const retained = layout.filter((item, index) => registeredIds.has(item.id) && layout.findIndex(({ id }) => id === item.id) === index)
  const retainedIds = new Set(retained.map(({ id }) => id))
  return [...retained, ...panelIds.filter((id) => !retainedIds.has(id)).map((id) => ({ id, visible: true }))]
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
  return reorderReplayPanelLayout(layout, commit.id, commit.index)
}

export function isSameReplayPanelLayout(left: readonly ReplayPanelLayoutItem[], right: readonly ReplayPanelLayoutItem[]): boolean {
  return left.length === right.length && left.every((item, index) => item.id === right[index]?.id && item.visible === right[index]?.visible)
}

function clampIndex(index: number, length: number): number {
  return Number.isInteger(index) ? Math.min(Math.max(index, 0), length) : length
}
