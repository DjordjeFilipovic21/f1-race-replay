import { expect, test } from 'vitest'
import { masonryRowSpan } from '../src/replay-ui/replay-workspace-masonry'

test('converts measured panel heights into dense-grid spans without clipping', () => {
  expect(masonryRowSpan(0)).toBe(1)
  expect(masonryRowSpan(8)).toBe(1)
  expect(masonryRowSpan(28)).toBe(2)
  expect(masonryRowSpan(101)).toBe(6)
})
