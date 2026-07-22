import { expect, test } from 'vitest'
import {
  commitReplayPanelDrag,
  defaultReplayPanelColumn,
  isReplayPanelId,
  reconcileReplayPanelLayout,
  toggleReplayPanelVisibility,
  type ReplayPanelLayoutItem,
} from '../src/replay-ui/replay-panel-layout'

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

test('validates the Driver panel sortable ID with the registered panel IDs', () => {
  expect(isReplayPanelId('driver')).toBe(true)
  expect(isReplayPanelId('telemetry')).toBe(false)
})

test('updates the dragged panel column while retaining canonical sortable order', () => {
  const updated = commitReplayPanelDrag(layout, { id: 'driver', index: 1, desktopColumnStart: 4 })

  expect(updated.map(({ id }) => id)).toEqual(['player', 'driver', 'track-map', 'leaderboard'])
  expect(updated.find(({ id }) => id === 'driver')?.desktopColumnStart).toBe(4)
})

test('uses semantic default desktop columns for the registered panels', () => {
  expect([defaultReplayPanelColumn('player'), defaultReplayPanelColumn('track-map'), defaultReplayPanelColumn('leaderboard'), defaultReplayPanelColumn('driver')]).toEqual([1, 2, 4, 1])
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
