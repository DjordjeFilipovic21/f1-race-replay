import type { ReplaySnapshot } from '../../../engine/replay/types'
import type { PitLossSelection } from './pit-loss-selectors'

export interface PitRejoinProjection {
  readonly selectedDriverId: string
  readonly projectedGapToLeaderMs: number
  readonly projectedPosition: number
  readonly nearestDriverId: string
  readonly nearestDriverGapMs: number
  readonly signedGapVsNearestMs: number
}

/**
 * Projects the selected driver's gap to leader after an immediate pit stop
 * and identifies the nearest competitor they would rejoin alongside.
 *
 * The selected driver's gap is projected as `currentGapToLeaderMs + estimatedLossMs`.
 * All other active drivers' gaps are held fixed at their current values.
 *
 * Signed semantics: `signedGapVsNearestMs = projectedSelectedGap - comparatorGap`
 *   positive → selected driver would rejoin BEHIND that driver
 *   negative → selected driver would rejoin AHEAD
 *
 * Returns null (frozen) when any required input is unavailable, the selected
 * driver is terminal/finished/in-pit-lane, the pit-loss estimate is invalid,
 * or there are no comparable active drivers with finite gaps.
 */
export function selectPitRejoinProjection(
  snapshot: ReplaySnapshot | null,
  selectedDriverId: string | null,
  pitLoss: PitLossSelection | null,
): PitRejoinProjection | null {
  if (snapshot === null || selectedDriverId === null || pitLoss === null) return NULL_PROJECTION
  if (!Number.isFinite(pitLoss.estimatedLossMs)) return NULL_PROJECTION

  const selectedSnapshot = snapshot.drivers[selectedDriverId]
  if (selectedSnapshot === undefined) return NULL_PROJECTION
  if (isExcluded(selectedSnapshot)) return NULL_PROJECTION

  const currentGap = selectedSnapshot.gapToLeaderMs
  if (currentGap === null || !Number.isFinite(currentGap)) return NULL_PROJECTION

  const projectedSelectedGap = currentGap + pitLoss.estimatedLossMs

  const comparators = collectComparators(snapshot, selectedDriverId)
  if (comparators.length === 0) return NULL_PROJECTION

  const projectedPosition = computeProjectedPosition(comparators, projectedSelectedGap, selectedDriverId)
  const nearest = findNearestComparator(comparators, projectedSelectedGap)
  if (nearest === null) return NULL_PROJECTION

  return Object.freeze({
    selectedDriverId,
    projectedGapToLeaderMs: projectedSelectedGap,
    projectedPosition,
    nearestDriverId: nearest.id,
    nearestDriverGapMs: nearest.gap,
    signedGapVsNearestMs: projectedSelectedGap - nearest.gap,
  })
}

const NULL_PROJECTION = null as PitRejoinProjection | null

function isExcluded(driverSnapshot: ReplaySnapshot['drivers'][string]): boolean {
  if (driverSnapshot.isFinished === true) return true
  if (driverSnapshot.isInPitLane === true) return true
  const normalized = driverSnapshot.status?.replace(/[\s_-]/g, '').toUpperCase() ?? ''
  return normalized === 'OUT'
}

function collectComparators(
  snapshot: ReplaySnapshot,
  selectedDriverId: string,
): readonly { readonly id: string; readonly gap: number }[] {
  const result: { id: string; gap: number }[] = []
  for (const [id, driverSnap] of Object.entries(snapshot.drivers)) {
    if (id === selectedDriverId) continue
    if (isExcluded(driverSnap)) continue
    const gap = driverSnap.gapToLeaderMs
    if (gap === null || !Number.isFinite(gap)) continue
    result.push({ id, gap })
  }
  return result
}

function computeProjectedPosition(
  comparators: readonly { readonly id: string; readonly gap: number }[],
  projectedSelectedGap: number,
  selectedDriverId: string,
): number {
  let position = 1
  for (const comparator of comparators) {
    if (comparator.gap < projectedSelectedGap) position += 1
    else if (comparator.gap === projectedSelectedGap && comparator.id < selectedDriverId) position += 1
  }
  return position
}

function findNearestComparator(
  comparators: readonly { readonly id: string; readonly gap: number }[],
  projectedSelectedGap: number,
): { readonly id: string; readonly gap: number } | null {
  let nearest: { id: string; gap: number; absDiff: number } | null = null
  for (const comparator of comparators) {
    const absDiff = Math.abs(projectedSelectedGap - comparator.gap)
    if (nearest === null || absDiff < nearest.absDiff || (absDiff === nearest.absDiff && comparator.id < nearest.id)) {
      nearest = { id: comparator.id, gap: comparator.gap, absDiff }
    }
  }
  return nearest === null ? null : { id: nearest.id, gap: nearest.gap }
}
