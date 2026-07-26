import type { PitLossModel } from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'

export interface PitLossSelection {
  readonly timeMs: number | null
  readonly estimatedLossMs: number
  readonly observedSampleCount: number
  readonly isBaseline: boolean
}

type CausalBoundary = number | Pick<ReplaySnapshot, 'sessionTimeMs'>

/** Returns the latest pit-loss estimate available at the replay cursor. */
export function selectPitLossEstimate(
  model: PitLossModel | null | undefined,
  boundary: CausalBoundary,
): PitLossSelection | null {
  if (model === null || model === undefined) return null
  const sessionTimeMs = getSessionTimeMs(boundary)
  let selectedIndex = -1
  for (let index = 0; index < model.timeMs.length; index += 1) {
    if (model.timeMs[index] > sessionTimeMs) break
    selectedIndex = index
  }
  if (selectedIndex < 0) return freeze({ timeMs: null, estimatedLossMs: model.baselineMs, observedSampleCount: 0, isBaseline: true })
  const observedSampleCount = model.observedSampleCount[selectedIndex]
  return freeze({
    timeMs: model.timeMs[selectedIndex],
    estimatedLossMs: model.estimatedLossMs[selectedIndex],
    observedSampleCount,
    isBaseline: observedSampleCount === 0,
  })
}

/** Alias kept concise for panel view-model code. */
export const selectPitLoss = selectPitLossEstimate

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
