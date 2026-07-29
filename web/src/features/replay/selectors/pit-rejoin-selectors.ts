import type { ReplaySnapshot } from '../../../engine/replay/types'
import type { PitLossSelection } from './pit-loss-selectors'

export interface PitRejoinComparator {
  readonly driverId: string
  readonly gapMs: number
  readonly signedGapMs: number
}

export interface PitRejoinProjection {
  readonly selectedDriverId: string
  readonly projectedGapToLeaderMs: number
  readonly projectedPosition: number
  readonly currentPosition: number | null
  readonly aheadComparator: PitRejoinComparator | null
  readonly behindComparator: PitRejoinComparator | null
}

/**
 * Projects the selected driver's gap to leader after an immediate pit stop
 * and identifies the closest competitor ahead and behind on track.
 *
 * The selected driver's gap is projected as `currentGapToLeaderMs + estimatedLossMs`.
 * All other active drivers' gaps are held fixed at their current values.
 *
 * Signed semantics: `signedGapMs = projectedSelectedGap - comparatorGap`
 *   positive → selected driver would rejoin BEHIND that driver (comparator is ahead on track)
 *   negative → selected driver would rejoin AHEAD of that driver (comparator is behind on track)
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
  const currentPosition = readCurrentPosition(selectedSnapshot)
  const { ahead, behind } = findSideComparators(comparators, projectedSelectedGap, selectedDriverId)

  return Object.freeze({
    selectedDriverId,
    projectedGapToLeaderMs: projectedSelectedGap,
    projectedPosition,
    currentPosition,
    aheadComparator: ahead,
    behindComparator: behind,
  })
}

const NULL_PROJECTION = null as PitRejoinProjection | null

/** Reads the selected driver's current valid race position, or null when unavailable. */
function readCurrentPosition(driverSnapshot: ReplaySnapshot['drivers'][string]): number | null {
  const position = driverSnapshot.position
  if (position === null || position === undefined) return null
  if (!Number.isFinite(position)) return null
  if (!Number.isInteger(position) || position < 1) return null
  return position
}

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

function findSideComparators(
  comparators: readonly { readonly id: string; readonly gap: number }[],
  projectedSelectedGap: number,
  selectedDriverId: string,
): { ahead: PitRejoinComparator | null; behind: PitRejoinComparator | null } {
  let ahead: PitRejoinComparator | null = null
  let behind: PitRejoinComparator | null = null

  for (const comparator of comparators) {
    const signedGap = projectedSelectedGap - comparator.gap

    if (signedGap > 0) {
      // Comparator is ahead of selected (selected is behind comparator)
      // We want the closest ahead comparator = smallest positive signedGap
      if (ahead === null || signedGap < ahead.signedGapMs || (signedGap === ahead.signedGapMs && comparator.id < ahead.driverId)) {
        ahead = Object.freeze({
          driverId: comparator.id,
          gapMs: comparator.gap,
          signedGapMs: signedGap,
        })
      }
    } else if (signedGap < 0) {
      // Comparator is behind selected (selected is ahead of comparator)
      // We want the closest behind comparator = largest negative signedGap (closest to zero)
      if (behind === null || signedGap > behind.signedGapMs || (signedGap === behind.signedGapMs && comparator.id < behind.driverId)) {
        behind = Object.freeze({
          driverId: comparator.id,
          gapMs: comparator.gap,
          signedGapMs: signedGap,
        })
      }
    } else {
      // Equal gap (signedGap === 0): assign to one side based on driver ID ordering
      // Same logic as computeProjectedPosition: if comparator.id < selectedDriverId, comparator is ahead
      if (comparator.id < selectedDriverId) {
        // Assign to ahead side
        if (ahead === null || signedGap < ahead.signedGapMs || (signedGap === ahead.signedGapMs && comparator.id < ahead.driverId)) {
          ahead = Object.freeze({
            driverId: comparator.id,
            gapMs: comparator.gap,
            signedGapMs: signedGap,
          })
        }
      } else {
        // Assign to behind side
        if (behind === null || signedGap > behind.signedGapMs || (signedGap === behind.signedGapMs && comparator.id < behind.driverId)) {
          behind = Object.freeze({
            driverId: comparator.id,
            gapMs: comparator.gap,
            signedGapMs: signedGap,
          })
        }
      }
    }
  }

  return { ahead, behind }
}
