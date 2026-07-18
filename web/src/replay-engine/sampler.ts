import type { DriverColumns, ReplayChunk, ReplayData, ReplayEvent } from '../replay-data/types'
import { createAuthoritativeTimeline } from './timeline'
import type { AuthoritativeTimeline, DriverSnapshot, ReplaySnapshot } from './types'

const CONTINUOUS_FIELDS = ['x', 'y', 'trackDistanceMeters', 'speed', 'throttle', 'brake', 'gapToLeaderMs'] as const
const STEP_FIELDS = ['lap', 'position', 'gear', 'drs', 'tyreCompound', 'status', 'isInPitLane'] as const
const MAX_INTERPOLATION_INTERVAL_MS = 1_000
const MAX_POSITION_INTERPOLATION_INTERVAL_MS = 1_500
const SMOOTH_FILTER_WINDOW_MS = 1_250
const MAX_SMOOTH_FILTER_DISPLACEMENT_METERS = 12

type ContinuousField = (typeof CONTINUOUS_FIELDS)[number]
type StepField = (typeof STEP_FIELDS)[number]
type GlobalField = 'leaderboardOrder' | 'trackStatusCode' | 'weatherState'
export type CoordinateInterpolationStrategy = 'linear' | 'smooth'

interface PreparedValues<T> { readonly times: readonly number[]; readonly values: readonly T[] }
interface PreparedDriver {
  readonly continuous: Readonly<Record<ContinuousField, PreparedValues<number>>>
  readonly filteredCoordinates: Readonly<{ readonly x: PreparedValues<number>; readonly y: PreparedValues<number> }>
  readonly step: Readonly<Record<StepField, PreparedValues<number | string | boolean>>>
}

export interface PreparedReplaySampler {
  readonly drivers: Readonly<Record<string, PreparedDriver>>
  readonly circuitLengthMeters: number
  readonly leaderboardOrder: PreparedValues<readonly string[]>
  readonly trackStatusCode: PreparedValues<number>
  readonly weatherState: PreparedValues<string>
  readonly events: readonly ReplayEvent[]
  readonly coordinateInterpolation: CoordinateInterpolationStrategy
}

/** Prepares immutable, valid-value indexes once for repeated samples of one chunk window. */
export function prepareReplaySampler(replay: ReplayData, timeline = createAuthoritativeTimeline(replay.chunks), coordinateInterpolation: CoordinateInterpolationStrategy = 'linear'): PreparedReplaySampler {
  if (coordinateInterpolation !== 'linear' && coordinateInterpolation !== 'smooth') throw new RangeError('Unsupported coordinate interpolation strategy')
  const driverValues = Object.fromEntries(replay.manifest.drivers.map(({ id }) => [id, prepareDriver(replay, timeline, id)]))
  return Object.freeze({
    drivers: Object.freeze(driverValues),
    circuitLengthMeters: replay.trackAssets.circuitLengthMeters,
    leaderboardOrder: prepareGlobal(replay, timeline, 'leaderboardOrder'),
    trackStatusCode: prepareGlobal(replay, timeline, 'trackStatusCode'),
    weatherState: prepareGlobal(replay, timeline, 'weatherState'),
    events: Object.freeze(replay.chunks.flatMap((chunk) => chunk.events.filter((event) => event.sessionTimeMs >= chunk.startMs && event.sessionTimeMs < chunk.endMs)).sort(byEventTime)),
    coordinateInterpolation,
  })
}

/** Standalone compatibility entry point; callers sampling repeatedly should prepare once. */
export function sampleReplayAt(replay: ReplayData, sessionTimeMs: number, timeline = createAuthoritativeTimeline(replay.chunks)): ReplaySnapshot {
  return samplePreparedReplayAt(prepareReplaySampler(replay, timeline), sessionTimeMs)
}

export function samplePreparedReplayAt(prepared: PreparedReplaySampler, sessionTimeMs: number): ReplaySnapshot {
  if (!Number.isSafeInteger(sessionTimeMs)) throw new RangeError('Replay time must be an integer millisecond')
  const sampledDrivers = Object.fromEntries(Object.entries(prepared.drivers).map(([id, driver]) => [id, sampleDriver(driver, sessionTimeMs, prepared.circuitLengthMeters, prepared.coordinateInterpolation)]))
  const leaderboardOrder = copyStepArray(previousValue(prepared.leaderboardOrder, sessionTimeMs))
  const drivers = setSampledLeaderGapToZero(sampledDrivers, leaderboardOrder)
  return Object.freeze({
    sessionTimeMs,
    drivers,
    leaderboardOrder,
    trackStatusCode: previousValue(prepared.trackStatusCode, sessionTimeMs),
    weatherState: previousValue(prepared.weatherState, sessionTimeMs),
    events: Object.freeze(exactTimeEvents(prepared.events, sessionTimeMs).map(copyEvent)),
  })
}

function prepareDriver(replay: ReplayData, timeline: AuthoritativeTimeline, driverId: string): PreparedDriver {
  const prepare = <T extends keyof DriverColumns>(field: T): PreparedValues<NonNullable<DriverColumns[T][number]>> => {
    const times: number[] = []
    const values: NonNullable<DriverColumns[T][number]>[] = []
    for (const sample of timeline.samples) {
      const value = replay.chunks[sample.chunkIndex].drivers[driverId][field][sample.timeIndex]
      if (value !== null && value !== undefined) {
        times.push(sample.timeMs)
        values.push(value as NonNullable<DriverColumns[T][number]>)
      }
    }
    return Object.freeze({ times: Object.freeze(times), values: Object.freeze(values) })
  }
  const continuous = Object.freeze(Object.fromEntries(CONTINUOUS_FIELDS.map((field) => [field, prepare(field)])) as Record<ContinuousField, PreparedValues<number>>)
  const step = Object.freeze(Object.fromEntries(STEP_FIELDS.map((field) => [field, prepare(field)])) as Record<StepField, PreparedValues<number | string | boolean>>)
  return Object.freeze({
    continuous,
    filteredCoordinates: Object.freeze({ x: preparePositionFilteredValues(continuous.x, step), y: preparePositionFilteredValues(continuous.y, step) }),
    step,
  })
}

function prepareGlobal<T extends GlobalField>(replay: ReplayData, timeline: AuthoritativeTimeline, field: T): PreparedValues<NonNullable<ReplayChunk[T][number]>> {
  const times: number[] = []
  const values: NonNullable<ReplayChunk[T][number]>[] = []
  for (const sample of timeline.samples) {
    const value = replay.chunks[sample.chunkIndex][field][sample.timeIndex]
    if (value !== null && value !== undefined) {
      times.push(sample.timeMs)
      values.push(value as NonNullable<ReplayChunk[T][number]>)
    }
  }
  return Object.freeze({ times: Object.freeze(times), values: Object.freeze(values) })
}

function sampleDriver(driver: PreparedDriver, timeMs: number, circuitLengthMeters: number, coordinateInterpolation: CoordinateInterpolationStrategy): DriverSnapshot {
  const continuous = (field: ContinuousField) => interpolate(driver.continuous[field], timeMs)
  const step = <T,>(field: StepField): T | null => previousValue(driver.step[field], timeMs) as T | null
  return Object.freeze({
    x: interpolateCoordinate(driver, 'x', timeMs, coordinateInterpolation), y: interpolateCoordinate(driver, 'y', timeMs, coordinateInterpolation), trackDistanceMeters: interpolateCircuitDistance(driver.continuous.trackDistanceMeters, timeMs, circuitLengthMeters), speed: continuous('speed'), throttle: continuous('throttle'), brake: continuous('brake'), gapToLeaderMs: continuous('gapToLeaderMs'),
    lap: step<number>('lap'), position: step<number>('position'), gear: step<number>('gear'), drs: step<number>('drs'), tyreCompound: step<string>('tyreCompound'), status: step<string>('status'), isInPitLane: step<boolean>('isInPitLane'),
  })
}

function interpolateCoordinate(driver: PreparedDriver, field: 'x' | 'y', timeMs: number, strategy: CoordinateInterpolationStrategy): number | null {
  if (strategy === 'smooth') return interpolate(driver.filteredCoordinates[field], timeMs, MAX_POSITION_INTERPOLATION_INTERVAL_MS)
  return interpolate(driver.continuous[field], timeMs, MAX_POSITION_INTERPOLATION_INTERVAL_MS)
}

function preparePositionFilteredValues(values: PreparedValues<number>, step: PreparedDriver['step']): PreparedValues<number> {
  if (values.values.length <= 2) return values
  const filtered = values.values.map((value, index) => {
    if (index === 0 || index === values.values.length - 1 || !isPositionFilterEligible(step, values.times[index])) return value
    let weightedTotal = 0
    let totalWeight = 0
    for (let candidate = index; candidate >= 0; candidate -= 1) {
      const distanceMs = values.times[index] - values.times[candidate]
      if (distanceMs > SMOOTH_FILTER_WINDOW_MS || !isPositionFilterEligible(step, values.times[candidate])) break
      const weight = 1 - distanceMs / SMOOTH_FILTER_WINDOW_MS
      weightedTotal += values.values[candidate] * weight
      totalWeight += weight
    }
    for (let candidate = index + 1; candidate < values.values.length; candidate += 1) {
      const distanceMs = values.times[candidate] - values.times[index]
      if (distanceMs > SMOOTH_FILTER_WINDOW_MS || !isPositionFilterEligible(step, values.times[candidate])) break
      const weight = 1 - distanceMs / SMOOTH_FILTER_WINDOW_MS
      weightedTotal += values.values[candidate] * weight
      totalWeight += weight
    }
    const candidate = weightedTotal / totalWeight
    return Math.min(Math.max(candidate, value - MAX_SMOOTH_FILTER_DISPLACEMENT_METERS), value + MAX_SMOOTH_FILTER_DISPLACEMENT_METERS)
  })
  return Object.freeze({ times: values.times, values: Object.freeze(filtered) })
}

function isPositionFilterEligible(step: PreparedDriver['step'], timeMs: number): boolean {
  if (previousValue(step.isInPitLane, timeMs) === true) return false
  const status = previousValue(step.status, timeMs)
  return typeof status !== 'string' || status.toLowerCase().replace(/[^a-z]/g, '') !== 'offtrack'
}

function interpolate(values: PreparedValues<number>, timeMs: number, maxIntervalMs = MAX_INTERPOLATION_INTERVAL_MS): number | null {
  const upperIndex = findTimeIndex(values.times, timeMs)
  const lowerIndex = upperIndex < values.times.length && values.times[upperIndex] === timeMs ? upperIndex : upperIndex - 1
  if (lowerIndex === upperIndex) return values.values[lowerIndex]
  if (lowerIndex < 0 || upperIndex === values.times.length || values.times[upperIndex] - values.times[lowerIndex] > maxIntervalMs) return null
  return values.values[lowerIndex] + (values.values[upperIndex] - values.values[lowerIndex]) * ((timeMs - values.times[lowerIndex]) / (values.times[upperIndex] - values.times[lowerIndex]))
}

/** Keeps the marker moving forward across the centerline without changing source samples. */
function interpolateCircuitDistance(values: PreparedValues<number>, timeMs: number, circuitLengthMeters: number): number | null {
  const upperIndex = findTimeIndex(values.times, timeMs)
  const lowerIndex = upperIndex < values.times.length && values.times[upperIndex] === timeMs ? upperIndex : upperIndex - 1
  if (lowerIndex === upperIndex) return values.values[lowerIndex]
  if (lowerIndex < 0 || upperIndex === values.times.length || values.times[upperIndex] - values.times[lowerIndex] > MAX_INTERPOLATION_INTERVAL_MS) return null

  const lower = values.values[lowerIndex]
  const upper = values.values[upperIndex]
  const ratio = (timeMs - values.times[lowerIndex]) / (values.times[upperIndex] - values.times[lowerIndex])
  if (isApprovedGeometricWrap(lower, upper, circuitLengthMeters)) return (lower + (upper + circuitLengthMeters - lower) * ratio) % circuitLengthMeters
  if (isLargeBackwardDistance(lower, upper, circuitLengthMeters)) return null
  return lower + (upper - lower) * ratio
}

function isApprovedGeometricWrap(lower: number, upper: number, circuitLengthMeters: number): boolean {
  return isValidCircuitLength(circuitLengthMeters)
    && lower >= circuitLengthMeters * 0.9
    && upper <= circuitLengthMeters * 0.1
    && lower - upper >= circuitLengthMeters * 0.8
}

function isLargeBackwardDistance(lower: number, upper: number, circuitLengthMeters: number): boolean {
  return isValidCircuitLength(circuitLengthMeters) && lower - upper >= circuitLengthMeters * 0.8
}

function isValidCircuitLength(value: number): boolean { return Number.isFinite(value) && value > 0 }

function setSampledLeaderGapToZero(
  drivers: Record<string, DriverSnapshot>, leaderboardOrder: readonly string[] | null,
): Readonly<Record<string, DriverSnapshot>> {
  const leaderId = leaderboardOrder?.[0]
  if (leaderId === undefined) return Object.freeze(drivers)
  const leader = drivers[leaderId]
  if (leader?.position !== 1) return Object.freeze(drivers)
  return Object.freeze({ ...drivers, [leaderId]: Object.freeze({ ...leader, gapToLeaderMs: 0 }) })
}

function previousValue<T>(values: PreparedValues<T>, timeMs: number): T | null {
  const index = findTimeIndex(values.times, timeMs)
  const previousIndex = index < values.times.length && values.times[index] === timeMs ? index : index - 1
  return previousIndex < 0 ? null : values.values[previousIndex]
}

function exactTimeEvents(events: readonly ReplayEvent[], timeMs: number): readonly ReplayEvent[] {
  let low = 0
  let high = events.length
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    if (events[middle].sessionTimeMs < timeMs) low = middle + 1
    else high = middle
  }
  const first = low
  let end = first
  while (events[end]?.sessionTimeMs === timeMs) end += 1
  return events.slice(first, end)
}

function findTimeIndex(times: readonly number[], timeMs: number): number {
  let low = 0
  let high = times.length
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    if (times[middle] < timeMs) low = middle + 1
    else high = middle
  }
  return low
}

function copyStepArray(value: readonly string[] | null): readonly string[] | null { return value === null ? null : Object.freeze([...value]) }
function copyEvent(event: ReplayEvent): ReplayEvent { return Object.freeze({ ...event, ...(event.payload ? { payload: deepFreezeCopy(event.payload) } : {}) }) }
function byEventTime(left: ReplayEvent, right: ReplayEvent): number { return left.sessionTimeMs - right.sessionTimeMs }
function deepFreezeCopy<T>(value: T): T {
  if (Array.isArray(value)) return Object.freeze(value.map(deepFreezeCopy)) as T
  if (value !== null && typeof value === 'object') return Object.freeze(Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, deepFreezeCopy(entry)]))) as T
  return value
}
