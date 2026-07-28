import {
  createDefaultReplayPanelLayout,
  isReplayPanelId,
  reconcileReplayPanelLayout,
  type ReplayPanelId,
  type ReplayPanelLayoutItem,
} from './replay-panel-layout'

export const REPLAY_WORKSPACE_PREFERENCES_STORAGE_KEY = 'f1-race-replay:workspace-preferences'

const REPLAY_WORKSPACE_PREFERENCES_VERSION = 1

export type ReplayWorkspaceMode = 'locked' | 'unlocked'

export interface ReplayWorkspaceStorage {
  readonly getItem: (key: string) => string | null
  readonly setItem: (key: string, value: string) => void
}

export interface ReplayWorkspacePreferences {
  readonly version: typeof REPLAY_WORKSPACE_PREFERENCES_VERSION
  readonly layout: readonly ReplayPanelLayoutItem[]
  readonly mode: ReplayWorkspaceMode
}

export interface ReplayWorkspacePreferencesState {
  readonly layout: readonly ReplayPanelLayoutItem[]
  readonly mode: ReplayWorkspaceMode
}

export function loadReplayWorkspacePreferences(
  panelIds: readonly ReplayPanelId[],
  storage: ReplayWorkspaceStorage | null = browserStorage(),
): ReplayWorkspacePreferencesState {
  if (storage === null) return defaultPreferences(panelIds)
  try {
    const stored = storage.getItem(REPLAY_WORKSPACE_PREFERENCES_STORAGE_KEY)
    if (stored === null) return defaultPreferences(panelIds)
    const preferences = parseReplayWorkspacePreferences(JSON.parse(stored))
    if (preferences === null) return defaultPreferences(panelIds)
    return {
      layout: reconcileReplayPanelLayout(panelIds, preferences.layout),
      mode: preferences.mode,
    }
  } catch {
    return defaultPreferences(panelIds)
  }
}

export function loadReplayWorkspaceLayout(
  panelIds: readonly ReplayPanelId[],
  storage: ReplayWorkspaceStorage | null = browserStorage(),
): readonly ReplayPanelLayoutItem[] {
  return loadReplayWorkspacePreferences(panelIds, storage).layout
}

export function saveReplayWorkspacePreferences(
  layout: readonly ReplayPanelLayoutItem[],
  mode: ReplayWorkspaceMode,
  storage: ReplayWorkspaceStorage | null = browserStorage(),
): boolean {
  if (storage === null) return false
  const preferences: ReplayWorkspacePreferences = { version: REPLAY_WORKSPACE_PREFERENCES_VERSION, layout, mode }
  try {
    storage.setItem(REPLAY_WORKSPACE_PREFERENCES_STORAGE_KEY, JSON.stringify(preferences))
    return true
  } catch {
    return false
  }
}

export function saveReplayWorkspaceLayout(
  layout: readonly ReplayPanelLayoutItem[],
  storage: ReplayWorkspaceStorage | null = browserStorage(),
): boolean {
  if (storage === null) return false
  const preferences = { version: REPLAY_WORKSPACE_PREFERENCES_VERSION, layout }
  try {
    storage.setItem(REPLAY_WORKSPACE_PREFERENCES_STORAGE_KEY, JSON.stringify(preferences))
    return true
  } catch {
    return false
  }
}

function parseReplayWorkspacePreferences(value: unknown): ReplayWorkspacePreferencesState | null {
  if (!isRecord(value) || value.version !== REPLAY_WORKSPACE_PREFERENCES_VERSION || !Array.isArray(value.layout)) return null
  const layout: ReplayPanelLayoutItem[] = []
  for (const item of value.layout) {
    if (!isRecord(item) || typeof item.pinned !== 'boolean' || !Number.isInteger(item.desktopColumnStart)) return null
    if (!isReplayPanelId(item.id)) continue
    layout.push({ id: item.id, pinned: item.pinned, desktopColumnStart: item.desktopColumnStart as number })
  }
  return { layout, mode: value.mode === 'locked' ? 'locked' : 'unlocked' }
}

function defaultPreferences(panelIds: readonly ReplayPanelId[]): ReplayWorkspacePreferencesState {
  return { layout: createDefaultReplayPanelLayout(panelIds), mode: 'unlocked' }
}

function browserStorage(): ReplayWorkspaceStorage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage ?? null
  } catch {
    return null
  }
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
