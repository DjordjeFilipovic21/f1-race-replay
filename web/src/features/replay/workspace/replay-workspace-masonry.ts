export const MASONRY_ROW_HEIGHT_PX = 8
export const MASONRY_GAP_PX = 12

/** Converts a measured panel height to dense-grid rows without undersizing it. */
export function masonryRowSpan(height: number): number {
  if (!Number.isFinite(height) || height <= 0) return 1
  return Math.max(1, Math.ceil((height + MASONRY_GAP_PX) / (MASONRY_ROW_HEIGHT_PX + MASONRY_GAP_PX)))
}
