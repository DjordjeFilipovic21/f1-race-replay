import type { PenaltySidecar } from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'

/** Returns whether a driver's penalty issuance is known at the replay cursor. */
export function selectDriverPenaltyStatus(
  snapshot: ReplaySnapshot,
  penaltySidecar: PenaltySidecar | undefined,
  driverId: string,
): boolean {
  if (penaltySidecar === undefined) return false

  return penaltySidecar.penaltyIssuances.some((penalty) => (
    penalty.driverId === driverId && penalty.sessionTimeMs <= snapshot.sessionTimeMs
  ))
}
