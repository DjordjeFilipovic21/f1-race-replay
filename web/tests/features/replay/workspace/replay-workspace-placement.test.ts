import { expect, test } from 'vitest'
import { canonicalDesktopColumnStart, clampColumnStart, columnStartFromDropCenter, columnStartWithHysteresis, isSameMasonryLayout, previewMasonryRow, resolveMasonryLayout, resolveVerticalInsertionIndex, responsiveColumnStart, workspaceColumnCount } from '../../../../src/features/replay/workspace/replay-workspace-placement'
import { commitReplayPanelDrag, createDefaultReplayPanelLayout, isDefaultReplayPanelLayout } from '../../../../src/features/replay/workspace/replay-panel-layout'

test('maps a dropped shape center to its legal desktop column', () => {
  expect(columnStartFromDropCenter(50, 0, 400, 4, 1)).toBe(1)
  expect(columnStartFromDropCenter(350, 0, 400, 4, 1)).toBe(4)
  expect(columnStartFromDropCenter(395, 0, 400, 4, 2)).toBe(3)
})

test('clamps column starts and maps stored desktop placement to responsive lanes', () => {
  expect(clampColumnStart(4, 4, 2)).toBe(3)
  expect(responsiveColumnStart(4, 1, 2)).toBe(2)
  expect(responsiveColumnStart(4, 2, 2)).toBe(1)
  expect(responsiveColumnStart(2, 2, 4)).toBe(2)
  expect(responsiveColumnStart(4, 2, 4)).toBe(3)
  expect(responsiveColumnStart(4, 2, 1)).toBe(1)
  expect(responsiveColumnStart(4, 1, 1)).toBe(1)
})

test('restores default status when a panel returns to its default responsive lane', () => {
  const panelIds = ['player'] as const
  const defaultLayout = createDefaultReplayPanelLayout(panelIds)
  const customLayout = commitReplayPanelDrag(defaultLayout, { id: 'player', index: 0, desktopColumnStart: 1 })
  const restoredColumn = canonicalDesktopColumnStart(2, 4, 1, 2)
  const restoredLayout = commitReplayPanelDrag(customLayout, { id: 'player', index: 0, desktopColumnStart: restoredColumn })

  expect(isDefaultReplayPanelLayout(panelIds, customLayout)).toBe(false)
  expect(restoredColumn).toBe(4)
  expect(isDefaultReplayPanelLayout(panelIds, restoredLayout)).toBe(true)
})

test('uses the workspace breakpoints for active placement columns', () => {
  // Arrange - sample the supported narrow, tablet, desktop, and wide viewport boundaries.
  const viewportWidths = [375, 767, 768, 1023, 1024, 1440]

  // Act - resolve the responsive masonry column count at each boundary.
  const columnCounts = viewportWidths.map((width) => workspaceColumnCount(width))

  // Assert - the placement model matches the responsive layout breakpoints without a wide-screen regression.
  expect(columnCounts).toEqual([1, 1, 2, 2, 4, 4])
})

test('holds a column near its boundary while allowing deliberate crossings', () => {
  expect(columnStartWithHysteresis(1, 2, 100, 0, 400, 4, 1)).toBe(1)
  expect(columnStartWithHysteresis(1, 2, 120, 0, 400, 4, 1)).toBe(2)
  expect(columnStartWithHysteresis(2, 1, 95, 0, 400, 4, 1)).toBe(2)
  expect(columnStartWithHysteresis(2, 1, 80, 0, 400, 4, 1)).toBe(1)
})

test('applies boundary hysteresis to two-column desktop panels', () => {
  expect(columnStartWithHysteresis(1, 2, 150, 0, 400, 4, 2)).toBe(1)
  expect(columnStartWithHysteresis(1, 2, 160, 0, 400, 4, 2)).toBe(2)
  expect(columnStartWithHysteresis(2, 1, 145, 0, 400, 4, 2)).toBe(2)
  expect(columnStartWithHysteresis(2, 1, 130, 0, 400, 4, 2)).toBe(1)
})

test('forces a two-column panel to the only legal tablet and mobile start', () => {
  expect(columnStartWithHysteresis(2, 2, 350, 0, 400, 2, 2)).toBe(1)
  expect(columnStartWithHysteresis(2, 2, 350, 0, 400, 1, 2)).toBe(1)
})

test('packs a preview into empty lanes and below occupied Track map or Leaderboard lanes', () => {
  expect(previewMasonryRow([], { id: 'driver', index: 0, columnStart: 3, columns: 1, rowSpan: 2 }, 4)).toBe(1)

  const occupied = [
    { id: 'track-map', columnStart: 2, columns: 2 as const, rowSpan: 5 },
    { id: 'leaderboard', columnStart: 4, columns: 1 as const, rowSpan: 4 },
  ]
  expect(previewMasonryRow(occupied, { id: 'driver', index: 1, columnStart: 2, columns: 1, rowSpan: 2 }, 4)).toBe(6)
  expect(previewMasonryRow(occupied, { id: 'driver', index: 2, columnStart: 4, columns: 1, rowSpan: 2 }, 4)).toBe(5)
})

test('compares visual masonry fingerprints independently of canonical item order', () => {
  const defaultItems = [
    { id: 'left', columnStart: 1, columns: 1 as const, rowSpan: 4 },
    { id: 'right', columnStart: 4, columns: 1 as const, rowSpan: 3 },
  ]
  const visuallyEquivalent = [...defaultItems].reverse()
  const visuallySwapped = [
    { ...defaultItems[0], columnStart: 4 },
    { ...defaultItems[1], columnStart: 1 },
  ]

  const defaultFingerprint = resolveMasonryLayout(defaultItems, 4)
  expect(isSameMasonryLayout(defaultFingerprint, resolveMasonryLayout(visuallyEquivalent, 4))).toBe(true)
  expect(isSameMasonryLayout(defaultFingerprint, resolveMasonryLayout(visuallySwapped, 4))).toBe(false)
})

test('rejects ambiguous masonry fingerprints with duplicate panel IDs', () => {
  const unique = resolveMasonryLayout([
    { id: 'left', columnStart: 1, columns: 1, rowSpan: 2 },
    { id: 'right', columnStart: 2, columns: 1, rowSpan: 2 },
  ], 4)
  const duplicate = resolveMasonryLayout([
    { id: 'left', columnStart: 1, columns: 1, rowSpan: 2 },
    { id: 'left', columnStart: 2, columns: 1, rowSpan: 2 },
  ], 4)

  expect(isSameMasonryLayout(unique, duplicate)).toBe(false)
  expect(isSameMasonryLayout(duplicate, duplicate)).toBe(false)
})

test('clamps a two-column preview to the final legal desktop lane', () => {
  expect(previewMasonryRow([], { id: 'track-map', index: 0, columnStart: 4, columns: 2, rowSpan: 3 }, 4)).toBe(1)
})

const verticalItems = [
  { id: 'player', columnStart: 1, columns: 1 as const, rowSpan: 4 },
  { id: 'track-map', columnStart: 2, columns: 2 as const, rowSpan: 8 },
  { id: 'leaderboard', columnStart: 4, columns: 1 as const, rowSpan: 5 },
  { id: 'driver', columnStart: 1, columns: 1 as const, rowSpan: 3 },
]

const verticalGeometry = new Map([
  ['player', { top: 0, bottom: 80 }],
  ['track-map', { top: 0, bottom: 160 }],
  ['leaderboard', { top: 0, bottom: 100 }],
])

test('resolves an empty-space drop below the target lane tail and predicts its masonry row', () => {
  const index = resolveVerticalInsertionIndex(verticalItems, 'driver', 4, 1, verticalGeometry, 250, 0)

  expect(index).toBe(3)
  expect(previewMasonryRow(verticalItems, { id: 'driver', index, columnStart: 4, columns: 1, rowSpan: 3 }, 4)).toBe(6)
})

test('inserts above a target midpoint and after the Track map without cross-column collisions', () => {
  expect(resolveVerticalInsertionIndex(verticalItems, 'driver', 4, 1, verticalGeometry, 20, 3)).toBe(2)
  expect(resolveVerticalInsertionIndex(verticalItems, 'driver', 2, 1, verticalGeometry, 250, 0)).toBe(2)
})

test('uses the same geometry-derived index for the committed canonical order', () => {
  const index = resolveVerticalInsertionIndex(verticalItems, 'driver', 4, 1, verticalGeometry, 250, 0)
  const layout = verticalItems.map((item) => ({ id: item.id as 'player' | 'track-map' | 'leaderboard' | 'driver', pinned: true, desktopColumnStart: item.columnStart }))

  expect(commitReplayPanelDrag(layout, { id: 'driver', index, desktopColumnStart: 4 }).map(({ id }) => id)).toEqual(['player', 'track-map', 'leaderboard', 'driver'])
})

test('keeps an empty lane compact and safely falls back when geometry is unavailable', () => {
  const sparseItems = verticalItems.filter(({ id }) => id !== 'player')
  const index = resolveVerticalInsertionIndex(sparseItems, 'driver', 1, 1, verticalGeometry, 250, 2)
  expect(index).toBe(2)
  expect(previewMasonryRow(sparseItems, { id: 'driver', index, columnStart: 1, columns: 1, rowSpan: 3 }, 4)).toBe(1)
  expect(resolveVerticalInsertionIndex(verticalItems, 'driver', 4, 1, new Map(), 250, 1)).toBe(1)
})
