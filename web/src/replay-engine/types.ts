import type { ReplayEvent } from '../replay-data/types'

export interface DriverSnapshot {
  readonly x: number | null
  readonly y: number | null
  readonly trackDistanceMeters: number | null
  readonly speed: number | null
  readonly throttle: number | null
  readonly brake: number | null
  readonly gapToLeaderMs: number | null
  readonly lap: number | null
  readonly position: number | null
  readonly gear: number | null
  readonly drs: number | null
  readonly tyreCompound: string | null
  readonly status: string | null
  readonly isInPitLane: boolean | null
}

export interface ReplaySnapshot {
  readonly sessionTimeMs: number
  readonly drivers: Readonly<Record<string, DriverSnapshot>>
  readonly leaderboardOrder: readonly string[] | null
  readonly trackStatusCode: number | null
  readonly weatherState: string | null
  readonly events: readonly ReplayEvent[]
}

export interface AuthoritativeSample {
  readonly timeMs: number
  readonly chunkIndex: number
  readonly timeIndex: number
}

export interface AuthoritativeTimeline {
  readonly samples: readonly AuthoritativeSample[]
}
