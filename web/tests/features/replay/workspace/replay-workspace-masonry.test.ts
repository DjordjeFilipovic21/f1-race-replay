import { expect, test } from 'vitest'
import { LOCKED_WORKSPACE_GAP_PX, MASONRY_GAP_PX, masonryRowSpan } from '../../../../src/features/replay/workspace/replay-workspace-masonry'

test('converts measured panel heights into dense-grid spans without clipping', () => {
  // Arrange - use representative and boundary measurements.
  const measuredHeights = [0, 8, 28, 101]

  // Act - convert each measurement into a row span.
  const spans = measuredHeights.map((height) => masonryRowSpan(height))

  // Assert - invalid/short panels retain a legal span and larger panels are not undersized.
  expect(spans).toEqual([1, 1, 2, 6])
})

test('uses the active workspace gap for both unlocked and locked row-span calculations', () => {
  // Arrange - the same measured panel height is evaluated against both mode metrics.
  const measuredHeight = 28

  // Act - calculate spans using the explicit unlocked and locked gaps.
  const unlockedSpan = masonryRowSpan(measuredHeight, MASONRY_GAP_PX)
  const lockedSpan = masonryRowSpan(measuredHeight, LOCKED_WORKSPACE_GAP_PX)

  // Assert - unlocked sizing remains unchanged while zero locked gap removes phantom rows.
  expect(unlockedSpan).toBe(2)
  expect(lockedSpan).toBe(4)
})

test('falls back to a legal span for non-finite measurements and invalid gaps', () => {
  // Arrange - provide measurements and a gap that cannot produce a reliable grid metric.
  const invalidHeight = Number.NaN
  const invalidGap = Number.NaN

  // Act - calculate the defensive fallback span.
  const span = masonryRowSpan(invalidHeight, invalidGap)

  // Assert - malformed measurements do not produce zero, negative, or non-finite spans.
  expect(span).toBe(1)
})
