import type { LapSectorDriverColumns, LapSectorSidecar, QualifyingLapStatusSidecar } from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'
import { selectQualifyingLapStatus } from './qualifying-lap-status-selectors'

export type SectorNumber = 1 | 2 | 3
export type SectorDurationField = 'sector1DurationMs' | 'sector2DurationMs' | 'sector3DurationMs'
export type SectorSessionTimeField = 'sector1SessionTimeMs' | 'sector2SessionTimeMs' | 'sector3SessionTimeMs'
export type SectorColumnFields = readonly [SectorNumber, SectorDurationField, SectorSessionTimeField]

export interface VisibleSector {
  readonly lapNumber: number
  readonly sectorNumber: SectorNumber
  readonly durationMs: number | null
  readonly sessionTimeMs: number
}

export interface VisibleLap {
  readonly lapNumber: number
  readonly lapStartMs: number
  readonly lapEndMs: number
  readonly lapDurationMs: number | null
  readonly sectors: readonly VisibleSector[]
}

export interface LapSectorSelection {
  readonly driverId: string
  readonly sessionTimeMs: number
  readonly laps: readonly VisibleLap[]
  readonly sectors: readonly VisibleSector[]
}

type CausalBoundary = number | Pick<ReplaySnapshot, 'sessionTimeMs'>

/**
 * True when the sidecar carries qualifying phase structure. The optional
 * `lapKind` column is emitted only for qualifying-like sessions; for older
 * sidecars without it, non-empty phase boundaries or any phase-qualified lap
 * is the truthful signal that a flying classification capability was expected.
 * Race/sprint/practice sidecars (empty boundaries, null phases) are not
 * qualifying-like and keep the legacy all-laps behavior.
 */
export function isQualifyingLikeSidecar(sidecar: LapSectorSidecar | null | undefined): boolean {
  if (sidecar?.contractVersion !== 'v2') return false
  if (sidecar.phaseBoundaries.length > 0) return true
  return Object.values(sidecar.drivers).some((columns) =>
    (columns.qualifyingPhase ?? []).some((phase) => phase !== null),
  )
}

/**
 * True when the lap contributes flying timing evidence. With an explicit
 * `lapKind` column only `'flying'` qualifies; outlap, inlap, and unknown
 * contribute nothing. When the column is absent the legacy behavior is
 * preserved for non-qualifying sidecars (race/sprint/practice), while
 * qualifying-like sidecars fail closed — no lap may be treated as flying.
 */
export function isFlyingEvidence(columns: LapSectorDriverColumns, index: number, qualifyingLike: boolean): boolean {
  const kind = columns.lapKind?.[index]
  if (kind !== undefined) return kind === 'flying'
  return !qualifyingLike
}

/** Selects only lap and sector completions that were known at the replay cursor. */
export function selectLapSectorData(
  sidecar: LapSectorSidecar | null | undefined,
  boundary: CausalBoundary,
  driverId: string,
  qualifyingLapStatus?: QualifyingLapStatusSidecar | null,
): LapSectorSelection {
  const sessionTimeMs = getSessionTimeMs(boundary)
  const columns = sidecar?.drivers[driverId]
  if (columns === undefined) return freeze({ driverId, sessionTimeMs, laps: [], sectors: [] })

  const qualifyingLike = isQualifyingLikeSidecar(sidecar)
  const sectors = selectVisibleSectors(columns, sessionTimeMs)
    .filter((sector) => isQualifyingLapVisible(qualifyingLapStatus, sessionTimeMs, driverId, sector.lapNumber))
    .filter((sector) => isFlyingSector(columns, sector.lapNumber, qualifyingLike))
  const laps = columns.lapNumber.flatMap((lapNumber, index) => {
    if (!isVisibleLap(columns, index, sessionTimeMs, qualifyingLapStatus, driverId, qualifyingLike)) return []
    const lapSectors = sectors.filter((sector) => sector.lapNumber === lapNumber)
    return [freeze({
      lapNumber,
      lapStartMs: columns.lapStartMs[index],
      lapEndMs: columns.lapEndMs[index],
      lapDurationMs: columns.lapDurationMs[index],
      sectors: freeze(lapSectors),
    })]
  })

  return freeze({ driverId, sessionTimeMs, laps: freeze(laps), sectors: freeze(sectors) })
}

/** Alias kept concise for panel view-model code. */
export const selectLapSector = selectLapSectorData

function selectVisibleSectors(columns: LapSectorDriverColumns, sessionTimeMs: number): readonly VisibleSector[] {
  const fields: readonly SectorColumnFields[] = [
    [1, 'sector1DurationMs', 'sector1SessionTimeMs'],
    [2, 'sector2DurationMs', 'sector2SessionTimeMs'],
    [3, 'sector3DurationMs', 'sector3SessionTimeMs'],
  ]
  return columns.lapNumber.flatMap((lapNumber, index) => fields.flatMap(([sectorNumber, durationField, timeField]) => {
    const completionTime = columns[timeField][index]
    if (completionTime === null || completionTime > sessionTimeMs) return []
    return [freeze({
      lapNumber,
      sectorNumber,
      durationMs: columns[durationField][index],
      sessionTimeMs: completionTime,
    }) as VisibleSector]
  }))
}

function isVisibleLap(
  columns: LapSectorDriverColumns,
  index: number,
  sessionTimeMs: number,
  qualifyingLapStatus: QualifyingLapStatusSidecar | null | undefined,
  driverId: string,
  qualifyingLike: boolean,
): boolean {
  const sector3Time = columns.sector3SessionTimeMs[index]
  return isFlyingEvidence(columns, index, qualifyingLike)
    && columns.lapEndMs[index] <= sessionTimeMs
    && (sector3Time === null || sector3Time <= sessionTimeMs)
    && isQualifyingLapVisible(qualifyingLapStatus, sessionTimeMs, driverId, columns.lapNumber[index])
}

function isFlyingSector(columns: LapSectorDriverColumns, lapNumber: number, qualifyingLike: boolean): boolean {
  const index = columns.lapNumber.indexOf(lapNumber)
  return index >= 0 && isFlyingEvidence(columns, index, qualifyingLike)
}

function isQualifyingLapVisible(
  qualifyingLapStatus: QualifyingLapStatusSidecar | null | undefined,
  sessionTimeMs: number,
  driverId: string,
  lapNumber: number,
): boolean {
  if (qualifyingLapStatus == null) return true
  return selectQualifyingLapStatus(qualifyingLapStatus, sessionTimeMs, driverId, lapNumber) === 'valid'
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
