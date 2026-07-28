export const MASONRY_ROW_HEIGHT_PX = 8
export const REPLAY_WORKSPACE_GAP_PX = 12
export const MASONRY_GAP_PX = REPLAY_WORKSPACE_GAP_PX
export const LOCKED_WORKSPACE_GAP_PX = 0

/** Converts a measured panel height to dense-grid rows without undersizing it. */
export function masonryRowSpan(height: number, gap = MASONRY_GAP_PX): number {
  if (!Number.isFinite(height) || height <= 0) return 1
  const safeGap = Number.isFinite(gap) && gap >= 0 ? gap : MASONRY_GAP_PX
  return Math.max(1, Math.ceil((height + safeGap) / (MASONRY_ROW_HEIGHT_PX + safeGap)))
}
