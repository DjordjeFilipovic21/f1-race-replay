import type {
  LapKind,
  LapSectorDriverColumns,
  LapSectorSidecar,
  QualifyingPhase,
  QualifyingLapStatus,
  QualifyingLapStatusSidecar,
  QualifyingSummary,
} from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'
import { isFlyingEvidence, isQualifyingLikeSidecar, type SectorColumnFields, type SectorNumber } from './lap-sector-selectors'
import { selectQualifyingLapStatus } from './qualifying-lap-status-selectors'

const Q2_ADVANCER_LIMIT = 10

export type QualifyingLiveLapPhase = 'flying' | 'outlap' | 'inlap' | 'unknown'
export type QualifyingLiveLapEvidenceStatus = QualifyingLapStatus | 'unknown' | 'unavailable'
export type QualifyingLiveClassification = 'classified' | 'out' | 'unavailable'

export interface QualifyingLiveSectorEvidence {
  readonly sectorNumber: SectorNumber
  readonly durationMs: number | null
  readonly sessionTimeMs: number
}

export interface QualifyingLiveLapEvidence {
  readonly lapNumber: number
  readonly qualifyingPhase: QualifyingPhase | null
  readonly status: QualifyingLiveLapEvidenceStatus
  /** Aligned delivery classification; null when the capability is absent. */
  readonly lapKind: LapKind | null
  readonly lapStartMs: number
  readonly lapEndMs: number | null
  readonly lapDurationMs: number | null
  readonly sectors: readonly QualifyingLiveSectorEvidence[]
}

export interface QualifyingLiveState {
  readonly driverId: string
  readonly sessionTimeMs: number | null
  readonly sampledLap: number | null
  readonly sampledStatus: string | null
  readonly rawSampledStatus: string | null
  readonly tyreCompound: string | null
  readonly tyreAge: number | null
  readonly tyre: Readonly<{ readonly compound: string | null; readonly age: number | null }>
  readonly lapPhase: QualifyingLiveLapPhase
  readonly lapState: QualifyingLiveLapPhase
  readonly causalLapEvidence: readonly QualifyingLiveLapEvidence[]
  readonly currentLapEvidence: QualifyingLiveLapEvidence | null
  readonly activeQualifyingPhase: QualifyingPhase | null
  readonly fastestCausalLapDurationMs: number | null
  /** The fastest causal qualifying time displayed when the active phase is finished. */
  readonly finishedLapDurationMs: number | null
  /** The frozen result time for a driver eliminated from a completed phase. */
  readonly terminalLapDurationMs: number | null
  readonly qualifyingPosition: number | null
  readonly classification: QualifyingLiveClassification
  readonly isOut: boolean
  readonly isQualifyingComplete: boolean
  readonly isFinished: boolean
}

type ReplayBoundary = ReplaySnapshot | null

/**
 * Selects the sampled qualifying state without mutating replay or sidecar data.
 *
 * The summary supplies phase-specific fallback results only after that phase
 * has causally completed. In particular, final qualifyingPosition is never a
 * source for a live row. The optional status sidecar is fail-closed for lap
 * evidence: absent is unavailable, while an absent lap in a present sidecar is
 * explicitly unknown.
 */
export function selectQualifyingLiveState(
  snapshot: ReplayBoundary,
  driverId: string,
  qualifyingSummary?: QualifyingSummary | null,
  lapSectorSidecar?: LapSectorSidecar | null,
  qualifyingLapStatus?: QualifyingLapStatusSidecar | null,
  replayEndMs?: number | null,
  driverIds?: readonly string[],
): QualifyingLiveState {
  const sessionTimeMs = snapshot?.sessionTimeMs ?? null
  const sampled = snapshot?.drivers[driverId]
  const sampledLap = sampled?.lap ?? null
  const sampledStatus = sampled?.status ?? null
  const tyreCompound = sampled?.tyreCompound ?? null
  const tyreAge = sampled?.tyreAge ?? null
  const evidence = selectCausalEvidence(lapSectorSidecar, qualifyingLapStatus, driverId, sessionTimeMs)
  const currentLapEvidence = sampledLap === null
    ? null
    : evidence.find((lap) => lap.lapNumber === sampledLap) ?? null
  const qualifyingLike = isQualifyingLikeSidecar(lapSectorSidecar)
  const lapPhase = inferLapPhase(currentLapEvidence, qualifyingLike)
  const activeQualifyingPhase = selectActiveQualifyingPhase(lapSectorSidecar, sessionTimeMs)
  const fastestCausalLapDurationMs = selectFastestCausalLapDuration(evidence, activeQualifyingPhase)
  const ids = collectDriverIds(driverId, driverIds, qualifyingSummary, lapSectorSidecar)
  const model = selectQualifyingPhaseModel(snapshot, ids, qualifyingSummary, lapSectorSidecar, qualifyingLapStatus, replayEndMs)
  const terminalPhase = model.terminalPhaseByDriver.get(driverId) ?? null
  const terminalLapDurationMs = terminalPhase === null
    ? null
    : model.phaseTimesByDriver.get(driverId)?.get(terminalPhase) ?? null
  const finishedLapDurationMs = model.finishedLapDurationsByDriver.get(driverId) ?? null
  const classification = qualifyingSummary == null
    ? 'unavailable'
    : model.eliminated.has(driverId) ? 'out' : 'classified'

  return freeze({
    driverId,
    sessionTimeMs,
    sampledLap,
    sampledStatus,
    rawSampledStatus: sampledStatus,
    tyreCompound,
    tyreAge,
    tyre: freeze({ compound: tyreCompound, age: tyreAge }),
    lapPhase,
    lapState: lapPhase,
    causalLapEvidence: freeze(evidence),
    currentLapEvidence,
    activeQualifyingPhase,
    fastestCausalLapDurationMs,
    finishedLapDurationMs,
    terminalLapDurationMs,
    qualifyingPosition: model.positions.get(driverId) ?? null,
    classification,
    isOut: classification === 'out',
    isQualifyingComplete: model.isQualifyingComplete,
    isFinished: model.finished.has(driverId),
  })
}

/** Selects immutable qualifying state for each supplied driver in input order. */
export function selectQualifyingLiveStates(
  snapshot: ReplayBoundary,
  driverIds: readonly string[],
  qualifyingSummary?: QualifyingSummary | null,
  lapSectorSidecar?: LapSectorSidecar | null,
  qualifyingLapStatus?: QualifyingLapStatusSidecar | null,
  replayEndMs?: number | null,
): readonly QualifyingLiveState[] {
  return Object.freeze(driverIds.map((driverId) => selectQualifyingLiveState(
    snapshot,
    driverId,
    qualifyingSummary,
    lapSectorSidecar,
    qualifyingLapStatus,
    replayEndMs,
    driverIds,
  )))
}

interface QualifyingPhaseModel {
  readonly positions: ReadonlyMap<string, number>
  readonly eliminated: ReadonlySet<string>
  readonly finished: ReadonlySet<string>
  readonly finishedLapDurationsByDriver: ReadonlyMap<string, number | null>
  readonly terminalPhaseByDriver: ReadonlyMap<string, QualifyingPhase>
  readonly phaseTimesByDriver: ReadonlyMap<string, ReadonlyMap<QualifyingPhase, number | null>>
  readonly isQualifyingComplete: boolean
}

function selectQualifyingPhaseModel(
  snapshot: ReplayBoundary,
  driverIds: readonly string[],
  summary: QualifyingSummary | null | undefined,
  sidecar: LapSectorSidecar | null | undefined,
  qualifyingLapStatus: QualifyingLapStatusSidecar | null | undefined,
  replayEndMs: number | null | undefined,
): QualifyingPhaseModel {
  const sessionTimeMs = snapshot?.sessionTimeMs ?? null
  const activePhase = selectActiveQualifyingPhase(sidecar, sessionTimeMs)
  const qualifyingLike = isQualifyingLikeSidecar(sidecar)
  const evidenceByDriver = new Map(driverIds.map((id) => [
    id,
    selectCausalEvidence(sidecar, qualifyingLapStatus, id, sessionTimeMs),
  ]))
  const eliminated = new Set<string>()
  const positions = new Map<string, number>()
  const terminalPhaseByDriver = new Map<string, QualifyingPhase>()
  const phaseTimesByDriver = new Map<string, Map<QualifyingPhase, number | null>>()
  const completedPhases = (['Q1', 'Q2', 'Q3'] as const).filter((phase) => isQualifyingPhaseComplete(sidecar, phase, sessionTimeMs, replayEndMs))

  for (const phase of completedPhases) {
    const phaseParticipants = driverIds.filter((id) => !eliminated.has(id))
    const completionBoundary = selectQualifyingPhaseCompletionBoundary(sidecar, phase, replayEndMs)
    const phaseEvidenceByDriver = completionBoundary === null
      ? evidenceByDriver
      : new Map(driverIds.map((id) => [
        id,
        selectCausalEvidence(sidecar, qualifyingLapStatus, id, completionBoundary),
      ]))
    const result = rankPhaseDrivers(phaseParticipants, phase, phaseEvidenceByDriver, summary, true)
    for (const id of phaseParticipants) {
      const driverTimes = phaseTimesByDriver.get(id) ?? new Map<QualifyingPhase, number | null>()
      driverTimes.set(phase, result.times.get(id) ?? null)
      phaseTimesByDriver.set(id, driverTimes)
    }
    const advancingCount = phase === 'Q1' ? q1AdvancerLimit(driverIds.length) : phase === 'Q2' ? Math.min(Q2_ADVANCER_LIMIT, phaseParticipants.length) : phaseParticipants.length
    const phaseEliminated = phase === 'Q3' ? result.rankedIds.filter((id) => !hasPhaseTime(result.times.get(id))) : result.rankedIds.slice(advancingCount)
    setTimedPositions(positions, result)
    for (const id of phaseEliminated) {
      eliminated.add(id)
      terminalPhaseByDriver.set(id, phase)
      if (phase === 'Q3') positions.delete(id)
    }
    if (phase === activePhase || (phase === 'Q3' && completedPhases.includes('Q3'))) {
      setTimedPositions(positions, result, (id) => !eliminated.has(id))
    }
  }

  if (activePhase !== null && !completedPhases.includes(activePhase)) {
    const activeParticipants = driverIds.filter((id) => !eliminated.has(id))
    activeParticipants.forEach((id) => positions.delete(id))
    const liveResult = rankPhaseDrivers(activeParticipants, activePhase, evidenceByDriver, summary, false)
    setTimedPositions(positions, liveResult, (id) => !eliminated.has(id))
  }

  const finished = new Set<string>()
  const finishedLapDurationsByDriver = new Map<string, number | null>()
  if (activePhase !== null) {
    for (const id of driverIds) {
      const finishedLap = selectFinishedLap(
        sidecar,
        id,
        activePhase,
        sessionTimeMs,
        evidenceByDriver.get(id) ?? [],
        qualifyingLike,
      )
      if (finishedLap.isFinished) {
        finished.add(id)
        finishedLapDurationsByDriver.set(id, finishedLap.displayDurationMs)
      }
    }
  }
  return {
    positions,
    eliminated,
    finished,
    finishedLapDurationsByDriver,
    terminalPhaseByDriver,
    phaseTimesByDriver,
    isQualifyingComplete: completedPhases.includes('Q3'),
  }
}

function setTimedPositions(
  positions: Map<string, number>,
  result: { readonly rankedIds: readonly string[]; readonly times: ReadonlyMap<string, number | null> },
  include: (driverId: string) => boolean = () => true,
): void {
  result.rankedIds.forEach((id, index) => {
    if (include(id) && hasPhaseTime(result.times.get(id))) positions.set(id, index + 1)
  })
}

function rankPhaseDrivers(
  driverIds: readonly string[],
  phase: QualifyingPhase,
  evidenceByDriver: ReadonlyMap<string, readonly QualifyingLiveLapEvidence[]>,
  summary: QualifyingSummary | null | undefined,
  allowSummaryFallback: boolean,
): { readonly rankedIds: readonly string[]; readonly times: ReadonlyMap<string, number | null> } {
  const times = new Map(driverIds.map((id) => [id, phaseTime(id, phase, evidenceByDriver.get(id) ?? [], summary, allowSummaryFallback)]))
  const rankedIds = [...driverIds].sort((left, right) => {
    const leftTime = times.get(left) ?? null
    const rightTime = times.get(right) ?? null
    if (leftTime === null && rightTime === null) return left.localeCompare(right)
    if (leftTime === null) return 1
    if (rightTime === null) return -1
    return leftTime - rightTime || left.localeCompare(right)
  })
  return { rankedIds, times }
}

function phaseTime(
  driverId: string,
  phase: QualifyingPhase,
  evidence: readonly QualifyingLiveLapEvidence[],
  summary: QualifyingSummary | null | undefined,
  allowSummaryFallback: boolean,
): number | null {
  const causal = selectFastestCausalLapDuration(evidence, phase)
  if (causal !== null || !allowSummaryFallback) return causal
  const columns = summary?.drivers[driverId]
  if (columns === undefined) return null
  if (phase === 'Q1') return readFinite(columns.q1TimeMs[0])
  if (phase === 'Q2') return readFinite(columns.q2TimeMs[0])
  return readFinite(columns.q3TimeMs[0])
}

function isQualifyingPhaseComplete(sidecar: LapSectorSidecar | null | undefined, phase: QualifyingPhase, sessionTimeMs: number | null, replayEndMs: number | null | undefined): boolean {
  const completionBoundary = selectQualifyingPhaseCompletionBoundary(sidecar, phase, replayEndMs)
  return sessionTimeMs !== null && completionBoundary !== null && completionBoundary <= sessionTimeMs
}

function selectQualifyingPhaseCompletionBoundary(
  sidecar: LapSectorSidecar | null | undefined,
  phase: QualifyingPhase,
  replayEndMs: number | null | undefined,
): number | null {
  if (sidecar?.contractVersion !== 'v2') return null
  const boundaryIndex = sidecar.phaseBoundaries.findIndex((boundary) => boundary.phase === phase)
  if (boundaryIndex < 0) return null
  const nextBoundary = sidecar.phaseBoundaries[boundaryIndex + 1]
  if (nextBoundary !== undefined) return nextBoundary.startMs
  return phase === 'Q3' && replayEndMs !== null && replayEndMs !== undefined && Number.isFinite(replayEndMs) ? replayEndMs : null
}

function collectDriverIds(driverId: string, driverIds: readonly string[] | undefined, summary: QualifyingSummary | null | undefined, sidecar: LapSectorSidecar | null | undefined): readonly string[] {
  return [...new Set([driverId, ...(driverIds ?? []), ...Object.keys(summary?.drivers ?? {}), ...Object.keys(sidecar?.drivers ?? {})])].sort()
}

function hasPhaseTime(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value) && value > 0
}

function q1AdvancerLimit(driverCount: number): number {
  return Math.min(driverCount, Math.floor(driverCount / 2) + 5)
}

function selectCausalEvidence(
  sidecar: LapSectorSidecar | null | undefined,
  qualifyingLapStatus: QualifyingLapStatusSidecar | null | undefined,
  driverId: string,
  sessionTimeMs: number | null,
): readonly QualifyingLiveLapEvidence[] {
  if (sidecar == null || sessionTimeMs === null) return []
  const columns = sidecar.drivers[driverId]
  if (columns === undefined) return []
  const qualifyingLike = isQualifyingLikeSidecar(sidecar)

  return columns.lapNumber.flatMap((lapNumber, index) => {
    const lapStartMs = columns.lapStartMs[index]
    if (!Number.isFinite(lapStartMs) || lapStartMs > sessionTimeMs) return []

    const lapKind = columns.lapKind?.[index] ?? null
    const status = qualifyingLapStatus == null
      ? 'unavailable'
      : selectQualifyingLapStatus(qualifyingLapStatus, sessionTimeMs, driverId, lapNumber) ?? 'unknown'
    // Outlap/inlap/unknown/deleted laps contribute no timing or sectors; for
    // qualifying-like sidecars without the lapKind column nothing is flying.
    const timingEligible = isTimingEvidenceStatus(status) && isFlyingEvidence(columns, index, qualifyingLike)
    const sectors = timingEligible
      ? selectCausalSectors(columns, index, sessionTimeMs)
      : []
    const lapEndMs = causalTime(columns.lapEndMs[index], sessionTimeMs)
    const qualifyingPhase = columns.qualifyingPhase?.[index] ?? null
    return [freeze({
      lapNumber,
      qualifyingPhase,
      status,
      lapKind,
      lapStartMs,
      lapEndMs,
      lapDurationMs: lapEndMs === null || !timingEligible
        ? null
        : columns.lapDurationMs[index],
      sectors: freeze(sectors),
    })]
  })
}

interface FinishedLapSelection {
  readonly isFinished: boolean
  readonly displayDurationMs: number | null
}

function selectFinishedLap(
  sidecar: LapSectorSidecar | null | undefined,
  driverId: string,
  activePhase: QualifyingPhase,
  sessionTimeMs: number | null,
  evidence: readonly QualifyingLiveLapEvidence[],
  qualifyingLike: boolean,
): FinishedLapSelection {
  if (sidecar?.contractVersion !== 'v2' || sessionTimeMs === null) return notFinishedLap()
  const columns = sidecar.drivers[driverId]
  if (columns === undefined) return notFinishedLap()

  // Finish is the last flying lap in the active phase, never the literal last
  // sidecar row: a later cooldown or pit-in lap must not delay it.
  const lastFlyingLapNumber = columns.lapNumber.reduce<number | null>((lastLap, lapNumber, index) => {
    if (columns.qualifyingPhase[index] !== activePhase) return lastLap
    if (!isFlyingEvidence(columns, index, qualifyingLike)) return lastLap
    return lastLap === null || lapNumber > lastLap ? lapNumber : lastLap
  }, null)
  if (lastFlyingLapNumber === null) return notFinishedLap()

  const lastFlyingLap = evidence.find((lap) => lap.lapNumber === lastFlyingLapNumber)
  if (lastFlyingLap === undefined || !isTimingEvidenceStatus(lastFlyingLap.status) || lastFlyingLap.lapEndMs === null) return notFinishedLap()
  const displayDurationMs = selectFastestCausalLapDuration(evidence, activePhase)
  return {
    isFinished: displayDurationMs !== null,
    displayDurationMs,
  }
}

function notFinishedLap(): FinishedLapSelection {
  return { isFinished: false, displayDurationMs: null }
}

/** Returns the authoritative Q phase at a causal replay cursor. */
export function selectActiveQualifyingPhase(
  sidecar: LapSectorSidecar | null | undefined,
  sessionTimeMs: number | null,
): QualifyingPhase | null {
  if (sidecar?.contractVersion !== 'v2' || sessionTimeMs === null) return null
  return sidecar.phaseBoundaries.reduce<QualifyingPhase | null>((activePhase, boundary) => (
    boundary.startMs <= sessionTimeMs ? boundary.phase : activePhase
  ), null)
}

/** Returns the fastest completed flying lap with no known causal invalidation. */
export function selectFastestCausalLapDuration(
  evidence: readonly QualifyingLiveLapEvidence[],
  activeQualifyingPhase: QualifyingPhase | null,
): number | null {
  if (activeQualifyingPhase === null) return null
  return evidence.reduce<number | null>((fastest, lap) => {
    if (lap.qualifyingPhase !== activeQualifyingPhase || !isTimingEvidenceStatus(lap.status)) return fastest
    // Only explicit flying laps contribute; outlap/inlap/unknown and causally
    // incomplete laps are excluded. A null lapKind is the legacy no-capability
    // case (race/sprint/practice) and keeps its historical behavior.
    if (lap.lapKind !== null && lap.lapKind !== 'flying') return fastest
    if (lap.lapEndMs === null || lap.lapDurationMs === null || !Number.isFinite(lap.lapDurationMs) || lap.lapDurationMs <= 0) return fastest
    return fastest === null || lap.lapDurationMs < fastest ? lap.lapDurationMs : fastest
  }, null)
}

function isTimingEvidenceStatus(status: QualifyingLiveLapEvidenceStatus): boolean {
  return status === 'valid' || status === 'unavailable'
}

function selectCausalSectors(columns: LapSectorDriverColumns, index: number, sessionTimeMs: number): readonly QualifyingLiveSectorEvidence[] {
  const fields: readonly SectorColumnFields[] = [
    [1, 'sector1DurationMs', 'sector1SessionTimeMs'],
    [2, 'sector2DurationMs', 'sector2SessionTimeMs'],
    [3, 'sector3DurationMs', 'sector3SessionTimeMs'],
  ]
  return fields.flatMap(([sectorNumber, durationField, timeField]) => {
    const sectorTimeMs = columns[timeField][index]
    if (sectorTimeMs === null || sectorTimeMs > sessionTimeMs) return []
    return [freeze({ sectorNumber, durationMs: columns[durationField][index], sessionTimeMs: sectorTimeMs })]
  })
}

function inferLapPhase(evidence: QualifyingLiveLapEvidence | null, qualifyingLike: boolean): QualifyingLiveLapPhase {
  if (evidence === null || evidence.status === 'deleted' || evidence.status === 'unknown') return 'unknown'
  if (evidence.lapKind === 'flying') return 'flying'
  if (evidence.lapKind === 'outlap') return 'outlap'
  if (evidence.lapKind === 'inlap') return 'inlap'
  if (evidence.lapKind === 'unknown') return 'unknown'
  // Capability absent: qualifying-like sidecars fail closed (no flying/outlap
  // guess), while non-qualifying sidecars keep the legacy sector-completion
  // heuristic that predates the aligned lapKind column.
  if (qualifyingLike) return 'unknown'
  // A started lap with no causal sector completion is the only outlap signal
  // available in sidecars without lapKind; a causal sector completion supports
  // flying.
  return evidence.sectors.length === 0 ? 'outlap' : 'flying'
}

function causalTime(value: number | null, sessionTimeMs: number): number | null {
  return value !== null && Number.isFinite(value) && value <= sessionTimeMs ? value : null
}

function readFinite(value: number | null | undefined): number | null {
  return value !== null && value !== undefined && Number.isFinite(value) ? value : null
}

function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value as Record<string, unknown>).forEach(freeze)
    Object.freeze(value)
  }
  return value
}
