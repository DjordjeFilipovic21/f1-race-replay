export type JsonPrimitive = null | boolean | number | string
export interface JsonArray extends ReadonlyArray<JsonValue> {}
export interface JsonObject { readonly [key: string]: JsonValue }
export type JsonValue = JsonPrimitive | JsonArray | JsonObject

type ManifestSchemaId = 'urn:f1-cache-replay:schema:replay-data:v2:manifest'
type ChunkSchemaId = 'urn:f1-cache-replay:schema:replay-data:v2:chunk'
type TrackAssetsSchemaId = 'urn:f1-cache-replay:schema:replay-data:v2:track-assets'
type TimelineSummarySchemaId = 'urn:f1-cache-replay:schema:replay-data:v2:timeline-summary'
type LapSectorSchemaId = 'urn:f1-cache-replay:schema:replay-data:v2:browser-lap-sector-sidecar'
type StintSummarySchemaId = 'urn:f1-cache-replay:schema:replay-data:v2:stint-summary'
type PitLossSchemaId = 'urn:f1-cache-replay:schema:replay-data:v2:pit-loss-model'
type PenaltySchemaId = 'urn:f1-cache-replay:schema:replay-data:v2:penalty-sidecar'
type QualifyingSummarySchemaId = 'urn:f1-cache-replay:schema:replay-data:v2:qualifying-summary'
type QualifyingLapStatusSchemaId = 'urn:f1-cache-replay:schema:replay-data:v2:browser-qualifying-lap-status'
type QualifyingTimelineSchemaId = 'urn:f1-cache-replay:schema:replay-data:v2:qualifying-timeline'
type WeatherSidecarSchemaId = 'urn:f1-cache-replay:schema:replay-data:v2:weather-sidecar'

export interface ReplaySource {
  readonly read: (path: string) => Promise<Uint8Array>
}

export interface BrowserPointer {
  readonly formatVersion: 'browser-delivery-v2'
  readonly deliveryVersion: string
  readonly manifestPath: string
  readonly manifestSha256: string
}

export type SessionMode = 'race' | 'practice' | 'qualifying' | 'sprint' | 'sprint-qualifying' | 'sprint-shootout' | 'testing'

export type QualifyingSessionMode = 'qualifying' | 'sprint-qualifying' | 'sprint-shootout'

/**
 * Explicit per-lap classification emitted only for qualifying-like sessions.
 * An absent column means the capability is unavailable (fail closed); `unknown`
 * is the fail-closed value for insufficient source evidence. `unknown` is
 * distinct from `false` or `OUT`; it never contributes timing.
 */
export type LapKind = 'flying' | 'outlap' | 'inlap' | 'unknown'

export interface ArtifactReference {
  readonly path: string
  readonly schemaId: string
  readonly sha256?: string
}

export interface TimelineSummaryReference extends ArtifactReference {
  readonly path: 'timeline-summary.json'
  readonly schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:timeline-summary'
  readonly sha256: string
}

export interface LapSectorSidecarReference extends ArtifactReference {
  readonly path: 'lap-sector-sidecar.json'
  readonly schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:browser-lap-sector-sidecar'
  readonly sha256: string
}

export type QualifyingPhase = 'Q1' | 'Q2' | 'Q3'

export interface QualifyingPhaseBoundary {
  readonly phase: QualifyingPhase
  readonly startMs: number
}

export interface StintSummaryReference extends ArtifactReference {
  readonly path: 'stint-summary.json'
  readonly schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:stint-summary'
  readonly sha256: string
}

export interface PitLossModelReference extends ArtifactReference {
  readonly path: 'pit-loss-model.json'
  readonly schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:pit-loss-model'
  readonly sha256: string
}

export interface PenaltySidecarReference extends ArtifactReference {
  readonly path: 'penalty-sidecar.json'
  readonly schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:penalty-sidecar'
  readonly sha256: string
}

export interface WeatherSidecarReference extends ArtifactReference {
  readonly path: 'weather-sidecar.json'
  readonly schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:weather-sidecar'
  readonly sha256: string
}

export interface LapSectorDriverColumns {
  readonly lapNumber: readonly number[]
  readonly lapStartMs: readonly number[]
  readonly lapEndMs: readonly number[]
  readonly lapDurationMs: readonly (number | null)[]
  readonly sector1DurationMs: readonly (number | null)[]
  readonly sector2DurationMs: readonly (number | null)[]
  readonly sector3DurationMs: readonly (number | null)[]
  readonly sector1SessionTimeMs: readonly (number | null)[]
  readonly sector2SessionTimeMs: readonly (number | null)[]
  readonly sector3SessionTimeMs: readonly (number | null)[]
  /** Present and aligned with lapNumber in every V2 sidecar. */
  readonly qualifyingPhase?: readonly (QualifyingPhase | null)[]
  /** Optional aligned lap classification; absent means the capability is unavailable (fail closed). */
  readonly lapKind?: readonly LapKind[]
}

export interface LapSectorSidecar {
  readonly contractVersion: 'v2'
  readonly fixtureId: string
  readonly phaseBoundaries: readonly QualifyingPhaseBoundary[]
  readonly drivers: Readonly<Record<string, LapSectorDriverColumns & {
    readonly qualifyingPhase: readonly (QualifyingPhase | null)[]
  }>>
}

export interface StintDriverColumns {
  readonly stintNumber: readonly number[]
  readonly compound: readonly (string | null)[]
  readonly startLap: readonly number[]
  readonly endLap: readonly (number | null)[]
  readonly startTimeMs: readonly (number | null)[]
  readonly endTimeMs: readonly (number | null)[]
  readonly tyreLifeAtStart: readonly (number | null)[]
  readonly isFreshTyre: readonly (boolean | null)[]
  readonly pitInTimeMs: readonly (number | null)[]
  readonly pitOutTimeMs: readonly (number | null)[]
}

export interface StintSummary {
  readonly contractVersion: 'v2'
  readonly fixtureId: string
  readonly drivers: Readonly<Record<string, StintDriverColumns>>
}

export interface PitLossModel {
  readonly contractVersion: 'v2'
  readonly fixtureId: string
  readonly method: 'global-prior-weighted-mean-v1'
  readonly baselineMs: number
  readonly priorWeight: number
  readonly timeMs: readonly number[]
  readonly estimatedLossMs: readonly number[]
  readonly observedSampleCount: readonly number[]
}

export interface PenaltyIssuance {
  readonly driverId: string
  readonly sessionTimeMs: number
  readonly penaltyType: string
  readonly reason: string
  readonly rawMessage: string
  readonly lapNumber?: number
}

/** Penalties record issuance; they do not indicate an active or unserved state. */
export interface PenaltySidecar {
  readonly contractVersion: 'v2'
  readonly fixtureId: string
  readonly penaltyIssuances: readonly PenaltyIssuance[]
}

export interface QualifyingSummaryReference extends ArtifactReference {
  readonly path: 'qualifying-summary.json'
  readonly schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:qualifying-summary'
  readonly sha256: string
}

/** V2 manifest reference for the optional qualifying lap-status artifact. */
export interface QualifyingLapStatusReference extends ArtifactReference {
  readonly path: 'qualifying-lap-status.json'
  readonly schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:browser-qualifying-lap-status'
  readonly sha256: string
}

/** V2 manifest reference for the optional qualifying-safe timeline/incident artifact. */
export interface QualifyingTimelineReference extends ArtifactReference {
  readonly path: 'qualifying-timeline.json'
  readonly schemaId: 'urn:f1-cache-replay:schema:replay-data:v2:qualifying-timeline'
  readonly sha256: string
}

export type QualifyingLapStatus = 'valid' | 'deleted'
export type QualifyingLapStatusEventStatus = 'deleted' | 'reinstated'

export interface QualifyingLapStatusEvent {
  readonly driverId: string
  readonly lapNumber: number
  readonly eventTimeMs: number
  readonly status: QualifyingLapStatusEventStatus
  readonly reason: string | null
  readonly rawMessage: string
}

export interface QualifyingLapStatusRecord {
  readonly lapNumber: readonly number[]
  readonly lapStartMs: readonly number[]
  readonly lapEndMs: readonly number[]
  readonly status: readonly QualifyingLapStatus[]
  readonly deletedReason: readonly (string | null)[]
}

export interface QualifyingLapStatusSidecar {
  readonly contractVersion: 'v2'
  readonly fixtureId: string
  readonly drivers: Readonly<Record<string, QualifyingLapStatusRecord>>
  readonly events: readonly QualifyingLapStatusEvent[]
}

/** Descriptive aliases matching the producer's immutable model names. */
export type BrowserQualifyingLapStatusEvent = QualifyingLapStatusEvent
export type BrowserQualifyingLapStatusRecord = QualifyingLapStatusRecord
export type BrowserQualifyingLapStatusSidecar = QualifyingLapStatusSidecar

/**
 * Qualifying-safe track-status intervals. Restricted to yellow/red; SC/VSC
 * equivalents are intentionally not exposed in this revision.
 */
export type QualifyingTimelineIntervalKind = 'yellow' | 'red'

/** Half-open [startMs, endMs) track-status interval bounded by the artifact window. */
export interface QualifyingTimelineInterval {
  readonly kind: QualifyingTimelineIntervalKind
  readonly startMs: number
  readonly endMs: number
}

/**
 * Qualifying incident marker for track-map marker hiding (visibility only).
 * Effective when `timeMs <= replayTimeMs`; never carries race DNF semantics.
 */
export interface QualifyingIncidentMarker {
  readonly driverId: string
  readonly timeMs: number
  readonly source: 'race-control-car-event'
  readonly rawMessage: string
  readonly lapNumber?: number
}

/**
 * Optional qualifying-safe timeline/incident artifact. Modeled on
 * qualifyingLapStatus, not race-only timelineSummary: no DNF markers, no OUT,
 * no finish/position semantics. Absent artifact means no interval rendering and
 * no incident marker hiding (fail closed), never "no incident occurred".
 */
export interface QualifyingTimeline {
  readonly contractVersion: 'v2'
  readonly fixtureId: string
  readonly startMs: number
  readonly endMs: number
  readonly intervals: readonly QualifyingTimelineInterval[]
  readonly incidentMarkers: readonly QualifyingIncidentMarker[]
}

export interface QualifyingDriverColumns {
  readonly qualifyingPosition: readonly (number | null)[]
  readonly q1TimeMs: readonly (number | null)[]
  readonly q2TimeMs: readonly (number | null)[]
  readonly q3TimeMs: readonly (number | null)[]
  readonly bestLapNumber: readonly (number | null)[]
  readonly bestLapTimeMs: readonly (number | null)[]
}

export interface QualifyingSummary {
  readonly contractVersion: 'v2'
  readonly fixtureId: string
  readonly drivers: Readonly<Record<string, QualifyingDriverColumns>>
}

export interface WeatherSidecar {
  readonly contractVersion: 'v2'
  readonly fixtureId: string
  readonly timeMs: readonly number[]
  readonly airTempC: readonly (number | null)[]
  readonly humidityPct: readonly (number | null)[]
  readonly pressureMbar: readonly (number | null)[]
  readonly rainfall: readonly (boolean | null)[]
  readonly trackTempC: readonly (number | null)[]
  readonly windDirectionDeg: readonly (number | null)[]
  readonly windSpeedMps: readonly (number | null)[]
}

export interface ChunkReference extends ArtifactReference {
  readonly sequence: number
  readonly startMs: number
  readonly endMs: number
  readonly overlapWithPreviousMs: number
}

export interface DriverMetadata {
  readonly id: string
  readonly displayName: string
  readonly teamName: string
  readonly colorHex: string
  readonly carNumber: string
}

export interface SeasonMetadata {
  readonly year: number
}

export type TelemetryCapabilityState = 'available' | 'not-published'

export interface TelemetryCapabilities {
  readonly drs: TelemetryCapabilityState
  readonly overtakeMode: TelemetryCapabilityState
  readonly activeAero: TelemetryCapabilityState
  readonly ersReplacement: TelemetryCapabilityState
}

export interface LapStart {
  readonly lap: number
  readonly startMs: number
}

export interface ReplayManifest {
  readonly contractVersion: 'v2'
  readonly formatVersion: 'browser-delivery-v2'
  readonly sessionMode: SessionMode
  readonly fixtureId: string
  readonly fixtureName: string
  readonly schemas: Readonly<{
    readonly manifest: ManifestSchemaId
    readonly chunk: ChunkSchemaId
    readonly trackAssets: TrackAssetsSchemaId
    readonly timelineSummary?: TimelineSummarySchemaId
    readonly lapSectorSidecar?: LapSectorSchemaId
    readonly stintSummary?: StintSummarySchemaId
    readonly pitLossModel?: PitLossSchemaId
    readonly penaltySidecar?: PenaltySchemaId
    readonly qualifyingSummary?: QualifyingSummarySchemaId
    readonly qualifyingLapStatus?: QualifyingLapStatusSchemaId
    readonly qualifyingTimeline?: QualifyingTimelineSchemaId
    readonly weatherSidecar?: WeatherSidecarSchemaId
  }>
  readonly trackAssets: ArtifactReference
  readonly seasonMetadata?: SeasonMetadata
  readonly telemetryCapabilities?: TelemetryCapabilities
  readonly timelineSummary?: TimelineSummaryReference
  readonly lapSectorSidecar?: LapSectorSidecarReference
  readonly stintSummary?: StintSummaryReference
  readonly pitLossModel?: PitLossModelReference
  readonly penaltySidecar?: PenaltySidecarReference
  readonly qualifyingSummary?: QualifyingSummaryReference
  readonly qualifyingLapStatus?: QualifyingLapStatusReference
  readonly qualifyingTimeline?: QualifyingTimelineReference
  readonly weatherSidecar?: WeatherSidecarReference
  readonly chunks: readonly ChunkReference[]
  readonly drivers: readonly DriverMetadata[]
  readonly lapStarts?: readonly LapStart[]
  readonly description?: string
  readonly deliveryVersion?: string
  readonly sourceGenerationId?: string
  readonly sourceManifestSha256?: string
  readonly goldenSnapshots?: Readonly<{ readonly path: 'golden-snapshots.json' }>
  readonly createdAt?: string
}

export type TimelineIntervalKind = 'yellow' | 'sc' | 'red' | 'vsc'

export interface TimelineInterval {
  readonly kind: TimelineIntervalKind
  readonly startMs: number
  readonly endMs: number
}

export interface DnfMarker {
  readonly driverId: string
  readonly timeMs: number
}

export interface TimelineSummary {
  readonly contractVersion: 'v2'
  readonly fixtureId: string
  readonly startMs: number
  readonly endMs: number
  readonly intervals: readonly TimelineInterval[]
  readonly dnfMarkers: readonly DnfMarker[]
}

export interface TrackPoint { readonly x: number; readonly y: number }
export interface DrsZone { readonly startMeters: number; readonly endMeters: number }
export interface TrackAssets {
  readonly contractVersion: 'v2'
  readonly fixtureId: string
  readonly trackId: string
  readonly trackName: string
  readonly coordinateSpace: Readonly<{ readonly units: 'meters'; readonly origin: string }>
  readonly circuitLengthMeters: number
  readonly rotationDegrees: number
  readonly startFinish: Readonly<{ readonly center: TrackPoint; readonly inner: TrackPoint; readonly outer: TrackPoint }>
  readonly centerLine: readonly TrackPoint[]
  readonly innerBoundary: readonly TrackPoint[]
  readonly outerBoundary: readonly TrackPoint[]
  readonly distanceMarkersMeters?: readonly number[]
  readonly drsZones?: readonly DrsZone[]
}

/** Nullable derived columns preserve unavailable source evidence. */
export type DerivedDistanceMeters = number | null
export type DerivedGapToLeaderMs = number | null
export type DerivedPosition = number | null

export interface DriverColumns {
  readonly x: readonly (number | null)[]; readonly y: readonly (number | null)[]
  readonly trackDistanceMeters: readonly DerivedDistanceMeters[]; readonly speed: readonly (number | null)[]
  /** Optional when the source does not publish RPM samples. */
  readonly rpm?: readonly (number | null)[]
  readonly throttle: readonly (number | null)[]; readonly brake: readonly (number | null)[]
  readonly gapToLeaderMs: readonly DerivedGapToLeaderMs[]; readonly lap: readonly (number | null)[]
  readonly position: readonly DerivedPosition[]; readonly gear: readonly (number | null)[]
  readonly drs: readonly (number | null)[]; readonly tyreCompound: readonly (string | null)[]
  /** Optional when tyre-age samples are unavailable. */
  readonly tyreAge?: readonly (number | null)[]
  readonly status: readonly (string | null)[]; readonly isInPitLane: readonly (boolean | null)[]
  /** Optional when finish-state samples are unavailable. */
  readonly isFinished?: readonly (boolean | null)[]
}


export interface ReplayEvent {
  readonly sessionTimeMs: number; readonly eventType: string; readonly description: string
  readonly driverId?: string | null; readonly payload?: JsonObject
}

export interface NoOverlap {
  readonly kind: 'none'
  readonly previousChunkPath: null
  readonly range: null
  readonly authoritativeFromMs: null
}

export interface HandoffOverlap {
  readonly kind: 'handoff'
  readonly previousChunkPath: string
  readonly range: Readonly<{ readonly startMs: number; readonly endMs: number }>
  readonly authoritativeFromMs: number
}

export type ReplayOverlap = NoOverlap | HandoffOverlap

export interface ReplayChunk {
  readonly contractVersion: 'v2'; readonly fixtureId: string; readonly chunkId: string; readonly sequence: number
  readonly startMs: number; readonly endMs: number
  readonly overlap: ReplayOverlap
  readonly timeMs: readonly number[]; readonly authoritativeStartIndex: number
  readonly drivers: Readonly<Record<string, DriverColumns>>
  readonly leaderboardOrder: readonly (readonly string[] | null)[]
  readonly trackStatusCode: readonly (number | null)[]; readonly weatherState: readonly (string | null)[]
  readonly events: readonly ReplayEvent[]
}

export interface ReplayData {
  readonly pointer?: BrowserPointer
  readonly manifest: ReplayManifest
  readonly trackAssets: TrackAssets
  readonly seasonMetadata?: SeasonMetadata
  readonly telemetryCapabilities?: TelemetryCapabilities
  readonly timelineSummary?: TimelineSummary
  readonly lapSectorSidecar?: LapSectorSidecar
  readonly stintSummary?: StintSummary
  readonly pitLossModel?: PitLossModel
  readonly penaltySidecar?: PenaltySidecar
  readonly qualifyingSummary?: QualifyingSummary
  readonly qualifyingLapStatus?: QualifyingLapStatusSidecar
  readonly qualifyingTimeline?: QualifyingTimeline
  readonly weatherSidecar?: WeatherSidecar
  readonly chunks: readonly ReplayChunk[]
}

export interface ReplayIndex {
  readonly pointer?: BrowserPointer
  readonly manifest: ReplayManifest
  readonly trackAssets: TrackAssets
  readonly seasonMetadata?: SeasonMetadata
  readonly telemetryCapabilities?: TelemetryCapabilities
  readonly timelineSummary?: TimelineSummary
  readonly lapSectorSidecar?: LapSectorSidecar
  readonly stintSummary?: StintSummary
  readonly pitLossModel?: PitLossModel
  readonly penaltySidecar?: PenaltySidecar
  readonly qualifyingSummary?: QualifyingSummary
  readonly qualifyingLapStatus?: QualifyingLapStatusSidecar
  readonly qualifyingTimeline?: QualifyingTimeline
  readonly weatherSidecar?: WeatherSidecar
  readonly loadChunk: (sequence: number) => Promise<ReplayChunk>
  readonly loadAllChunks: (concurrency?: number) => Promise<readonly ReplayChunk[]>
}
