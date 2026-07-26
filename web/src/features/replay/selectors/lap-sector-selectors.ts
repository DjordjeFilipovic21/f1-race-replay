import type { LapSectorDriverColumns, LapSectorSidecar } from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'

export type SectorNumber = 1 | 2 | 3

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

/** Selects only lap and sector completions that were known at the replay cursor. */
export function selectLapSectorData(
  sidecar: LapSectorSidecar | null | undefined,
  boundary: CausalBoundary,
  driverId: string,
): LapSectorSelection {
  const sessionTimeMs = getSessionTimeMs(boundary)
  const columns = sidecar?.drivers[driverId]
  if (columns === undefined) return freeze({ driverId, sessionTimeMs, laps: [], sectors: [] })

  const sectors = selectVisibleSectors(columns, sessionTimeMs)
  const laps = columns.lapNumber.flatMap((lapNumber, index) => {
    if (!isVisibleLap(columns, index, sessionTimeMs)) return []
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
  const fields: readonly [SectorNumber, keyof LapSectorDriverColumns, keyof LapSectorDriverColumns][] = [
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

function isVisibleLap(columns: LapSectorDriverColumns, index: number, sessionTimeMs: number): boolean {
  const sector3Time = columns.sector3SessionTimeMs[index]
  return columns.lapEndMs[index] <= sessionTimeMs && (sector3Time === null || sector3Time <= sessionTimeMs)
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
