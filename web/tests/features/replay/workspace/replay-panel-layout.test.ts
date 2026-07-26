import { expect, test } from 'vitest'
import {
  commitReplayPanelDrag,
  defaultReplayPanelColumn,
  isReplayPanelId,
  replayPanelColumns,
  reconcileReplayPanelLayout,
  toggleReplayPanelVisibility,
  type ReplayPanelLayoutItem,
} from '../../../../src/features/replay/workspace/replay-panel-layout'

const layout: readonly ReplayPanelLayoutItem[] = [
  { id: 'player', visible: true, desktopColumnStart: 1 },
  { id: 'track-map', visible: false, desktopColumnStart: 2 },
  { id: 'leaderboard', visible: true, desktopColumnStart: 4 },
  { id: 'driver', visible: true, desktopColumnStart: 3 },
]

test('keeps array position as the canonical workspace reorder order', () => {
  expect(commitReplayPanelDrag(layout, { id: 'leaderboard', index: 0 }).map(({ id }) => id)).toEqual([
    'leaderboard', 'player', 'track-map', 'driver',
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

  expect(updated.map(({ id }) => id)).toEqual(['player', 'driver', 'track-map', 'leaderboard'])
  expect(updated.find(({ id }) => id === 'driver')?.desktopColumnStart).toBe(4)
})

test('uses semantic default desktop columns for the registered panels', () => {
  expect([defaultReplayPanelColumn('player'), defaultReplayPanelColumn('track-map'), defaultReplayPanelColumn('leaderboard'), defaultReplayPanelColumn('race-control'), defaultReplayPanelColumn('driver'), defaultReplayPanelColumn('telemetry'), defaultReplayPanelColumn('lap-analysis'), defaultReplayPanelColumn('strategy')]).toEqual([1, 2, 4, 1, 1, 1, 1, 1])
})

test('keeps a collapsed panel in the canonical order when it is shown', () => {
  const reordered = commitReplayPanelDrag(layout, { id: 'track-map', index: 0 })
  expect(toggleReplayPanelVisibility(reordered, 'track-map')).toEqual([
    { id: 'track-map', visible: true, desktopColumnStart: 2 },
    { id: 'player', visible: true, desktopColumnStart: 1 },
    { id: 'leaderboard', visible: true, desktopColumnStart: 4 },
    { id: 'driver', visible: true, desktopColumnStart: 3 },
  ])
})

test('reconciles a changed registry while retaining known visibility and order', () => {
  expect(reconcileReplayPanelLayout(['leaderboard', 'player', 'driver'] as const, layout)).toEqual([
    { id: 'player', visible: true, desktopColumnStart: 1 },
    { id: 'leaderboard', visible: true, desktopColumnStart: 4 },
    { id: 'driver', visible: true, desktopColumnStart: 3 },
  ])
})

test('adds the two-column telemetry panel to legacy layouts without changing saved choices', () => {
  expect(reconcileReplayPanelLayout(['player', 'track-map', 'leaderboard', 'driver', 'telemetry'] as const, layout)).toEqual([
    { id: 'player', visible: true, desktopColumnStart: 1 },
    { id: 'track-map', visible: false, desktopColumnStart: 2 },
    { id: 'leaderboard', visible: true, desktopColumnStart: 4 },
    { id: 'driver', visible: true, desktopColumnStart: 3 },
    { id: 'telemetry', visible: true, desktopColumnStart: 1 },
  ])
})

test('adds and removes the new panels while retaining registered layout choices', () => {
  const withNewPanels = reconcileReplayPanelLayout(['player', 'lap-analysis', 'strategy'] as const, layout)
  expect(withNewPanels).toEqual([
    { id: 'player', visible: true, desktopColumnStart: 1 },
    { id: 'lap-analysis', visible: true, desktopColumnStart: 1 },
    { id: 'strategy', visible: true, desktopColumnStart: 1 },
  ])
  expect(reconcileReplayPanelLayout(['player'] as const, withNewPanels)).toEqual([
    { id: 'player', visible: true, desktopColumnStart: 1 },
  ])
})

test('clamps every registered panel column start to a valid desktop position', () => {
  const layoutWithMixedPanels = [
    ...layout,
    { id: 'telemetry', visible: true, desktopColumnStart: 1 },
    { id: 'lap-analysis', visible: true, desktopColumnStart: 1 },
    { id: 'strategy', visible: true, desktopColumnStart: 1 },
  ] as const
  const reconciled = reconcileReplayPanelLayout(['lap-analysis', 'strategy'] as const, layoutWithMixedPanels)

  expect(reconciled).toEqual([
    { id: 'lap-analysis', visible: true, desktopColumnStart: 1 },
    { id: 'strategy', visible: true, desktopColumnStart: 1 },
  ])
  expect(commitReplayPanelDrag(layoutWithMixedPanels, { id: 'lap-analysis', index: 4, desktopColumnStart: 4 }).find(({ id }) => id === 'lap-analysis')?.desktopColumnStart).toBe(4)
  expect(commitReplayPanelDrag(layoutWithMixedPanels, { id: 'strategy', index: 5, desktopColumnStart: 0 }).find(({ id }) => id === 'strategy')?.desktopColumnStart).toBe(1)
})
