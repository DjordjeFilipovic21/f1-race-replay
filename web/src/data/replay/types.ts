export type JsonPrimitive = null | boolean | number | string
export interface JsonArray extends ReadonlyArray<JsonValue> {}
export interface JsonObject { readonly [key: string]: JsonValue }
export type JsonValue = JsonPrimitive | JsonArray | JsonObject

export interface ReplaySource {
  readonly read: (path: string) => Promise<Uint8Array>
}

export interface BrowserPointer {
  readonly formatVersion: 'browser-delivery-v1'
  readonly deliveryVersion: string
  readonly manifestPath: string
  readonly manifestSha256: string
}

export interface ArtifactReference {
  readonly path: string
  readonly schemaId: string
  readonly sha256?: string
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

export interface LapStart {
  readonly lap: number
  readonly startMs: number
}

export interface ReplayManifest {
  readonly contractVersion: 'v1'
  readonly fixtureId: string
  readonly fixtureName: string
  readonly schemas: Readonly<{ readonly manifest: string; readonly chunk: string; readonly trackAssets: string }>
  readonly trackAssets: ArtifactReference
  readonly chunks: readonly ChunkReference[]
  readonly drivers: readonly DriverMetadata[]
  readonly lapStarts?: readonly LapStart[]
  readonly description?: string
  readonly formatVersion?: 'browser-delivery-v1'
  readonly deliveryVersion?: string
  readonly sourceGenerationId?: string
  readonly sourceManifestSha256?: string
  readonly goldenSnapshots?: Readonly<{ readonly path: 'golden-snapshots.json' }>
  readonly createdAt?: string
}

export interface TrackPoint { readonly x: number; readonly y: number }
export interface DrsZone { readonly startMeters: number; readonly endMeters: number }
export interface TrackAssets {
  readonly contractVersion: 'v1'
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

/** Nullable derived columns preserve v1 null-only browser generations. */
export type DerivedDistanceMeters = number | null
export type DerivedGapToLeaderMs = number | null
export type DerivedPosition = number | null

export interface DriverColumns {
  readonly x: readonly (number | null)[]; readonly y: readonly (number | null)[]
  readonly trackDistanceMeters: readonly DerivedDistanceMeters[]; readonly speed: readonly (number | null)[]
  /** Optional in the frozen v1 payload; guards expose legacy absence as nulls. */
  readonly rpm?: readonly (number | null)[]
  readonly throttle: readonly (number | null)[]; readonly brake: readonly (number | null)[]
  readonly gapToLeaderMs: readonly DerivedGapToLeaderMs[]; readonly lap: readonly (number | null)[]
  readonly position: readonly DerivedPosition[]; readonly gear: readonly (number | null)[]
  readonly drs: readonly (number | null)[]; readonly tyreCompound: readonly (string | null)[]
  readonly status: readonly (string | null)[]; readonly isInPitLane: readonly (boolean | null)[]
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
  readonly contractVersion: 'v1'; readonly fixtureId: string; readonly chunkId: string; readonly sequence: number
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
  readonly chunks: readonly ReplayChunk[]
}

export interface ReplayIndex {
  readonly pointer?: BrowserPointer
  readonly manifest: ReplayManifest
  readonly trackAssets: TrackAssets
  readonly loadChunk: (sequence: number) => Promise<ReplayChunk>
  readonly loadAllChunks: (concurrency?: number) => Promise<readonly ReplayChunk[]>
}
