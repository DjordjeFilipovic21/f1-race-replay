export type ReplayPanelId = 'player' | 'track-map' | 'leaderboard' | 'race-control' | 'weather' | 'driver' | 'telemetry' | 'lap-analysis' | 'strategy' | 'pit-loss-position' | 'local-video'
export const LOCAL_VIDEO_PANEL_ID: ReplayPanelId = 'local-video'

const DESKTOP_WORKSPACE_COLUMNS = 4

const REPLAY_PANEL_COLUMNS: Readonly<Record<ReplayPanelId, 1 | 2>> = {
  player: 1,
  'track-map': 2,
  leaderboard: 1,
  'race-control': 1,
  weather: 1,
  driver: 1,
  telemetry: 1,
  'lap-analysis': 1,
  strategy: 2,
  'pit-loss-position': 1,
  'local-video': 2,
}

const REPLAY_PANEL_DEFAULT_COLUMNS: Readonly<Record<ReplayPanelId, number>> = {
  player: 4,
  'track-map': 2,
  leaderboard: 1,
  'race-control': 1,
  weather: 4,
  driver: 4,
  telemetry: 2,
  'lap-analysis': 4,
  strategy: 2,
  'pit-loss-position': 4,
  'local-video': 2,
}

export const REPLAY_PANEL_DEFAULT_LAYOUT: readonly ReplayPanelLayoutItem[] = Object.freeze([
  { id: 'race-control', pinned: true, desktopColumnStart: REPLAY_PANEL_DEFAULT_COLUMNS['race-control'] },
  { id: 'weather', pinned: true, desktopColumnStart: REPLAY_PANEL_DEFAULT_COLUMNS.weather },
  { id: 'track-map', pinned: true, desktopColumnStart: REPLAY_PANEL_DEFAULT_COLUMNS['track-map'] },
  { id: 'player', pinned: true, desktopColumnStart: REPLAY_PANEL_DEFAULT_COLUMNS.player },
  { id: 'leaderboard', pinned: true, desktopColumnStart: REPLAY_PANEL_DEFAULT_COLUMNS.leaderboard },
  { id: 'driver', pinned: true, desktopColumnStart: REPLAY_PANEL_DEFAULT_COLUMNS.driver },
  { id: 'lap-analysis', pinned: true, desktopColumnStart: REPLAY_PANEL_DEFAULT_COLUMNS['lap-analysis'] },
  { id: 'telemetry', pinned: true, desktopColumnStart: REPLAY_PANEL_DEFAULT_COLUMNS.telemetry },
  { id: 'strategy', pinned: true, desktopColumnStart: REPLAY_PANEL_DEFAULT_COLUMNS.strategy },
  { id: 'pit-loss-position', pinned: true, desktopColumnStart: REPLAY_PANEL_DEFAULT_COLUMNS['pit-loss-position'] },
  { id: LOCAL_VIDEO_PANEL_ID, pinned: false, desktopColumnStart: REPLAY_PANEL_DEFAULT_COLUMNS['local-video'] },
])

export function isReplayPanelId(value: unknown): value is ReplayPanelId {
  return value === 'player' || value === 'track-map' || value === 'leaderboard' || value === 'race-control' || value === 'weather' || value === 'driver' || value === 'telemetry' || value === 'lap-analysis' || value === 'strategy' || value === 'pit-loss-position' || value === LOCAL_VIDEO_PANEL_ID
}

export interface ReplayPanelLayoutItem {
  readonly id: ReplayPanelId
  readonly pinned: boolean
  readonly desktopColumnStart: number
}

export interface ReplayPanelDragCommit {
  readonly id: ReplayPanelId
  readonly index: number
  readonly desktopColumnStart?: number | null
}

export function defaultReplayPanelColumn(id: ReplayPanelId): number {
  return REPLAY_PANEL_DEFAULT_COLUMNS[id]
}

export function replayPanelColumns(id: ReplayPanelId): 1 | 2 {
  return REPLAY_PANEL_COLUMNS[id]
}

export function createDefaultReplayPanelLayout(panelIds: readonly ReplayPanelId[]): readonly ReplayPanelLayoutItem[] {
  const registeredIds = new Set(panelIds)
  return REPLAY_PANEL_DEFAULT_LAYOUT.filter(({ id }) => registeredIds.has(id))
}

export function isDefaultReplayPanelLayout(panelIds: readonly ReplayPanelId[], layout: readonly ReplayPanelLayoutItem[]): boolean {
  return isSameReplayPanelLayout(createDefaultReplayPanelLayout(panelIds), reconcileReplayPanelLayout(panelIds, layout))
}

/** Reconciles panel registry changes without replacing a user's local layout choices. */
export function reconcileReplayPanelLayout(panelIds: readonly ReplayPanelId[], layout: readonly ReplayPanelLayoutItem[]): readonly ReplayPanelLayoutItem[] {
  const registeredIds = new Set(panelIds)
  const retained = layout.filter((item, index) => registeredIds.has(item.id) && layout.findIndex(({ id }) => id === item.id) === index)
  const retainedIds = new Set(retained.map(({ id }) => id))
  return [
    ...retained.map((item) => ({ ...item, desktopColumnStart: normalizeDesktopColumn(item.desktopColumnStart, item.id) })),
    ...createDefaultReplayPanelLayout(panelIds).filter(({ id }) => !retainedIds.has(id)),
  ]
}

export function toggleReplayPanelPinning(layout: readonly ReplayPanelLayoutItem[], id: ReplayPanelId): readonly ReplayPanelLayoutItem[] {
  return layout.map((item) => item.id === id ? { ...item, pinned: !item.pinned } : item)
}

/** Reorders the displayed pinned subset while leaving unpinned slots unchanged. */
export function reorderReplayPanelLayout(layout: readonly ReplayPanelLayoutItem[], id: ReplayPanelId, destinationIndex: number): readonly ReplayPanelLayoutItem[] {
  const pinned = layout.filter((item) => item.pinned)
  const sourceIndex = pinned.findIndex((item) => item.id === id)
  if (sourceIndex < 0) return layout
  const nextPinned = [...pinned]
  const [item] = nextPinned.splice(sourceIndex, 1)
  nextPinned.splice(clampIndex(destinationIndex, nextPinned.length), 0, item)
  let pinnedIndex = 0
  return layout.map((current) => current.pinned ? nextPinned[pinnedIndex++] : current)
}

/** Applies the sortable workspace index to the canonical panel order. */
export function commitReplayPanelDrag(layout: readonly ReplayPanelLayoutItem[], commit: ReplayPanelDragCommit): readonly ReplayPanelLayoutItem[] {
  const reordered = reorderReplayPanelLayout(layout, commit.id, commit.index)
  const desktopColumnStart = commit.desktopColumnStart
  if (desktopColumnStart === null || desktopColumnStart === undefined) return reordered
  return reordered.map((item) => item.id === commit.id ? { ...item, desktopColumnStart: normalizeDesktopColumn(desktopColumnStart, item.id) } : item)
}

export function isSameReplayPanelLayout(left: readonly ReplayPanelLayoutItem[], right: readonly ReplayPanelLayoutItem[]): boolean {
  return left.length === right.length && left.every((item, index) => item.id === right[index]?.id && item.pinned === right[index]?.pinned && item.desktopColumnStart === right[index]?.desktopColumnStart)
}

function clampIndex(index: number, length: number): number {
  return Number.isInteger(index) ? Math.min(Math.max(index, 0), length) : length
}

function normalizeDesktopColumn(value: number, id: ReplayPanelId): number {
  const maximumColumnStart = DESKTOP_WORKSPACE_COLUMNS - replayPanelColumns(id) + 1
  return Number.isInteger(value) ? Math.min(Math.max(value, 1), maximumColumnStart) : defaultReplayPanelColumn(id)
}
