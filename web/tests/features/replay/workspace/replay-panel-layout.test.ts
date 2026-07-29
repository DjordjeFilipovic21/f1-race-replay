import { expect, test } from 'vitest'
import {
  commitReplayPanelDrag,
  createDefaultReplayPanelLayout,
  defaultReplayPanelColumn,
  isDefaultReplayPanelLayout,
  isReplayPanelId,
  REPLAY_PANEL_DEFAULT_LAYOUT,
  replayPanelColumns,
  reconcileReplayPanelLayout,
  toggleReplayPanelPinning,
  type ReplayPanelLayoutItem,
} from '../../../../src/features/replay/workspace/replay-panel-layout'

const layout: readonly ReplayPanelLayoutItem[] = [
  { id: 'player', pinned: true, desktopColumnStart: 1 },
  { id: 'track-map', pinned: false, desktopColumnStart: 2 },
  { id: 'leaderboard', pinned: true, desktopColumnStart: 4 },
  { id: 'driver', pinned: true, desktopColumnStart: 3 },
]

test('reorders the displayed pinned subset while leaving unpinned panels in place', () => {
  expect(commitReplayPanelDrag(layout, { id: 'leaderboard', index: 0 }).map(({ id }) => id)).toEqual([
    'leaderboard', 'track-map', 'player', 'driver',
  ])
})

test('validates sortable IDs against the registered panel IDs', () => {
  expect(isReplayPanelId('driver')).toBe(true)
  expect(isReplayPanelId('telemetry')).toBe(true)
  expect(isReplayPanelId('race-control')).toBe(true)
  expect(isReplayPanelId('lap-analysis')).toBe(true)
  expect(isReplayPanelId('strategy')).toBe(true)
})

test('registers the analysis panels with their correct column widths', () => {
  expect(replayPanelColumns('lap-analysis')).toBe(1)
  expect(replayPanelColumns('strategy')).toBe(2)
})

test('updates the dragged panel column while retaining canonical sortable order', () => {
  const updated = commitReplayPanelDrag(layout, { id: 'driver', index: 1, desktopColumnStart: 4 })

  expect(updated.map(({ id }) => id)).toEqual(['player', 'track-map', 'driver', 'leaderboard'])
  expect(updated.find(({ id }) => id === 'driver')?.desktopColumnStart).toBe(4)
})

test('uses semantic default desktop columns for the registered panels', () => {
  expect([defaultReplayPanelColumn('player'), defaultReplayPanelColumn('track-map'), defaultReplayPanelColumn('leaderboard'), defaultReplayPanelColumn('race-control'), defaultReplayPanelColumn('driver'), defaultReplayPanelColumn('telemetry'), defaultReplayPanelColumn('lap-analysis'), defaultReplayPanelColumn('strategy')]).toEqual([4, 2, 1, 1, 4, 2, 4, 2])
})

test('defines a stable default layout independently of panel registry order', () => {
  expect(createDefaultReplayPanelLayout(['strategy', 'player', 'leaderboard'])).toEqual([
    { id: 'player', pinned: true, desktopColumnStart: 4 },
    { id: 'leaderboard', pinned: true, desktopColumnStart: 1 },
    { id: 'strategy', pinned: true, desktopColumnStart: 2 },
  ])
  expect(REPLAY_PANEL_DEFAULT_LAYOUT.map(({ id }) => id)).toEqual([
    'race-control', 'track-map', 'player', 'leaderboard', 'driver', 'lap-analysis', 'telemetry', 'strategy',
  ])
})

test('distinguishes the default layout from user layout choices', () => {
  const panelIds = ['player', 'track-map', 'leaderboard'] as const
  const defaultLayout = createDefaultReplayPanelLayout(panelIds)

  expect(isDefaultReplayPanelLayout(panelIds, defaultLayout)).toBe(true)
  expect(isDefaultReplayPanelLayout(panelIds, toggleReplayPanelPinning(defaultLayout, 'track-map'))).toBe(false)
  expect(isDefaultReplayPanelLayout(panelIds, commitReplayPanelDrag(defaultLayout, { id: 'leaderboard', index: 0 }))).toBe(false)
})

test('reorders only the displayed pinned subset while retaining unpinned slots', () => {
  const reordered = commitReplayPanelDrag(layout, { id: 'track-map', index: 0 })
  expect(reordered).toEqual(layout)
  expect(toggleReplayPanelPinning(reordered, 'track-map')).toEqual([
    { id: 'player', pinned: true, desktopColumnStart: 1 },
    { id: 'track-map', pinned: true, desktopColumnStart: 2 },
    { id: 'leaderboard', pinned: true, desktopColumnStart: 4 },
    { id: 'driver', pinned: true, desktopColumnStart: 3 },
  ])
})

test('reconciles a changed registry while retaining known pinning and order', () => {
  expect(reconcileReplayPanelLayout(['leaderboard', 'player', 'driver'] as const, layout)).toEqual([
    { id: 'player', pinned: true, desktopColumnStart: 1 },
    { id: 'leaderboard', pinned: true, desktopColumnStart: 4 },
    { id: 'driver', pinned: true, desktopColumnStart: 3 },
  ])
})

test('adds the two-column telemetry panel to legacy layouts without changing saved choices', () => {
  expect(reconcileReplayPanelLayout(['player', 'track-map', 'leaderboard', 'driver', 'telemetry'] as const, layout)).toEqual([
    { id: 'player', pinned: true, desktopColumnStart: 1 },
    { id: 'track-map', pinned: false, desktopColumnStart: 2 },
    { id: 'leaderboard', pinned: true, desktopColumnStart: 4 },
    { id: 'driver', pinned: true, desktopColumnStart: 3 },
    { id: 'telemetry', pinned: true, desktopColumnStart: 2 },
  ])
})

test('adds and removes the new panels while retaining registered layout choices', () => {
  const withNewPanels = reconcileReplayPanelLayout(['player', 'lap-analysis', 'strategy'] as const, layout)
  expect(withNewPanels).toEqual([
    { id: 'player', pinned: true, desktopColumnStart: 1 },
    { id: 'lap-analysis', pinned: true, desktopColumnStart: 4 },
    { id: 'strategy', pinned: true, desktopColumnStart: 2 },
  ])
  expect(reconcileReplayPanelLayout(['player'] as const, withNewPanels)).toEqual([
    { id: 'player', pinned: true, desktopColumnStart: 1 },
  ])
})

test('clamps every registered panel column start to a valid desktop position', () => {
  const layoutWithMixedPanels = [
    ...layout,
    { id: 'telemetry', pinned: true, desktopColumnStart: 1 },
    { id: 'lap-analysis', pinned: true, desktopColumnStart: 1 },
    { id: 'strategy', pinned: true, desktopColumnStart: 1 },
  ] as const
  const reconciled = reconcileReplayPanelLayout(['lap-analysis', 'strategy'] as const, layoutWithMixedPanels)

  expect(reconciled).toEqual([
    { id: 'lap-analysis', pinned: true, desktopColumnStart: 1 },
    { id: 'strategy', pinned: true, desktopColumnStart: 1 },
  ])
  expect(commitReplayPanelDrag(layoutWithMixedPanels, { id: 'lap-analysis', index: 4, desktopColumnStart: 4 }).find(({ id }) => id === 'lap-analysis')?.desktopColumnStart).toBe(4)
  expect(commitReplayPanelDrag(layoutWithMixedPanels, { id: 'strategy', index: 5, desktopColumnStart: 0 }).find(({ id }) => id === 'strategy')?.desktopColumnStart).toBe(1)
})
