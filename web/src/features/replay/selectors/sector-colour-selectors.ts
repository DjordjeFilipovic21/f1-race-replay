import type { LapSectorDriverColumns, LapSectorSidecar } from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'
import { selectLapSectorData, type SectorNumber } from './lap-sector-selectors'

export type SectorColour = 'session-best' | 'personal-best' | 'slower' | 'unavailable'

export interface ColouredSector {
  readonly lapNumber: number
  readonly sectorNumber: SectorNumber
  readonly durationMs: number | null
  readonly sessionTimeMs: number | null
  readonly sessionBestMs: number | null
  readonly personalBestMs: number | null
  readonly colour: SectorColour
  readonly isSessionBest: boolean
  readonly isPersonalBest: boolean
}

export interface SectorColourSelection {
  readonly driverId: string
  readonly sessionTimeMs: number
  readonly sectors: readonly ColouredSector[]
}

type CausalBoundary = number | Pick<ReplaySnapshot, 'sessionTimeMs'>

/** Computes sector colours from only sector completions at or before the cursor. */
export function selectSectorColours(
  sidecar: LapSectorSidecar | null | undefined,
  boundary: CausalBoundary,
  driverId: string,
): SectorColourSelection {
  const sessionTimeMs = getSessionTimeMs(boundary)
  if (sidecar === null || sidecar === undefined) return freeze({ driverId, sessionTimeMs, sectors: [] })
  const columns = sidecar.drivers[driverId]
  if (columns === undefined) return freeze({ driverId, sessionTimeMs, sectors: [] })

  const visible = selectLapSectorData(sidecar, sessionTimeMs, driverId)
  const sessionBests = findSessionBests(sidecar, sessionTimeMs)
  const personalBests = findPersonalBests(columns, sessionTimeMs)
  const completedLapNumbers = new Set(visible.laps.map((lap) => lap.lapNumber))
  const lapNumbers = [...new Set([
    ...visible.laps.map((lap) => lap.lapNumber),
    ...visible.sectors.map((sector) => sector.lapNumber),
  ])]
  const sectors = lapNumbers.flatMap((lapNumber) => {
    const availableSectorNumbers = completedLapNumbers.has(lapNumber)
      ? [1, 2, 3] as const
      : visible.sectors.filter((sector) => sector.lapNumber === lapNumber).map((sector) => sector.sectorNumber)
    return availableSectorNumbers.map((sectorNumber) => {
      const source = visible.sectors.find((sector) => sector.lapNumber === lapNumber && sector.sectorNumber === sectorNumber)
      const durationMs = source?.durationMs ?? null
      const sessionBestMs = sessionBests[sectorNumber as SectorNumber] ?? null
      const personalBestMs = personalBests[sectorNumber as SectorNumber] ?? null
      const isSessionBest = durationMs !== null && sessionBestMs === durationMs
      const isPersonalBest = durationMs !== null && personalBestMs === durationMs
      return freeze({
        lapNumber,
        sectorNumber: sectorNumber as SectorNumber,
        durationMs,
        sessionTimeMs: source?.sessionTimeMs ?? null,
        sessionBestMs,
        personalBestMs,
        colour: getSectorColour(durationMs, isSessionBest, isPersonalBest),
        isSessionBest,
        isPersonalBest,
      })
    })
  })
  return freeze({ driverId, sessionTimeMs, sectors: freeze(sectors) })
}

/** Alias kept concise for panel view-model code. */
export const selectSectorColour = selectSectorColours
export const selectSectorColors = selectSectorColours

function findSessionBests(sidecar: LapSectorSidecar, sessionTimeMs: number): Partial<Record<SectorNumber, number>> {
  const bests: Partial<Record<SectorNumber, number>> = {}
  Object.values(sidecar.drivers).forEach((columns) => mergeBests(bests, columns, sessionTimeMs))
  return bests
}

function findPersonalBests(columns: LapSectorDriverColumns, sessionTimeMs: number): Partial<Record<SectorNumber, number>> {
  const bests: Partial<Record<SectorNumber, number>> = {}
  mergeBests(bests, columns, sessionTimeMs)
  return bests
}

function mergeBests(bests: Partial<Record<SectorNumber, number>>, columns: LapSectorDriverColumns, sessionTimeMs: number): void {
  const fields: readonly [SectorNumber, keyof LapSectorDriverColumns, keyof LapSectorDriverColumns][] = [
    [1, 'sector1DurationMs', 'sector1SessionTimeMs'],
    [2, 'sector2DurationMs', 'sector2SessionTimeMs'],
    [3, 'sector3DurationMs', 'sector3SessionTimeMs'],
  ]
  fields.forEach(([sectorNumber, durationField, timeField]) => {
    columns[timeField].forEach((completionTime, index) => {
      const duration = columns[durationField][index]
      if (completionTime === null || completionTime > sessionTimeMs || duration === null) return
      const current = bests[sectorNumber]
      if (current === undefined || duration < current) bests[sectorNumber] = duration
    })
  })
}

function getSectorColour(durationMs: number | null, isSessionBest: boolean, isPersonalBest: boolean): SectorColour {
  if (durationMs === null) return 'unavailable'
  if (isSessionBest) return 'session-best'
  if (isPersonalBest) return 'personal-best'
  return 'slower'
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
