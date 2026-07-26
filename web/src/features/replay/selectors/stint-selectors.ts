import type { StintDriverColumns, StintSummary } from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'

export interface VisibleStint {
  readonly stintNumber: number
  readonly compound: string | null
  readonly startLap: number
  readonly endLap: number | null
  readonly startTimeMs: number | null
  readonly endTimeMs: number | null
  readonly tyreLifeAtStart: number | null
  readonly isFreshTyre: boolean | null
  readonly pitInTimeMs: number | null
  readonly pitOutTimeMs: number | null
  readonly ageMs: number | null
  readonly isInProgress: boolean
}

export interface StintSelection {
  readonly driverId: string
  readonly sessionTimeMs: number
  readonly stints: readonly VisibleStint[]
}

type CausalBoundary = number | Pick<ReplaySnapshot, 'sessionTimeMs'>

/** Selects the driver's tyre history without exposing future starts or pit events. */
export function selectStintData(
  summary: StintSummary | null | undefined,
  boundary: CausalBoundary,
  driverId: string,
): StintSelection {
  const sessionTimeMs = getSessionTimeMs(boundary)
  const columns = summary?.drivers[driverId]
  if (columns === undefined) return freeze({ driverId, sessionTimeMs, stints: [] })

  const stints = columns.stintNumber.flatMap((_, index) => {
    if (!isVisibleStart(columns.startTimeMs[index], sessionTimeMs)) return []
    return [createVisibleStint(columns, index, sessionTimeMs)]
  })
  return freeze({ driverId, sessionTimeMs, stints: freeze(stints) })
}

/** Alias kept concise for panel view-model code. */
export const selectStints = selectStintData

function createVisibleStint(columns: StintDriverColumns, index: number, sessionTimeMs: number): VisibleStint {
  const startTimeMs = columns.startTimeMs[index]
  const sourceEndTimeMs = columns.endTimeMs[index]
  const endTimeMs = sourceEndTimeMs !== null && sourceEndTimeMs <= sessionTimeMs ? sourceEndTimeMs : null
  const isInProgress = endTimeMs === null
  const ageEndMs = isInProgress ? sessionTimeMs : endTimeMs
  const ageMs = startTimeMs === null || ageEndMs === null ? null : Math.max(0, ageEndMs - startTimeMs)
  return freeze({
    stintNumber: columns.stintNumber[index],
    compound: columns.compound[index],
    startLap: columns.startLap[index],
    endLap: endTimeMs === null ? null : columns.endLap[index],
    startTimeMs,
    endTimeMs,
    tyreLifeAtStart: columns.tyreLifeAtStart[index],
    isFreshTyre: columns.isFreshTyre[index],
    pitInTimeMs: causalTime(columns.pitInTimeMs[index], sessionTimeMs),
    pitOutTimeMs: causalTime(columns.pitOutTimeMs[index], sessionTimeMs),
    ageMs,
    isInProgress,
  })
}

function isVisibleStart(startTimeMs: number | null, sessionTimeMs: number): boolean {
  return startTimeMs === null || startTimeMs <= sessionTimeMs
}

function causalTime(timeMs: number | null, sessionTimeMs: number): number | null {
  return timeMs !== null && timeMs <= sessionTimeMs ? timeMs : null
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
