import type {
  LapSectorSidecar,
  PitLossModel,
  QualifyingLapStatusSidecar,
  QualifyingSummary,
  QualifyingTimeline,
  SessionMode,
  StintSummary,
  TimelineSummary,
} from '../../data/replay/types'

export interface SessionArtifacts {
  readonly lapSectorSidecar?: LapSectorSidecar | null
  readonly timelineSummary?: TimelineSummary | null
  readonly stintSummary?: StintSummary | null
  readonly pitLossModel?: PitLossModel | null
  readonly qualifyingSummary?: QualifyingSummary | null
  readonly qualifyingLapStatus?: QualifyingLapStatusSidecar | null
  readonly qualifyingTimeline?: QualifyingTimeline | null
}

export interface SessionCapabilities {
  readonly mode: SessionMode
  readonly label: string
  readonly isRaceLike: boolean
  readonly isQualifyingLike: boolean
  readonly canShowRaceOrder: boolean
  readonly canShowRaceTimeline: boolean
  readonly canShowTyreStrategy: boolean
  readonly canShowPitLoss: boolean
  readonly canShowQualifyingClassification: boolean
  readonly canFilterQualifyingLapStatus: boolean
  readonly canShowQualifyingTimeline: boolean
}

const SESSION_LABELS: Readonly<Record<SessionMode, string>> = Object.freeze({
  practice: 'Practice',
  qualifying: 'Qualifying',
  race: 'Race',
  sprint: 'Sprint',
  'sprint-qualifying': 'Sprint qualifying',
  'sprint-shootout': 'Sprint shootout',
  testing: 'Testing',
})

export function getSessionLabel(mode: SessionMode): string {
  return SESSION_LABELS[mode]
}

export function isRaceSessionMode(mode: SessionMode): boolean {
  return mode === 'race' || mode === 'sprint'
}

export function isQualifyingSessionMode(mode: SessionMode): boolean {
  return mode === 'qualifying' || mode === 'sprint-qualifying' || mode === 'sprint-shootout'
}

/** Derives UI permissions from the session mode and delivered V2 artifacts. */
export function createSessionCapabilities(mode: SessionMode, artifacts: SessionArtifacts = {}): SessionCapabilities {
  const isRaceLike = isRaceSessionMode(mode)
  const isQualifyingLike = isQualifyingSessionMode(mode)
  return Object.freeze({
    mode,
    label: getSessionLabel(mode),
    isRaceLike,
    isQualifyingLike,
    canShowRaceOrder: isRaceLike,
    canShowRaceTimeline: isRaceLike && artifacts.timelineSummary != null,
    canShowTyreStrategy: isRaceLike && artifacts.stintSummary != null,
    canShowPitLoss: isRaceLike && artifacts.pitLossModel != null,
    canShowQualifyingClassification: isQualifyingLike && artifacts.qualifyingSummary != null,
    canFilterQualifyingLapStatus: isQualifyingLike && artifacts.qualifyingLapStatus != null,
    canShowQualifyingTimeline: isQualifyingLike && artifacts.qualifyingTimeline != null,
  })
}
