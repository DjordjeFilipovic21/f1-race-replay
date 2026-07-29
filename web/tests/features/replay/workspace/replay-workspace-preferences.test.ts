import { expect, test, vi } from 'vitest'
import { createDefaultReplayPanelLayout, type ReplayPanelLayoutItem } from '../../../../src/features/replay/workspace/replay-panel-layout'
import {
  loadReplayWorkspacePreferences,
  loadReplayWorkspaceLayout,
  REPLAY_WORKSPACE_PREFERENCES_STORAGE_KEY,
  saveReplayWorkspacePreferences,
  saveReplayWorkspaceLayout,
  type ReplayWorkspaceStorage,
} from '../../../../src/features/replay/workspace/replay-workspace-preferences'

const panelIds = ['race-control', 'track-map', 'player'] as const

test('loads and reconciles a versioned custom layout', () => {
  const storage = memoryStorage(JSON.stringify({
    version: 1,
    layout: [
      { id: 'player', pinned: false, desktopColumnStart: 3 },
      { id: 'removed-panel', pinned: true, desktopColumnStart: 1 },
      { id: 'race-control', pinned: true, desktopColumnStart: 2 },
    ],
  }))

  expect(loadReplayWorkspaceLayout(panelIds, storage)).toEqual([
    { id: 'player', pinned: false, desktopColumnStart: 3 },
    { id: 'race-control', pinned: true, desktopColumnStart: 2 },
    { id: 'track-map', pinned: true, desktopColumnStart: 2 },
  ])
})

test('loads a persisted lock mode and defaults legacy preferences to unlocked', () => {
  const layout = [{ id: 'player', pinned: false, desktopColumnStart: 3 }] as const

  expect(loadReplayWorkspacePreferences(panelIds, memoryStorage(JSON.stringify({ version: 1, layout, mode: 'locked' })))).toEqual({
    layout: [
      { id: 'player', pinned: false, desktopColumnStart: 3 },
      { id: 'race-control', pinned: true, desktopColumnStart: 1 },
      { id: 'track-map', pinned: true, desktopColumnStart: 2 },
    ],
    mode: 'locked',
  })
  expect(loadReplayWorkspacePreferences(panelIds, memoryStorage(JSON.stringify({ version: 1, layout })))).toEqual({
    layout: [
      { id: 'player', pinned: false, desktopColumnStart: 3 },
      { id: 'race-control', pinned: true, desktopColumnStart: 1 },
      { id: 'track-map', pinned: true, desktopColumnStart: 2 },
    ],
    mode: 'unlocked',
  })
})

test('falls back to default for missing, malformed, or unsupported preferences', () => {
  const expected = createDefaultReplayPanelLayout(panelIds)

  expect(loadReplayWorkspacePreferences(panelIds, memoryStorage(null))).toEqual({ layout: expected, mode: 'unlocked' })
  expect(loadReplayWorkspaceLayout(panelIds, memoryStorage(null))).toEqual(expected)
  expect(loadReplayWorkspaceLayout(panelIds, memoryStorage('{invalid'))).toEqual(expected)
  expect(loadReplayWorkspaceLayout(panelIds, memoryStorage(JSON.stringify({ version: 2, layout: [] })))).toEqual(expected)
  expect(loadReplayWorkspaceLayout(panelIds, memoryStorage(JSON.stringify({ version: 1, layout: [{ id: 'player' }] })))).toEqual(expected)
})

test('tolerates storage access errors', () => {
  const failingStorage: ReplayWorkspaceStorage = {
    getItem: vi.fn(() => { throw new Error('blocked') }),
    setItem: vi.fn(() => { throw new Error('full') }),
  }

  expect(loadReplayWorkspacePreferences(panelIds, failingStorage)).toEqual({
    layout: createDefaultReplayPanelLayout(panelIds),
    mode: 'unlocked',
  })
  expect(saveReplayWorkspaceLayout(createDefaultReplayPanelLayout(panelIds), failingStorage)).toBe(false)
  expect(saveReplayWorkspacePreferences(createDefaultReplayPanelLayout(panelIds), 'locked', failingStorage)).toBe(false)
})

test('saves the committed layout with its schema version', () => {
  const setItem = vi.fn()
  const storage: ReplayWorkspaceStorage = { getItem: () => null, setItem }
  const layout: readonly ReplayPanelLayoutItem[] = [{ id: 'player', pinned: false, desktopColumnStart: 4 }]

  expect(saveReplayWorkspaceLayout(layout, storage)).toBe(true)
  expect(setItem).toHaveBeenCalledWith(REPLAY_WORKSPACE_PREFERENCES_STORAGE_KEY, JSON.stringify({ version: 1, layout }))
})

test('saves layout and mode together with the existing schema version', () => {
  const setItem = vi.fn()
  const storage: ReplayWorkspaceStorage = { getItem: () => null, setItem }
  const layout: readonly ReplayPanelLayoutItem[] = [{ id: 'player', pinned: true, desktopColumnStart: 4 }]

  expect(saveReplayWorkspacePreferences(layout, 'locked', storage)).toBe(true)
  expect(setItem).toHaveBeenCalledWith(REPLAY_WORKSPACE_PREFERENCES_STORAGE_KEY, JSON.stringify({ version: 1, layout, mode: 'locked' }))
})

function memoryStorage(value: string | null): ReplayWorkspaceStorage {
  return { getItem: () => value, setItem: () => undefined }
}
