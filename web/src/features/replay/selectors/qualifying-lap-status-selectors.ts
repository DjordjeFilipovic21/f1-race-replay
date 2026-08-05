import type {
  QualifyingLapStatus,
  QualifyingLapStatusEvent,
  QualifyingLapStatusSidecar,
} from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'

export interface QualifyingLapStatusAtTime {
  readonly lapNumber: number
  readonly status: QualifyingLapStatus
  readonly deletedReason: string | null
  readonly eventTimeMs: number | null
}

export interface QualifyingLapStatusSelection {
  readonly driverId: string
  readonly sessionTimeMs: number
  readonly laps: readonly QualifyingLapStatusAtTime[]
}

export type QualifyingCandidate = { readonly lapNumber: number | null }
type CausalBoundary = number | Pick<ReplaySnapshot, 'sessionTimeMs'>

/** Returns the causal status for one published lap, or null when evidence is absent. */
export function selectQualifyingLapStatus(
  sidecar: QualifyingLapStatusSidecar | null | undefined,
  boundary: CausalBoundary,
  driverId: string,
  lapNumber: number,
): QualifyingLapStatus | null {
  if (sidecar == null) return null
  const record = sidecar.drivers[driverId]
  if (record === undefined) return null
  const index = record.lapNumber.indexOf(lapNumber)
  if (index < 0) return null
  return statusAtTime(sidecar, driverId, lapNumber, getSessionTimeMs(boundary), record.status[index])
}

/** Selects every known lap state at a causal replay boundary. */
export function selectQualifyingLapStatuses(
  sidecar: QualifyingLapStatusSidecar | null | undefined,
  boundary: CausalBoundary,
  driverId: string,
): QualifyingLapStatusSelection {
  const sessionTimeMs = getSessionTimeMs(boundary)
  if (sidecar == null) return freeze({ driverId, sessionTimeMs, laps: [] })
  const record = sidecar.drivers[driverId]
  if (record === undefined) return freeze({ driverId, sessionTimeMs, laps: [] })
  const laps = record.lapNumber.map((lapNumber, index) => {
    const state = resolveLapState(sidecar, driverId, lapNumber, sessionTimeMs, record.status[index], record.deletedReason[index])
    return freeze({ lapNumber, ...state })
  })
  return freeze({ driverId, sessionTimeMs, laps: freeze(laps) })
}

/** Filters only candidates with explicit causal valid evidence. Unknown and deleted candidates are omitted. */
export function filterQualifyingLapCandidates<T extends QualifyingCandidate>(
  candidates: readonly T[],
  sidecar: QualifyingLapStatusSidecar | null | undefined,
  boundary: CausalBoundary,
  driverId: string,
): readonly T[] {
  if (sidecar == null) return Object.freeze(candidates.slice())
  const sessionTimeMs = getSessionTimeMs(boundary)
  return Object.freeze(candidates.filter((candidate) => candidate.lapNumber !== null
    && selectQualifyingLapStatus(sidecar, sessionTimeMs, driverId, candidate.lapNumber) === 'valid').slice())
}

export const filterQualifyingSectorCandidates = filterQualifyingLapCandidates
export const filterQualifyingBestLapCandidates = filterQualifyingLapCandidates
export const selectQualifyingLapStatusAt = selectQualifyingLapStatus
export const selectQualifyingLapStatusAtTime = selectQualifyingLapStatus
export const selectQualifyingLapStates = selectQualifyingLapStatuses
export const filterLapCandidatesByQualifyingStatus = filterQualifyingLapCandidates
export const filterSectorCandidatesByQualifyingStatus = filterQualifyingSectorCandidates
export const filterBestLapCandidatesByQualifyingStatus = filterQualifyingBestLapCandidates

function resolveLapState(
  sidecar: QualifyingLapStatusSidecar,
  driverId: string,
  lapNumber: number,
  sessionTimeMs: number,
  finalStatus: QualifyingLapStatus,
  finalReason: string | null,
): Omit<QualifyingLapStatusAtTime, 'lapNumber'> {
  let status: QualifyingLapStatus = 'valid'
  let deletedReason: string | null = null
  let eventTimeMs: number | null = null
  for (const event of orderedEvents(sidecar.events)) {
    if (event.driverId !== driverId || event.lapNumber !== lapNumber || event.eventTimeMs > sessionTimeMs) continue
    status = event.status === 'deleted' ? 'deleted' : 'valid'
    deletedReason = event.status === 'deleted' ? event.reason : null
    eventTimeMs = event.eventTimeMs
  }
  if (status === finalStatus) deletedReason = finalReason
  return { status, deletedReason, eventTimeMs }
}

function statusAtTime(
  sidecar: QualifyingLapStatusSidecar,
  driverId: string,
  lapNumber: number,
  sessionTimeMs: number,
  finalStatus: QualifyingLapStatus,
): QualifyingLapStatus {
  return resolveLapState(sidecar, driverId, lapNumber, sessionTimeMs, finalStatus, null).status
}

function orderedEvents(events: readonly QualifyingLapStatusEvent[]): readonly QualifyingLapStatusEvent[] {
  return [...events].sort((left, right) => left.eventTimeMs - right.eventTimeMs
    || left.driverId.localeCompare(right.driverId)
    || left.lapNumber - right.lapNumber
    || left.status.localeCompare(right.status)
    || (left.reason ?? '').localeCompare(right.reason ?? '')
    || left.rawMessage.localeCompare(right.rawMessage))
}

function getSessionTimeMs(boundary: CausalBoundary): number {
  return typeof boundary === 'number' ? boundary : boundary.sessionTimeMs
}

function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value as Record<string, unknown>).forEach(freeze)
    Object.freeze(value)
  }
  return value
}
