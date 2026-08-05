import type {
  PitLossEstimateSidecar,
  PitLossEstimateStatus,
  PitLossModel,
} from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'
import { describeTrackStatus } from '../panels/track-status'

export type PitLossSelectionSource = 'race' | 'safety-car' | 'virtual-safety-car' | 'race-fallback'

export interface PitLossSelection {
  readonly timeMs: number | null
  readonly estimatedLossMs: number
  /** Current-race observations for legacy values; curated values are zero. */
  readonly observedSampleCount: number
  readonly isBaseline: boolean
  /** Stable machine-readable status origin for status-aware callers. */
  readonly source?: PitLossSelectionSource
  /** Stable human-readable status label for legacy callers. */
  readonly sourceLabel?: string
}

type CausalBoundary = number | Pick<ReplaySnapshot, 'sessionTimeMs'> | Pick<ReplaySnapshot, 'sessionTimeMs' | 'trackStatusCode'>

const SOURCE_LABELS: Readonly<Record<PitLossSelectionSource, string>> = {
  race: 'Race estimate',
  'safety-car': 'Safety Car estimate',
  'virtual-safety-car': 'Virtual Safety Car estimate',
  'race-fallback': 'Race estimate (fallback)',
}

/** Returns the latest pit-loss estimate available at the replay cursor. */
export function selectPitLossEstimate(
  model: PitLossModel | null | undefined,
  boundary: CausalBoundary,
  sidecar?: PitLossEstimateSidecar | null,
): PitLossSelection | null
/** Compatibility overload for callers that keep the optional sidecar before the cursor. */
export function selectPitLossEstimate(
  model: PitLossModel | null | undefined,
  sidecar: PitLossEstimateSidecar | null | undefined,
  boundary: CausalBoundary,
): PitLossSelection | null
export function selectPitLossEstimate(
  model: PitLossModel | null | undefined,
  boundaryOrSidecar: CausalBoundary | PitLossEstimateSidecar | null | undefined,
  sidecarOrBoundary?: PitLossEstimateSidecar | CausalBoundary | null,
): PitLossSelection | null {
  const { boundary, sidecar } = normalizeArguments(boundaryOrSidecar, sidecarOrBoundary)
  if (model === null || model === undefined) {
    if (sidecar === null || sidecar === undefined) return null
  }

  if (sidecar === null || sidecar === undefined) {
    if (model === null || model === undefined) return null
    const legacySelection = selectLegacyModel(model, boundary, true)
    return legacySelection === null ? null : withSource(legacySelection, 'race')
  }

  const requestedSource = getRequestedSource(boundary, sidecar)
  if (requestedSource === 'race') {
    const selectedRaceSample = selectTimelineSample(sidecar.race, getSessionTimeMs(boundary), sidecar)
    if (selectedRaceSample !== null) return withSource(selectedRaceSample, 'race')
  } else if (requestedSource !== 'race-fallback') {
    const statusTimeline = getStatusTimeline(sidecar, requestedSource)
    const selectedStatusSample = statusTimeline === null
      ? null
      : selectTimelineSample(statusTimeline, getSessionTimeMs(boundary), sidecar)
    if (selectedStatusSample !== null) return withSource(selectedStatusSample, requestedSource)
  }

  return selectRaceFallback(model, sidecar, boundary, requestedSource === 'race' ? 'race' : 'race-fallback')
}

/** Alias kept concise for panel view-model code. */
export const selectPitLoss = selectPitLossEstimate

function normalizeArguments(
  boundaryOrSidecar: CausalBoundary | PitLossEstimateSidecar | null | undefined,
  sidecarOrBoundary: PitLossEstimateSidecar | CausalBoundary | null | undefined,
): { readonly boundary: CausalBoundary; readonly sidecar: PitLossEstimateSidecar | null | undefined } {
  if (isCausalBoundary(boundaryOrSidecar)) {
    return {
      boundary: boundaryOrSidecar,
      sidecar: isPitLossEstimateSidecar(sidecarOrBoundary) ? sidecarOrBoundary : null,
    }
  }
  return {
    boundary: isCausalBoundary(sidecarOrBoundary) ? sidecarOrBoundary : 0,
    sidecar: isPitLossEstimateSidecar(boundaryOrSidecar) ? boundaryOrSidecar : null,
  }
}

function selectRaceFallback(
  model: PitLossModel | null | undefined,
  sidecar: PitLossEstimateSidecar,
  boundary: CausalBoundary,
  source: PitLossSelectionSource,
): PitLossSelection | null {
  // A curated sidecar has no race-derived fallback and must never fall back to
  // the legacy model: an unsupported, mixed, or unavailable status fails closed
  // even when a legacy model is present, so the old 22000 ms baseline cannot
  // surface for a curated delivery.  Legacy-sidecar fallback stays unchanged.
  if (isCuratedSidecar(sidecar)) return null

  const raceSample = selectTimelineSample(sidecar.race, getSessionTimeMs(boundary), sidecar)
  if (raceSample !== null) return withSource(raceSample, source)
  if (model === null || model === undefined) return null
  const legacySelection = selectLegacyModel(model, boundary)
  return legacySelection === null ? null : withSource(legacySelection, source)
}

function selectLegacyModel(model: PitLossModel, boundary: CausalBoundary, allowBaseline = false): PitLossSelection | null {
  const sessionTimeMs = getSessionTimeMs(boundary)
  const selectedIndex = findLatestIndex(model.timeMs, sessionTimeMs)
  if (selectedIndex < 0) {
    return allowBaseline
      ? freeze({ timeMs: null, estimatedLossMs: model.baselineMs, observedSampleCount: 0, isBaseline: true })
      : null
  }
  const observedSampleCount = model.observedSampleCount[selectedIndex]
  if (observedSampleCount <= 0) {
    return allowBaseline && observedSampleCount === 0
      ? freeze({
          timeMs: model.timeMs[selectedIndex],
          estimatedLossMs: model.estimatedLossMs[selectedIndex],
          observedSampleCount,
          isBaseline: true,
        })
      : null
  }
  return freeze({
    timeMs: model.timeMs[selectedIndex],
    estimatedLossMs: model.estimatedLossMs[selectedIndex],
    observedSampleCount,
    isBaseline: observedSampleCount === 0,
  })
}

function selectTimelineSample(
  timeline: Exclude<PitLossEstimateStatus, { readonly status: 'unavailable' }>,
  sessionTimeMs: number,
  sidecar: PitLossEstimateSidecar,
): PitLossSelection | null {
  const selectedIndex = findLatestIndex(timeline.timeMs, sessionTimeMs)
  if (selectedIndex < 0) return null

  if (isCuratedSidecar(sidecar)) {
    return freeze({
      timeMs: timeline.timeMs[selectedIndex],
      estimatedLossMs: timeline.estimatedLossMs[selectedIndex],
      observedSampleCount: 0,
      isBaseline: false,
    })
  }

  const observedSampleCount = timeline.observedSampleCount?.[selectedIndex]
  if (typeof observedSampleCount !== 'number' || !Number.isFinite(observedSampleCount) || observedSampleCount <= 0) return null
  return freeze({
    timeMs: timeline.timeMs[selectedIndex],
    estimatedLossMs: timeline.estimatedLossMs[selectedIndex],
    observedSampleCount,
    isBaseline: observedSampleCount === 0,
  })
}

function findLatestIndex(times: readonly number[], sessionTimeMs: number): number {
  let selectedIndex = -1
  let selectedTime = Number.NEGATIVE_INFINITY
  for (let index = 0; index < times.length; index += 1) {
    const timeMs = times[index]
    if (timeMs <= sessionTimeMs && timeMs >= selectedTime) {
      selectedIndex = index
      selectedTime = timeMs
    }
  }
  return selectedIndex
}

function getRequestedSource(
  boundary: CausalBoundary,
  sidecar: PitLossEstimateSidecar,
): PitLossSelectionSource {
  if (typeof boundary === 'number') {
    // A numeric boundary has no status sample.  Curated values are static
    // replay-start catalog data, so the safe status-neutral choice is Green;
    // keep the legacy sidecar's fallback label/behaviour unchanged.
    return isCuratedSidecar(sidecar) ? 'race' : 'race-fallback'
  }
  if (!('trackStatusCode' in boundary)) {
    // Preserve the older time-only boundary overload.  It has no explicit
    // unknown status, so curated deliveries can still expose Green at start.
    return isCuratedSidecar(sidecar) ? 'race' : 'race-fallback'
  }
  const status = describeTrackStatus('trackStatusCode' in boundary ? boundary.trackStatusCode : null)
  if (status.isSafetyCar) return 'safety-car'
  if (status.isVirtualSafetyCar) return 'virtual-safety-car'
  if (status.isAllClear) return 'race'
  return 'race-fallback'
}

function getStatusTimeline(sidecar: PitLossEstimateSidecar, source: PitLossSelectionSource): Exclude<PitLossEstimateStatus, { readonly status: 'unavailable' }> | null {
  const status = source === 'safety-car'
    ? sidecar.safetyCar
    : source === 'virtual-safety-car'
      ? sidecar.virtualSafetyCar
      : null
  return status !== null && status !== undefined && !('status' in status) ? status : null
}

function withSource(selection: PitLossSelection, source: PitLossSelectionSource): PitLossSelection {
  return freeze({ ...selection, source, sourceLabel: SOURCE_LABELS[source] })
}

function isCausalBoundary(value: unknown): value is CausalBoundary {
  return typeof value === 'number' || (
    value !== null &&
    typeof value === 'object' &&
    'sessionTimeMs' in value &&
    typeof value.sessionTimeMs === 'number'
  )
}

function isPitLossEstimateSidecar(value: unknown): value is PitLossEstimateSidecar {
  return value !== null && typeof value === 'object' && 'race' in value
}

function isCuratedSidecar(sidecar: PitLossEstimateSidecar): sidecar is Extract<PitLossEstimateSidecar, { readonly method: 'curated-track-baseline-v1' }> {
  return sidecar.method === 'curated-track-baseline-v1'
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
