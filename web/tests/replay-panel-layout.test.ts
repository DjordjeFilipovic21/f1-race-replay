import { expect, test } from 'vitest'
import {
  commitReplayPanelDrag,
  reconcileReplayPanelLayout,
  toggleReplayPanelVisibility,
  type ReplayPanelLayoutItem,
} from '../src/replay-ui/replay-panel-layout'

const layout: readonly ReplayPanelLayoutItem[] = [
  { id: 'player', visible: true },
  { id: 'track-map', visible: false },
  { id: 'leaderboard', visible: true },
]

test('keeps array position as the canonical workspace reorder order', () => {
  expect(commitReplayPanelDrag(layout, { id: 'leaderboard', index: 0 }).map(({ id }) => id)).toEqual([
    'leaderboard', 'player', 'track-map',
  ])
})

test('keeps a collapsed panel in the canonical order when it is shown', () => {
  const reordered = commitReplayPanelDrag(layout, { id: 'track-map', index: 0 })
  expect(toggleReplayPanelVisibility(reordered, 'track-map')).toEqual([
    { id: 'track-map', visible: true },
    { id: 'player', visible: true },
    { id: 'leaderboard', visible: true },
  ])
})

test('reconciles a changed registry while retaining known visibility and order', () => {
  expect(reconcileReplayPanelLayout(['leaderboard', 'player'] as const, layout)).toEqual([
    { id: 'player', visible: true },
    { id: 'leaderboard', visible: true },
  ])
})
