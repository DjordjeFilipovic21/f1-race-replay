import type { WeatherSidecar } from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'

export const STALE_WEATHER_MS = 90_000

export type WeatherSelectionStatus = 'fresh' | 'stale' | 'unavailable'

export type WeatherSelectionReason =
  | 'fresh'
  | 'stale'
  | 'no-sidecar'
  | 'before-first-observation'
  | 'no-usable-measurements'

export interface WeatherSelection {
  readonly status: WeatherSelectionStatus
  readonly reason: WeatherSelectionReason
  readonly observationTimeMs: number | null
  readonly ageMs: number | null
  readonly airTempC: number | null
  readonly humidityPct: number | null
  readonly pressureMbar: number | null
  readonly rainfall: boolean | null
  readonly trackTempC: number | null
  readonly windDirectionDeg: number | null
  readonly windSpeedMps: number | null
}

type CausalBoundary = number | Pick<ReplaySnapshot, 'sessionTimeMs'>

/** Selects one native weather row without consulting observations after the cursor. */
export function causalWeatherSelector(
  sidecar: WeatherSidecar | null | undefined,
  boundary: CausalBoundary,
): WeatherSelection {
  const sessionTimeMs = getSessionTimeMs(boundary)
  if (sidecar === null || sidecar === undefined) {
    return unavailable('no-sidecar')
  }

  const selectedIndex = latestAtOrBefore(sidecar.timeMs, sessionTimeMs)
  if (selectedIndex < 0) return unavailable('before-first-observation')

  const observationTimeMs = sidecar.timeMs[selectedIndex]
  const ageMs = sessionTimeMs - observationTimeMs
  const values = readObservation(sidecar, selectedIndex)
  if (!hasUsableMeasurement(values)) {
    return freeze({
      status: 'unavailable',
      reason: 'no-usable-measurements',
      observationTimeMs,
      ageMs,
      ...values,
    })
  }

  const status: WeatherSelectionStatus = ageMs > STALE_WEATHER_MS ? 'stale' : 'fresh'
  return freeze({
    status,
    reason: status,
    observationTimeMs,
    ageMs,
    ...values,
  })
}

/** Alias kept concise for panel view-model code. */
export const selectWeather = causalWeatherSelector

function readObservation(sidecar: WeatherSidecar, index: number): Omit<WeatherSelection, 'status' | 'reason' | 'observationTimeMs' | 'ageMs'> {
  return {
    airTempC: sidecar.airTempC[index],
    humidityPct: sidecar.humidityPct[index],
    pressureMbar: sidecar.pressureMbar[index],
    rainfall: sidecar.rainfall[index],
    trackTempC: sidecar.trackTempC[index],
    windDirectionDeg: sidecar.windDirectionDeg[index],
    windSpeedMps: sidecar.windSpeedMps[index],
  }
}

function hasUsableMeasurement(values: Omit<WeatherSelection, 'status' | 'reason' | 'observationTimeMs' | 'ageMs'>): boolean {
  return Object.values(values).some((value) => value !== null && value !== undefined)
}

function unavailable(reason: Exclude<WeatherSelectionReason, 'fresh' | 'stale'>): WeatherSelection {
  return freeze({
    status: 'unavailable',
    reason,
    observationTimeMs: null,
    ageMs: null,
    airTempC: null,
    humidityPct: null,
    pressureMbar: null,
    rainfall: null,
    trackTempC: null,
    windDirectionDeg: null,
    windSpeedMps: null,
  })
}

function latestAtOrBefore(times: readonly number[], targetMs: number): number {
  let low = 0
  let high = times.length
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    if (times[middle] <= targetMs) low = middle + 1
    else high = middle
  }
  return low - 1
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
