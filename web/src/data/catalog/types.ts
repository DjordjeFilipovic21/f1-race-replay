import type { ReplaySource } from '../replay/types'

export interface CatalogV2Session {
  readonly session_code: string
  readonly session_name: string
  readonly generation_id: string | null
  readonly delivery_version: string | null
  readonly outcome: string
  readonly validated: boolean
  readonly canonical_pointer: string | null
  readonly browser_pointer: string | null
}

export interface CatalogV2Race {
  readonly race_id: string
  readonly round_number: number
  readonly event_name: string
  readonly country?: string | null
  readonly location?: string | null
  readonly event_date?: string | null
  readonly sessions: readonly CatalogV2Session[]
}

export interface CatalogV2 {
  readonly schemaVersion: 2
  readonly year: number
  readonly atomicAcrossRaces: boolean
  readonly races: readonly CatalogV2Race[]
}

export interface BrowserPointerResolution {
  /** The race browser root, relative to the season directory. */
  readonly browserBasePath: string
  /** The pointer path, relative to browserBasePath. */
  readonly pointerPath: string
}

export interface CatalogSelection {
  readonly race: CatalogV2Race
  readonly session: CatalogV2Session
}

export interface LoadCatalogOptions {
  readonly source: ReplaySource
  readonly year: number
  /** Optional path prefix when the source is rooted above the seasons directory. */
  readonly seasonsBase?: string
}

/** Names matching the Python contract's record terminology. */
export type CatalogV2Payload = CatalogV2
export type CatalogV2RaceRecord = CatalogV2Race
export type CatalogV2SessionRecord = CatalogV2Session
