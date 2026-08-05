import type {
  ArtifactReference, BrowserPointer, ChunkReference, DnfMarker, DriverColumns, DriverMetadata,
  LapKind, LapSectorDriverColumns, LapSectorSidecar, LapSectorSidecarReference,
  PitLossEstimateSidecar, PitLossEstimateSidecarReference,
  PitLossEstimateStatus, PitLossEstimateTimeline, PitLossModel, PitLossModelReference,
  PenaltyIssuance, PenaltySidecar, PenaltySidecarReference, QualifyingIncidentMarker, QualifyingTimeline,
  QualifyingTimelineInterval, QualifyingTimelineIntervalKind, QualifyingTimelineReference, ReplayChunk, ReplayEvent, ReplayManifest, ReplayOverlap,
  SeasonMetadata, StintDriverColumns, StintSummary, StintSummaryReference, TelemetryCapabilities, TelemetryCapabilityState,
  TimelineInterval, TimelineIntervalKind, TimelineSummary,
  TimelineSummaryReference, TrackAssets, TrackPoint, SessionMode, QualifyingSessionMode,
  QualifyingDriverColumns, QualifyingLapStatusReference, QualifyingSummary, QualifyingSummaryReference,
  QualifyingLapStatus, QualifyingLapStatusEvent, QualifyingLapStatusEventStatus, QualifyingLapStatusRecord,
  QualifyingLapStatusSidecar, QualifyingPhase, QualifyingPhaseBoundary,
  WeatherSidecar, WeatherSidecarReference,
} from './types'
import { array, exact, finite, freeze, integer, jsonObject, nullable, object, string, type ObjectValue } from './value-guards'
import { assertSafeRelativePath } from './source'

export const MANIFEST_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v2:manifest'
export const CHUNK_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v2:chunk'
export const TRACK_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v2:track-assets'
export const TIMELINE_SUMMARY_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v2:timeline-summary'
export const BROWSER_LAP_SECTOR_SIDECAR_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v2:browser-lap-sector-sidecar'
export const STINT_SUMMARY_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v2:stint-summary'
export const PIT_LOSS_MODEL_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v2:pit-loss-model'
export const PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v2:pit-loss-estimate-sidecar'
export const PENALTY_SIDECAR_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v2:penalty-sidecar'
export const QUALIFYING_SUMMARY_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v2:qualifying-summary'
export const QUALIFYING_LAP_STATUS_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v2:browser-qualifying-lap-status'
export const QUALIFYING_TIMELINE_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v2:qualifying-timeline'
export const WEATHER_SIDECAR_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v2:weather-sidecar'
const REQUIRED_DRIVER_FIELDS = ['x', 'y', 'trackDistanceMeters', 'speed', 'throttle', 'brake', 'gapToLeaderMs', 'lap', 'position', 'gear', 'drs', 'tyreCompound', 'status', 'isInPitLane'] as const
const OPTIONAL_DRIVER_FIELDS = ['rpm', 'tyreAge', 'isFinished'] as const
const FIXTURE_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
const DRIVER_ID = /^[A-Z0-9]{2,4}$/
const SHA256 = /^[0-9a-f]{64}$/
const DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const TIMELINE_INTERVAL_KINDS = ['yellow', 'sc', 'red', 'vsc'] as const
const QUALIFYING_TIMELINE_INTERVAL_KINDS = ['yellow', 'red'] as const
const LAP_KINDS = ['flying', 'outlap', 'inlap', 'unknown'] as const
const TELEMETRY_CAPABILITY_STATES = ['available', 'not-published'] as const
const SESSION_MODES = ['race', 'practice', 'qualifying', 'sprint', 'sprint-qualifying', 'sprint-shootout', 'testing'] as const
const QUALIFYING_MODES = ['qualifying', 'sprint-qualifying', 'sprint-shootout'] as const
const QUALIFYING_PHASES = ['Q1', 'Q2', 'Q3'] as const
type QualifyingLapSectorColumns = LapSectorDriverColumns & {
  readonly qualifyingPhase: readonly (QualifyingPhase | null)[]
}

function artifact(value: unknown, label: string, extraFields: readonly string[] = [], digestRequired = false): ArtifactReference {
  const item = object(value, label)
  exact(item, ['path', 'schemaId', ...(digestRequired ? ['sha256'] : [])], [ ...(digestRequired ? [] : ['sha256']), ...extraFields], label)
  const sha256 = item.sha256
  if (sha256 !== undefined && (typeof sha256 !== 'string' || !SHA256.test(sha256))) throw new Error(`${label}.sha256 is invalid`)
  const path = string(item.path, `${label}.path`)
  try { assertSafeRelativePath(path) } catch (error) { throw new Error(`${label}.path is unsafe`, { cause: error }) }
  return freeze({ path, schemaId: string(item.schemaId, `${label}.schemaId`), ...(sha256 === undefined ? {} : { sha256 }) })
}

function requireCanonicalArtifactPath(item: ObjectValue, expected: string, message: string): void {
  if (item.path !== expected) throw new Error(message)
}

export function parsePointer(value: unknown): BrowserPointer {
  const item = object(value, 'pointer')
  exact(item, ['formatVersion', 'deliveryVersion', 'manifestPath', 'manifestSha256'], [], 'pointer')
  if (item.formatVersion !== 'browser-delivery-v2') throw new Error('Unsupported browser pointer format version')
  if (typeof item.manifestSha256 !== 'string' || !SHA256.test(item.manifestSha256)) throw new Error('pointer.manifestSha256 is invalid')
  const deliveryVersion = safeComponent(item.deliveryVersion, 'pointer.deliveryVersion')
  const manifestPath = string(item.manifestPath, 'pointer.manifestPath')
  try { assertSafeRelativePath(manifestPath) } catch (error) { throw new Error('pointer.manifestPath is unsafe', { cause: error }) }
  if (manifestPath !== `generations/${deliveryVersion}/manifest.json`) throw new Error('pointer.manifestPath disagrees with deliveryVersion')
  return freeze({ formatVersion: 'browser-delivery-v2', deliveryVersion, manifestPath, manifestSha256: item.manifestSha256 })
}

export function parseManifest(value: unknown): ReplayManifest {
  const item = object(value, 'manifest')
  exact(item, ['contractVersion', 'formatVersion', 'sessionMode', 'fixtureId', 'fixtureName', 'schemas', 'trackAssets', 'chunks', 'drivers'], ['description', 'deliveryVersion', 'sourceGenerationId', 'sourceManifestSha256', 'goldenSnapshots', 'createdAt', 'lapStarts', 'seasonMetadata', 'telemetryCapabilities', 'timelineSummary', 'lapSectorSidecar', 'stintSummary', 'pitLossModel', 'pitLossEstimateSidecar', 'penaltySidecar', 'qualifyingSummary', 'qualifyingLapStatus', 'qualifyingTimeline', 'weatherSidecar'], 'manifest')
  if (item.contractVersion !== 'v2') throw new Error('manifest must be contract version v2')
  if (item.formatVersion !== 'browser-delivery-v2') throw new Error('manifest format version is unsupported')
  if (!SESSION_MODES.includes(item.sessionMode as SessionMode)) throw new Error('manifest.sessionMode is invalid')
  const schemas = object(item.schemas, 'manifest.schemas')
  exact(schemas, ['manifest', 'chunk', 'trackAssets'], ['timelineSummary', 'lapSectorSidecar', 'stintSummary', 'pitLossModel', 'pitLossEstimateSidecar', 'penaltySidecar', 'qualifyingSummary', 'qualifyingLapStatus', 'qualifyingTimeline', 'weatherSidecar'], 'manifest.schemas')
  if (schemas.manifest !== MANIFEST_SCHEMA || schemas.chunk !== CHUNK_SCHEMA || schemas.trackAssets !== TRACK_SCHEMA) throw new Error('manifest schema identities are unsupported')
  const trackAssets = artifact(item.trackAssets, 'manifest.trackAssets')
  if (trackAssets.path !== 'track-assets.json' || trackAssets.schemaId !== TRACK_SCHEMA) throw new Error('track asset schema identity or path is unsupported')
  const seasonMetadata = item.seasonMetadata === undefined ? undefined : parseSeasonMetadata(item.seasonMetadata)
  const telemetryCapabilities = item.telemetryCapabilities === undefined ? undefined : parseTelemetryCapabilities(item.telemetryCapabilities)
  const timelineSummary = item.timelineSummary === undefined ? undefined : parseTimelineSummaryReference(item.timelineSummary)
  const lapSectorSidecar = item.lapSectorSidecar === undefined ? undefined : parseLapSectorSidecarReference(item.lapSectorSidecar)
  const stintSummary = item.stintSummary === undefined ? undefined : parseStintSummaryReference(item.stintSummary)
  const pitLossModel = item.pitLossModel === undefined ? undefined : parsePitLossModelReference(item.pitLossModel)
  const pitLossEstimateSidecar = item.pitLossEstimateSidecar === undefined ? undefined : parsePitLossEstimateSidecarReference(item.pitLossEstimateSidecar)
  const penaltySidecar = item.penaltySidecar === undefined ? undefined : parsePenaltySidecarReference(item.penaltySidecar)
  const qualifyingSummary = item.qualifyingSummary === undefined ? undefined : parseQualifyingSummaryReference(item.qualifyingSummary)
  const qualifyingLapStatus = item.qualifyingLapStatus === undefined ? undefined : parseQualifyingLapStatusReference(item.qualifyingLapStatus)
  const qualifyingTimeline = item.qualifyingTimeline === undefined ? undefined : parseQualifyingTimelineReference(item.qualifyingTimeline)
  const weatherSidecar = item.weatherSidecar === undefined ? undefined : parseWeatherSidecarReference(item.weatherSidecar)
  const chunks = array(item.chunks, 'manifest.chunks').map(parseChunkReference)
  const drivers = array(item.drivers, 'manifest.drivers').map(parseDriver)
  const lapStarts = item.lapStarts === undefined ? undefined : array(item.lapStarts, 'manifest.lapStarts').map(parseLapStart)
  if (!chunks.length || !drivers.length || new Set(drivers.map(({ id }) => id)).size !== drivers.length) throw new Error('manifest requires chunks and unique drivers')
  chunks.forEach((chunk, index) => {
    if (chunk.schemaId !== CHUNK_SCHEMA || chunk.sequence !== index + 1 || (index === 0 && chunk.overlapWithPreviousMs !== 0) || (index > 0 && chunks[index - 1].endMs !== chunk.startMs)) throw new Error('manifest chunk references are invalid')
  })
  const fixtureId = string(item.fixtureId, 'manifest.fixtureId'); if (!FIXTURE_ID.test(fixtureId)) throw new Error('manifest.fixtureId is invalid')
  if (item.description !== undefined && typeof item.description !== 'string') throw new Error('manifest.description must be a string')
  if (item.deliveryVersion !== undefined) string(item.deliveryVersion, 'manifest.deliveryVersion')
  if (item.sourceGenerationId !== undefined) string(item.sourceGenerationId, 'manifest.sourceGenerationId')
  if (item.sourceManifestSha256 !== undefined && (typeof item.sourceManifestSha256 !== 'string' || !SHA256.test(item.sourceManifestSha256))) throw new Error('manifest.sourceManifestSha256 is invalid')
  if (item.createdAt !== undefined && (typeof item.createdAt !== 'string' || !DATE_TIME.test(item.createdAt) || Number.isNaN(Date.parse(item.createdAt)))) throw new Error('manifest.createdAt is invalid')
  if (lapStarts && lapStarts.some((marker, index) => index > 0 && (marker.lap <= lapStarts[index - 1].lap || marker.startMs < lapStarts[index - 1].startMs))) throw new Error('manifest.lapStarts must be ordered')
  if (lapStarts && lapStarts.some(({ startMs }) => startMs < chunks[0].startMs || startMs >= chunks[chunks.length - 1].endMs)) throw new Error('manifest.lapStarts must be within replay bounds')
  const golden = item.goldenSnapshots === undefined ? undefined : object(item.goldenSnapshots, 'manifest.goldenSnapshots')
  if (golden) { exact(golden, ['path'], [], 'manifest.goldenSnapshots'); if (golden.path !== 'golden-snapshots.json') throw new Error('golden snapshot path is unsupported') }
  validateModeGating(item.sessionMode as SessionMode, timelineSummary, pitLossModel, pitLossEstimateSidecar, qualifyingSummary, qualifyingLapStatus, qualifyingTimeline)
  validateSchemaRegistry(schemas, { timelineSummary, lapSectorSidecar, stintSummary, pitLossModel, pitLossEstimateSidecar, penaltySidecar, qualifyingSummary, qualifyingLapStatus, qualifyingTimeline, weatherSidecar })
  const deliveryVersion = item.deliveryVersion === undefined ? undefined : safeComponent(item.deliveryVersion, 'manifest.deliveryVersion')
  const sourceGenerationId = item.sourceGenerationId === undefined ? undefined : safeComponent(item.sourceGenerationId, 'manifest.sourceGenerationId')
  return freeze({ contractVersion: 'v2', formatVersion: 'browser-delivery-v2', sessionMode: item.sessionMode as SessionMode, fixtureId, fixtureName: string(item.fixtureName, 'manifest.fixtureName'), schemas: freeze({ ...schemas } as ReplayManifest['schemas']), trackAssets, ...(seasonMetadata === undefined ? {} : { seasonMetadata }), ...(telemetryCapabilities === undefined ? {} : { telemetryCapabilities }), ...(timelineSummary === undefined ? {} : { timelineSummary }), ...(lapSectorSidecar === undefined ? {} : { lapSectorSidecar }), ...(stintSummary === undefined ? {} : { stintSummary }), ...(pitLossModel === undefined ? {} : { pitLossModel }), ...(pitLossEstimateSidecar === undefined ? {} : { pitLossEstimateSidecar }), ...(penaltySidecar === undefined ? {} : { penaltySidecar }), ...(qualifyingSummary === undefined ? {} : { qualifyingSummary }), ...(qualifyingLapStatus === undefined ? {} : { qualifyingLapStatus }), ...(qualifyingTimeline === undefined ? {} : { qualifyingTimeline }), ...(weatherSidecar === undefined ? {} : { weatherSidecar }), chunks, drivers, ...(lapStarts === undefined ? {} : { lapStarts: freeze(lapStarts) }), ...(item.description === undefined ? {} : { description: item.description as string }), ...(deliveryVersion === undefined ? {} : { deliveryVersion }), ...(sourceGenerationId === undefined ? {} : { sourceGenerationId }), ...(item.sourceManifestSha256 === undefined ? {} : { sourceManifestSha256: item.sourceManifestSha256 as string }), ...(golden ? { goldenSnapshots: freeze({ path: 'golden-snapshots.json' as const }) } : {}), ...(item.createdAt === undefined ? {} : { createdAt: item.createdAt as string }) })
}

function safeComponent(value: unknown, label: string): string {
  const component = string(value, label)
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(component)) throw new Error(`${label} is invalid`)
  return component
}

function validateModeGating(
  mode: SessionMode,
  timelineSummary: TimelineSummaryReference | undefined,
  pitLossModel: PitLossModelReference | undefined,
  pitLossEstimateSidecar: PitLossEstimateSidecarReference | undefined,
  qualifyingSummary: QualifyingSummaryReference | undefined,
  qualifyingLapStatus: QualifyingLapStatusReference | undefined,
  qualifyingTimeline: QualifyingTimelineReference | undefined,
): void {
  const qualifying = QUALIFYING_MODES.includes(mode as QualifyingSessionMode)
  const raceOnly = mode === 'practice' || qualifying || mode === 'testing'
  if (raceOnly && (timelineSummary !== undefined || pitLossModel !== undefined || pitLossEstimateSidecar !== undefined)) {
    throw new Error('race-only browser sidecars are invalid for this session mode')
  }
  if (!qualifying && (qualifyingSummary !== undefined || qualifyingLapStatus !== undefined || qualifyingTimeline !== undefined)) {
    throw new Error('qualifying artifacts are valid only for qualifying-like modes')
  }
}

function validateSchemaRegistry(
  schemas: ObjectValue,
  references: Readonly<Record<string, ArtifactReference | undefined>>,
): void {
  const expected: Readonly<Record<string, string>> = {
    timelineSummary: TIMELINE_SUMMARY_SCHEMA,
    lapSectorSidecar: BROWSER_LAP_SECTOR_SIDECAR_SCHEMA,
    stintSummary: STINT_SUMMARY_SCHEMA,
    pitLossModel: PIT_LOSS_MODEL_SCHEMA,
    pitLossEstimateSidecar: PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA,
    penaltySidecar: PENALTY_SIDECAR_SCHEMA,
    qualifyingSummary: QUALIFYING_SUMMARY_SCHEMA,
    qualifyingLapStatus: QUALIFYING_LAP_STATUS_SCHEMA,
    qualifyingTimeline: QUALIFYING_TIMELINE_SCHEMA,
    weatherSidecar: WEATHER_SIDECAR_SCHEMA,
  }
  for (const [field, schemaId] of Object.entries(expected)) {
    const reference = references[field]
    const registered = schemas[field]
    if (registered !== undefined && registered !== schemaId) throw new Error(`manifest.schemas.${field} identity is unsupported`)
    if (reference !== undefined && reference.schemaId !== schemaId) throw new Error(`manifest.${field} schema identity is unsupported`)
  }
}

export function parseSeasonMetadata(value: unknown): SeasonMetadata {
  const item = object(value, 'manifest.seasonMetadata')
  exact(item, ['year'], [], 'manifest.seasonMetadata')
  return freeze({ year: integer(item.year, 'manifest.seasonMetadata.year', 1, 9999) })
}

export function parseTelemetryCapabilities(value: unknown): TelemetryCapabilities {
  const item = object(value, 'manifest.telemetryCapabilities')
  const fields = ['drs', 'overtakeMode', 'activeAero', 'ersReplacement'] as const
  exact(item, fields, [], 'manifest.telemetryCapabilities')
  const capability = (field: typeof fields[number]): TelemetryCapabilityState => {
    const state = item[field]
    if (!TELEMETRY_CAPABILITY_STATES.includes(state as TelemetryCapabilityState)) {
      throw new Error(`manifest.telemetryCapabilities.${field} is invalid`)
    }
    return state as TelemetryCapabilityState
  }
  return freeze({ drs: capability('drs'), overtakeMode: capability('overtakeMode'), activeAero: capability('activeAero'), ersReplacement: capability('ersReplacement') })
}

export function parseTimelineSummaryReference(value: unknown): TimelineSummaryReference {
  const item = object(value, 'manifest.timelineSummary')
  artifact(item, 'manifest.timelineSummary', [], true)
  requireCanonicalArtifactPath(item, 'timeline-summary.json', 'timeline summary path is unsupported')
  if (item.schemaId !== TIMELINE_SUMMARY_SCHEMA) throw new Error('timeline summary schema identity is unsupported')
  const sha256 = item.sha256
  if (typeof sha256 !== 'string' || !SHA256.test(sha256)) throw new Error('manifest.timelineSummary.sha256 is invalid')
  return freeze({ path: 'timeline-summary.json', schemaId: TIMELINE_SUMMARY_SCHEMA, sha256 })
}

export function parseLapSectorSidecarReference(value: unknown): LapSectorSidecarReference {
  const item = object(value, 'manifest.lapSectorSidecar')
  artifact(item, 'manifest.lapSectorSidecar', [], true)
  requireCanonicalArtifactPath(item, 'lap-sector-sidecar.json', 'lap sector sidecar path is unsupported')
  if (item.schemaId !== BROWSER_LAP_SECTOR_SIDECAR_SCHEMA) throw new Error('lap sector sidecar schema identity is unsupported')
  const sha256 = item.sha256
  if (typeof sha256 !== 'string' || !SHA256.test(sha256)) throw new Error('manifest.lapSectorSidecar.sha256 is invalid')
  return freeze({ path: 'lap-sector-sidecar.json', schemaId: BROWSER_LAP_SECTOR_SIDECAR_SCHEMA, sha256 })
}

export function parseStintSummaryReference(value: unknown): StintSummaryReference {
  const item = object(value, 'manifest.stintSummary')
  artifact(item, 'manifest.stintSummary', [], true)
  requireCanonicalArtifactPath(item, 'stint-summary.json', 'stint summary path is unsupported')
  if (item.schemaId !== STINT_SUMMARY_SCHEMA) throw new Error('stint summary schema identity is unsupported')
  const sha256 = item.sha256
  if (typeof sha256 !== 'string' || !SHA256.test(sha256)) throw new Error('manifest.stintSummary.sha256 is invalid')
  return freeze({ path: 'stint-summary.json', schemaId: STINT_SUMMARY_SCHEMA, sha256 })
}

export function parsePitLossModelReference(value: unknown): PitLossModelReference {
  const item = object(value, 'manifest.pitLossModel')
  artifact(item, 'manifest.pitLossModel', [], true)
  requireCanonicalArtifactPath(item, 'pit-loss-model.json', 'pit loss model path is unsupported')
  if (item.schemaId !== PIT_LOSS_MODEL_SCHEMA) throw new Error('pit loss model schema identity is unsupported')
  const sha256 = item.sha256
  if (typeof sha256 !== 'string' || !SHA256.test(sha256)) throw new Error('manifest.pitLossModel.sha256 is invalid')
  return freeze({ path: 'pit-loss-model.json', schemaId: PIT_LOSS_MODEL_SCHEMA, sha256 })
}

export function parsePitLossEstimateSidecarReference(value: unknown): PitLossEstimateSidecarReference {
  const item = object(value, 'manifest.pitLossEstimateSidecar')
  exact(item, ['path', 'schemaId', 'sha256'], [], 'manifest.pitLossEstimateSidecar')
  if (item.path !== 'pit-loss-estimate-sidecar.json') throw new Error('pit loss estimate sidecar path is unsupported')
  if (item.schemaId !== PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA) throw new Error('pit loss estimate sidecar schema identity is unsupported')
  const sha256 = item.sha256
  if (typeof sha256 !== 'string' || !SHA256.test(sha256)) throw new Error('manifest.pitLossEstimateSidecar.sha256 is invalid')
  return freeze({ path: 'pit-loss-estimate-sidecar.json', schemaId: PIT_LOSS_ESTIMATE_SIDECAR_SCHEMA, sha256 })
}

export function parsePenaltySidecarReference(value: unknown): PenaltySidecarReference {
  const item = object(value, 'manifest.penaltySidecar')
  artifact(item, 'manifest.penaltySidecar', [], true)
  requireCanonicalArtifactPath(item, 'penalty-sidecar.json', 'penalty sidecar path is unsupported')
  if (item.schemaId !== PENALTY_SIDECAR_SCHEMA) throw new Error('penalty sidecar schema identity is unsupported')
  const sha256 = item.sha256
  if (typeof sha256 !== 'string' || !SHA256.test(sha256)) throw new Error('manifest.penaltySidecar.sha256 is invalid')
  return freeze({ path: 'penalty-sidecar.json', schemaId: PENALTY_SIDECAR_SCHEMA, sha256 })
}

export function parseQualifyingSummaryReference(value: unknown): QualifyingSummaryReference {
  const item = object(value, 'manifest.qualifyingSummary')
  artifact(item, 'manifest.qualifyingSummary', [], true)
  requireCanonicalArtifactPath(item, 'qualifying-summary.json', 'qualifying summary path is unsupported')
  if (item.schemaId !== QUALIFYING_SUMMARY_SCHEMA) throw new Error('qualifying summary schema identity is unsupported')
  return freeze({ path: 'qualifying-summary.json', schemaId: QUALIFYING_SUMMARY_SCHEMA, sha256: item.sha256 as string })
}

export function parseQualifyingLapStatusReference(value: unknown): QualifyingLapStatusReference {
  const item = object(value, 'manifest.qualifyingLapStatus')
  artifact(item, 'manifest.qualifyingLapStatus', [], true)
  requireCanonicalArtifactPath(item, 'qualifying-lap-status.json', 'qualifying lap status path is unsupported')
  if (item.schemaId !== QUALIFYING_LAP_STATUS_SCHEMA) throw new Error('qualifying lap status schema identity is unsupported')
  return freeze({ path: 'qualifying-lap-status.json', schemaId: QUALIFYING_LAP_STATUS_SCHEMA, sha256: item.sha256 as string })
}

export function parseQualifyingTimelineReference(value: unknown): QualifyingTimelineReference {
  const item = object(value, 'manifest.qualifyingTimeline')
  artifact(item, 'manifest.qualifyingTimeline', [], true)
  requireCanonicalArtifactPath(item, 'qualifying-timeline.json', 'qualifying timeline path is unsupported')
  if (item.schemaId !== QUALIFYING_TIMELINE_SCHEMA) throw new Error('qualifying timeline schema identity is unsupported')
  return freeze({ path: 'qualifying-timeline.json', schemaId: QUALIFYING_TIMELINE_SCHEMA, sha256: item.sha256 as string })
}

export function parseWeatherSidecarReference(value: unknown): WeatherSidecarReference {
  const item = object(value, 'manifest.weatherSidecar')
  exact(item, ['path', 'schemaId', 'sha256'], [], 'manifest.weatherSidecar')
  if (item.path !== 'weather-sidecar.json') throw new Error('weather sidecar path is unsupported')
  if (item.schemaId !== WEATHER_SIDECAR_SCHEMA) throw new Error('weather sidecar schema identity is unsupported')
  const sha256 = item.sha256
  if (typeof sha256 !== 'string' || !SHA256.test(sha256)) throw new Error('manifest.weatherSidecar.sha256 is invalid')
  return freeze({ path: 'weather-sidecar.json', schemaId: WEATHER_SIDECAR_SCHEMA, sha256 })
}

export function parseLapSectorSidecar(value: unknown): LapSectorSidecar {
  const item = object(value, 'lap sector sidecar')
  exact(item, ['contractVersion', 'fixtureId', 'phaseBoundaries', 'drivers'], [], 'lap sector sidecar')
  if (item.contractVersion !== 'v2') throw new Error('lap sector sidecar must be contract version v2')
  const fixtureId = parseFixtureId(item.fixtureId, 'lap sector sidecar fixture id')
  const phaseBoundaries = array(item.phaseBoundaries, 'lap sector sidecar phaseBoundaries').map(parseQualifyingPhaseBoundary)
  const drivers = parseDrivers<QualifyingLapSectorColumns>(item.drivers, 'lap sector sidecar drivers', (columns, label) => parseLapSectorColumns(columns, label, true) as QualifyingLapSectorColumns)
  validateQualifyingPhaseBoundaries(phaseBoundaries, drivers)
  return freeze({ contractVersion: 'v2', fixtureId, phaseBoundaries: freeze(phaseBoundaries), drivers })
}

/** Enforces source-derived phase evidence only when a bundle is qualifying-like. */
export function validateQualifyingLikeLapSectorSidecar(sidecar: LapSectorSidecar): void {
  if (sidecar.phaseBoundaries.length === 0) throw new Error('qualifying-like lap sector sidecar requires at least one phase boundary')
  const hasAssignedPhaseLap = Object.values(sidecar.drivers).some((driver) => driver.qualifyingPhase.some((phase) => phase !== null))
  if (!hasAssignedPhaseLap) throw new Error('qualifying-like lap sector sidecar requires at least one assigned phase lap')
}

export function parseStintSummary(value: unknown): StintSummary {
  const item = object(value, 'stint summary')
  exact(item, ['contractVersion', 'fixtureId', 'drivers'], [], 'stint summary')
  if (item.contractVersion !== 'v2') throw new Error('stint summary must be contract version v2')
  const fixtureId = parseFixtureId(item.fixtureId, 'stint summary fixture id')
  const drivers = parseDrivers(item.drivers, 'stint summary drivers', parseStintColumns)
  return freeze({ contractVersion: 'v2', fixtureId, drivers })
}

export function parsePitLossModel(value: unknown): PitLossModel {
  const item = object(value, 'pit loss model')
  exact(item, ['contractVersion', 'fixtureId', 'method', 'baselineMs', 'priorWeight', 'timeMs', 'estimatedLossMs', 'observedSampleCount'], [], 'pit loss model')
  if (item.contractVersion !== 'v2') throw new Error('pit loss model must be contract version v2')
  const fixtureId = parseFixtureId(item.fixtureId, 'pit loss model fixture id')
  if (item.method !== 'global-prior-weighted-mean-v1') throw new Error('pit loss model method is invalid')
  const baselineMs = integer(item.baselineMs, 'pit loss model baseline', 1)
  const priorWeight = integer(item.priorWeight, 'pit loss model prior weight', 1)
  const timeMs = parseStandaloneColumn(item.timeMs, 'pit loss model timeMs', (entry) => integer(entry, 'pit loss model timeMs value'))
  const estimatedLossMs = parseStandaloneColumn(item.estimatedLossMs, 'pit loss model estimatedLossMs', (entry) => integer(entry, 'pit loss model estimatedLossMs value'))
  const observedSampleCount = parseStandaloneColumn(item.observedSampleCount, 'pit loss model observedSampleCount', (entry) => integer(entry, 'pit loss model observedSampleCount value'))
  if (!timeMs.length) throw new Error('pit loss model arrays must be non-empty')
  if (estimatedLossMs.length !== timeMs.length || observedSampleCount.length !== timeMs.length) throw new Error('pit loss model arrays must be aligned')
  assertStrictlyIncreasing(timeMs, 'pit loss model timeMs must be strictly increasing')
  if (estimatedLossMs[0] !== baselineMs) throw new Error('pit loss model first estimatedLossMs must equal baselineMs')
  if (observedSampleCount[0] !== 0) throw new Error('pit loss model first observedSampleCount must be zero')
  assertStrictlyIncreasing(observedSampleCount, 'pit loss model observedSampleCount must strictly increase')
  return freeze({ contractVersion: 'v2', fixtureId, method: 'global-prior-weighted-mean-v1', baselineMs, priorWeight, timeMs, estimatedLossMs, observedSampleCount })
}

export function parsePitLossEstimateSidecar(value: unknown): PitLossEstimateSidecar {
  const item = object(value, 'pit loss estimate sidecar')
  if (item.contractVersion !== 'v2') throw new Error('pit loss estimate sidecar must be contract version v2')
  const fixtureId = parseFixtureId(item.fixtureId, 'pit loss estimate sidecar fixture id')
  const trackId = parseFixtureId(item.trackId, 'pit loss estimate sidecar track id')
  if (item.method === 'track-status-median-v1') {
    exact(item, ['contractVersion', 'fixtureId', 'trackId', 'method', 'race'], ['safetyCar', 'virtualSafetyCar'], 'pit loss estimate sidecar')
    const race = parsePitLossEstimateTimeline(item.race, 'pit loss estimate sidecar race')
    if (race.observedSampleCount === undefined) throw new Error('pit loss estimate sidecar race must contain observedSampleCount')
    const safetyCar = item.safetyCar === undefined ? undefined : parsePitLossStatusEstimate(item.safetyCar, 'pit loss estimate sidecar safetyCar')
    const virtualSafetyCar = item.virtualSafetyCar === undefined ? undefined : parsePitLossStatusEstimate(item.virtualSafetyCar, 'pit loss estimate sidecar virtualSafetyCar')
    return freeze({
      contractVersion: 'v2',
      fixtureId,
      trackId,
      method: 'track-status-median-v1',
      race,
      ...(safetyCar === undefined ? {} : { safetyCar }),
      ...(virtualSafetyCar === undefined ? {} : { virtualSafetyCar }),
    })
  }
  if (item.method !== 'curated-track-baseline-v1') throw new Error('pit loss estimate sidecar method is invalid')
  // Catalog-only audit metadata (catalogVersion, sourceStatus, provenance,
  // evidenceCount, confidence, statusMetadata, derivation) is rejected rather
  // than read-and-discarded: the v2 public sidecar must never carry it.
  exact(
    item,
    ['contractVersion', 'fixtureId', 'trackId', 'method', 'race', 'safetyCar', 'virtualSafetyCar'],
    [],
    'pit loss estimate sidecar',
  )
  const race = parsePitLossEstimateTimeline(item.race, 'pit loss estimate sidecar race', true)
  // Curated status values must be available replay-start timelines; the
  // unavailable status is a legacy-only shape and fails closed here.
  const safetyCar = parseCuratedStatusTimeline(item.safetyCar, 'pit loss estimate sidecar safetyCar')
  const virtualSafetyCar = parseCuratedStatusTimeline(item.virtualSafetyCar, 'pit loss estimate sidecar virtualSafetyCar')
  validateCuratedBaselineOrdering(race, safetyCar, virtualSafetyCar)
  return freeze({ contractVersion: 'v2', fixtureId, trackId, method: 'curated-track-baseline-v1', race, safetyCar, virtualSafetyCar })
}

function parsePitLossEstimateTimeline(value: unknown, label: string, curated = false): PitLossEstimateTimeline {
  const item = object(value, label)
  // Curated timelines never carry observedSampleCount: current-race observation
  // counts must not be fabricated for immutable catalog values. The JSON
  // schema and the pipeline model reject the field there; the guard mirrors
  // that contract by forbidding it instead of silently consuming it.
  exact(item, curated ? ['timeMs', 'estimatedLossMs'] : ['timeMs', 'estimatedLossMs', 'observedSampleCount'], [], label)
  const timeMs = parseStandaloneColumn(item.timeMs, `${label}.timeMs`, (entry) => integer(entry, `${label}.timeMs value`))
  const estimatedLossMs = parseStandaloneColumn(item.estimatedLossMs, `${label}.estimatedLossMs`, (entry) => integer(entry, `${label}.estimatedLossMs value`))
  const observedSampleCount = item.observedSampleCount === undefined
    ? undefined
    : parseStandaloneColumn(item.observedSampleCount, `${label}.observedSampleCount`, (entry) => integer(entry, `${label}.observedSampleCount value`))
  if (!timeMs.length) throw new Error(`${label} arrays must be non-empty`)
  if (estimatedLossMs.length !== timeMs.length || (observedSampleCount !== undefined && observedSampleCount.length !== timeMs.length)) throw new Error(`${label} arrays must be aligned`)
  assertStrictlyIncreasing(timeMs, `${label}.timeMs must be strictly increasing`)
  if (curated && timeMs.length !== 1) throw new Error(`${label} must contain one replay-start value`)
  if (!curated && observedSampleCount === undefined) throw new Error(`${label}.observedSampleCount is required`)
  if (observedSampleCount !== undefined && observedSampleCount.length > 1) {
    assertStrictlyIncreasing(observedSampleCount, `${label}.observedSampleCount must strictly increase`)
    if (!curated && observedSampleCount[0] !== 0) throw new Error(`${label}.observedSampleCount must start at zero`)
  }
  return freeze({ timeMs, estimatedLossMs, ...(observedSampleCount === undefined ? {} : { observedSampleCount }) })
}

function parsePitLossStatusEstimate(value: unknown, label: string, curated = false): PitLossEstimateStatus {
  const item = object(value, label)
  if ('status' in item) {
    exact(item, ['status'], [], label)
    if (item.status !== 'unavailable') throw new Error(`${label}.status is invalid`)
    return freeze({ status: 'unavailable' as const })
  }
  return parsePitLossEstimateTimeline(value, label, curated)
}

function parseCuratedStatusTimeline(value: unknown, label: string): PitLossEstimateTimeline {
  const item = object(value, label)
  if ('status' in item) throw new Error(`${label}.status is unavailable and is not valid for curated sidecars`)
  return parsePitLossEstimateTimeline(value, label, true)
}

function validateCuratedBaselineOrdering(
  race: PitLossEstimateTimeline,
  safetyCar: PitLossEstimateTimeline,
  virtualSafetyCar: PitLossEstimateTimeline,
): void {
  const greenMs = race.estimatedLossMs[0]
  const safetyCarMs = safetyCar.estimatedLossMs[0]
  const virtualSafetyCarMs = virtualSafetyCar.estimatedLossMs[0]
  if (safetyCarMs > virtualSafetyCarMs || virtualSafetyCarMs > greenMs) throw new Error('pit loss estimate sidecar baselines must satisfy SC <= VSC <= Green')
}

export function parsePenaltySidecar(value: unknown): PenaltySidecar {
  const item = object(value, 'penalty sidecar')
  exact(item, ['contractVersion', 'fixtureId', 'penaltyIssuances'], [], 'penalty sidecar')
  if (item.contractVersion !== 'v2') throw new Error('penalty sidecar must be contract version v2')
  const fixtureId = parseFixtureId(item.fixtureId, 'penalty sidecar fixture id')
  const penaltyIssuances = array(item.penaltyIssuances, 'penalty sidecar penaltyIssuances').map(parsePenaltyIssuance)
  return freeze({ contractVersion: 'v2', fixtureId, penaltyIssuances: freeze(penaltyIssuances) })
}

export function parseQualifyingSummary(value: unknown): QualifyingSummary {
  const item = object(value, 'qualifying summary')
  exact(item, ['contractVersion', 'fixtureId', 'drivers'], [], 'qualifying summary')
  if (item.contractVersion !== 'v2') throw new Error('qualifying summary must be contract version v2')
  const fixtureId = parseFixtureId(item.fixtureId, 'qualifying summary fixture id')
  const drivers = parseDrivers(item.drivers, 'qualifying summary drivers', parseQualifyingColumns)
  return freeze({ contractVersion: 'v2', fixtureId, drivers })
}

export function parseQualifyingLapStatus(value: unknown): QualifyingLapStatusSidecar {
  const item = object(value, 'qualifying lap status')
  exact(item, ['contractVersion', 'fixtureId', 'drivers', 'events'], [], 'qualifying lap status')
  if (item.contractVersion !== 'v2') throw new Error('qualifying lap status must be contract version v2')
  const fixtureId = parseFixtureId(item.fixtureId, 'qualifying lap status fixture id')
  const drivers = parseDrivers(item.drivers, 'qualifying lap status drivers', parseQualifyingLapStatusRecord)
  const events = array(item.events, 'qualifying lap status events').map(parseQualifyingLapStatusEvent)
  const orderedEvents = sortQualifyingLapStatusEvents(events)
  validateQualifyingLapStatusEvents(drivers, orderedEvents)
  return freeze({ contractVersion: 'v2', fixtureId, drivers, events: freeze(orderedEvents) })
}

function parseQualifyingLapStatusRecord(value: unknown, label: string): QualifyingLapStatusRecord {
  const item = object(value, label)
  const fields = ['lapNumber', 'lapStartMs', 'lapEndMs', 'status', 'deletedReason'] as const
  exact(item, fields, [], label)
  const lapNumber = parseStandaloneColumn(item.lapNumber, `${label}.lapNumber`, (entry) => integer(entry, `${label}.lapNumber`, 1))
  const lapStartMs = parseColumn(item.lapStartMs, lapNumber.length, `${label}.lapStartMs`, (entry) => integer(entry, `${label}.lapStartMs`))
  const lapEndMs = parseColumn(item.lapEndMs, lapNumber.length, `${label}.lapEndMs`, (entry) => integer(entry, `${label}.lapEndMs`))
  const status = parseColumn(item.status, lapNumber.length, `${label}.status`, (entry) => parseQualifyingLapStatusValue(entry, `${label}.status`))
  const deletedReason = parseColumn(item.deletedReason, lapNumber.length, `${label}.deletedReason`, (entry) => nullable(entry, (inner) => parseNullableReason(inner, `${label}.deletedReason`)))
  assertStrictlyIncreasing(lapNumber, `${label}.lapNumber must be strictly increasing`)
  if (lapEndMs.some((endMs, index) => endMs <= lapStartMs[index])) throw new Error(`${label} lap end times must follow lap start times`)
  if (status.some((value, index) => value === 'valid' && deletedReason[index] !== null)) throw new Error(`${label} valid laps must not contain a deleted reason`)
  return freeze({ lapNumber, lapStartMs, lapEndMs, status, deletedReason })
}

function parseQualifyingLapStatusEvent(value: unknown, index: number): QualifyingLapStatusEvent {
  const label = `qualifying lap status events[${index}]`
  const item = object(value, label)
  exact(item, ['driverId', 'lapNumber', 'eventTimeMs', 'status', 'reason', 'rawMessage'], [], label)
  const status = item.status
  if (status !== 'deleted' && status !== 'reinstated') throw new Error(`${label}.status is invalid`)
  const rawMessage = string(item.rawMessage, `${label}.rawMessage`)
  if (!rawMessage.trim()) throw new Error(`${label}.rawMessage must be non-blank`)
  return freeze({
    driverId: parseDriverId(item.driverId, `${label}.driverId`),
    lapNumber: integer(item.lapNumber, `${label}.lapNumber`, 1),
    eventTimeMs: integer(item.eventTimeMs, `${label}.eventTimeMs`),
    status,
    reason: nullable(item.reason, (inner) => parseNullableReason(inner, `${label}.reason`)),
    rawMessage,
  })
}

function parseQualifyingLapStatusValue(value: unknown, label: string): QualifyingLapStatus {
  if (value !== 'valid' && value !== 'deleted') throw new Error(`${label} is invalid`)
  return value
}

function parseNullableReason(value: unknown, label: string): string {
  const reason = string(value, label)
  if (!reason.trim()) throw new Error(`${label} must be non-blank`)
  return reason
}

function sortQualifyingLapStatusEvents(events: readonly QualifyingLapStatusEvent[]): readonly QualifyingLapStatusEvent[] {
  return [...events].sort((left, right) => left.eventTimeMs - right.eventTimeMs
    || left.driverId.localeCompare(right.driverId)
    || left.lapNumber - right.lapNumber
    || left.status.localeCompare(right.status)
    || (left.reason ?? '').localeCompare(right.reason ?? '')
    || left.rawMessage.localeCompare(right.rawMessage))
}

function validateQualifyingLapStatusEvents(
  drivers: Readonly<Record<string, QualifyingLapStatusRecord>>,
  events: readonly QualifyingLapStatusEvent[],
): void {
  const semanticKeys = new Set<string>()
  const sameTimeStatuses = new Map<string, QualifyingLapStatusEventStatus>()
  const state = new Map<string, boolean>()
  for (const [driverId, record] of Object.entries(drivers)) {
    record.lapNumber.forEach((lapNumber, index) => state.set(`${driverId}/${lapNumber}`, record.status[index] === 'deleted'))
  }
  const eventState = new Map<string, boolean>()
  for (const event of events) {
    const key = `${event.driverId}/${event.lapNumber}`
    const semanticKey = `${key}/${event.eventTimeMs}/${event.status}/${event.reason ?? ''}`
    if (semanticKeys.has(semanticKey)) throw new Error('qualifying lap status events contain duplicate semantic records')
    semanticKeys.add(semanticKey)
    const record = drivers[event.driverId]
    if (record === undefined || !record.lapNumber.includes(event.lapNumber)) throw new Error('qualifying lap status event references an unknown lap')
    const sameTimeKey = `${key}/${event.eventTimeMs}`
    const previousStatus = sameTimeStatuses.get(sameTimeKey)
    if (previousStatus !== undefined && previousStatus !== event.status) throw new Error('qualifying lap status events contain contradictory same-time statuses')
    sameTimeStatuses.set(sameTimeKey, event.status)
    eventState.set(key, event.status === 'deleted')
  }
  for (const [key, finalDeleted] of state) {
    if ((eventState.get(key) ?? false) !== finalDeleted) throw new Error('qualifying lap status events disagree with final statuses')
  }
}

function parseQualifyingColumns(value: unknown, label: string): QualifyingDriverColumns {
  const item = object(value, label)
  const fields = ['qualifyingPosition', 'q1TimeMs', 'q2TimeMs', 'q3TimeMs', 'bestLapNumber', 'bestLapTimeMs'] as const
  exact(item, fields, [], label)
  const qualifyingPosition = parseStandaloneColumn(item.qualifyingPosition, `${label}.qualifyingPosition`, (entry) => nullable(entry, (inner) => integer(inner, `${label}.qualifyingPosition`, 1)))
  const length = qualifyingPosition.length
  const q1TimeMs = parseNullableNonNegativeColumn(item.q1TimeMs, `${label}.q1TimeMs`, length)
  const q2TimeMs = parseNullableNonNegativeColumn(item.q2TimeMs, `${label}.q2TimeMs`, length)
  const q3TimeMs = parseNullableNonNegativeColumn(item.q3TimeMs, `${label}.q3TimeMs`, length)
  const bestLapNumber = parseNullablePositiveColumn(item.bestLapNumber, `${label}.bestLapNumber`, length)
  const bestLapTimeMs = parseNullableNonNegativeColumn(item.bestLapTimeMs, `${label}.bestLapTimeMs`, length)
  return freeze({ qualifyingPosition, q1TimeMs, q2TimeMs, q3TimeMs, bestLapNumber, bestLapTimeMs })
}

export function parseWeatherSidecar(value: unknown): WeatherSidecar {
  const item = object(value, 'weather sidecar')
  exact(item, ['contractVersion', 'fixtureId', 'timeMs', 'airTempC', 'humidityPct', 'pressureMbar', 'rainfall', 'trackTempC', 'windDirectionDeg', 'windSpeedMps'], [], 'weather sidecar')
  if (item.contractVersion !== 'v2') throw new Error('weather sidecar must be contract version v2')
  const fixtureId = parseFixtureId(item.fixtureId, 'weather sidecar fixture id')
  const timeMs = parseStandaloneColumn(item.timeMs, 'weather sidecar timeMs', (entry) => integer(entry, 'weather sidecar timeMs value'))
  if (!timeMs.length) throw new Error('weather sidecar timeMs must be non-empty')
  assertStrictlyIncreasing(timeMs, 'weather sidecar timeMs must be strictly increasing')
  const airTempC = parseWeatherMeasurement(item.airTempC, timeMs.length, 'airTempC', (value) => positiveFinite(value, 'weather sidecar airTempC value'))
  const humidityPct = parseWeatherMeasurement(item.humidityPct, timeMs.length, 'humidityPct', (value) => boundedFinite(value, 'weather sidecar humidityPct value', 0, 100))
  const pressureMbar = parseWeatherMeasurement(item.pressureMbar, timeMs.length, 'pressureMbar', (value) => positiveFinite(value, 'weather sidecar pressureMbar value'))
  const rainfall = parseWeatherColumn(item.rainfall, timeMs.length, 'rainfall', (value) => {
    if (typeof value !== 'boolean') throw new Error('weather sidecar rainfall must contain booleans or null')
    return value
  })
  const trackTempC = parseWeatherMeasurement(item.trackTempC, timeMs.length, 'trackTempC', (value) => positiveFinite(value, 'weather sidecar trackTempC value'))
  const windDirectionDeg = parseWeatherMeasurement(item.windDirectionDeg, timeMs.length, 'windDirectionDeg', (value) => integer(value, 'weather sidecar windDirectionDeg value', 0, 359))
  const windSpeedMps = parseWeatherMeasurement(item.windSpeedMps, timeMs.length, 'windSpeedMps', (value) => boundedFinite(value, 'weather sidecar windSpeedMps value', 0, Number.MAX_VALUE))
  return freeze({ contractVersion: 'v2', fixtureId, timeMs, airTempC, humidityPct, pressureMbar, rainfall, trackTempC, windDirectionDeg, windSpeedMps })
}

function parseWeatherMeasurement<T>(value: unknown, length: number, label: string, parse: (entry: unknown) => T): readonly (T | null)[] {
  return parseWeatherColumn(value, length, label, parse)
}

function parseWeatherColumn<T>(value: unknown, length: number, label: string, parse: (entry: unknown) => T): readonly (T | null)[] {
  return parseColumn(value, length, `weather sidecar.${label}`, (entry) => nullable(entry, parse))
}

function positiveFinite(value: unknown, label: string): number {
  const parsed = finite(value, label)
  if (parsed <= 0) throw new Error(`${label} must be greater than zero`)
  return parsed
}

function boundedFinite(value: unknown, label: string, minimum: number, maximum: number): number {
  const parsed = finite(value, label)
  if (parsed < minimum || parsed > maximum) throw new Error(`${label} must be between ${minimum} and ${maximum}`)
  return parsed
}

function parsePenaltyIssuance(value: unknown, index: number): PenaltyIssuance {
  const label = `penalty sidecar penaltyIssuances[${index}]`
  const item = object(value, label)
  exact(item, ['driverId', 'sessionTimeMs', 'penaltyType', 'reason', 'rawMessage'], ['lapNumber'], label)
  const lapNumber = item.lapNumber === undefined ? undefined : integer(item.lapNumber, `${label}.lapNumber`, 1)
  return freeze({
    driverId: parseDriverId(item.driverId, `${label}.driverId`),
    sessionTimeMs: integer(item.sessionTimeMs, `${label}.sessionTimeMs`),
    penaltyType: string(item.penaltyType, `${label}.penaltyType`),
    reason: string(item.reason, `${label}.reason`),
    rawMessage: string(item.rawMessage, `${label}.rawMessage`),
    ...(lapNumber === undefined ? {} : { lapNumber }),
  })
}

function parseFixtureId(value: unknown, label: string): string {
  const fixtureId = string(value, label)
  if (!FIXTURE_ID.test(fixtureId)) throw new Error(`${label} is invalid`)
  return fixtureId
}

function parseDriverId(value: unknown, label: string): string {
  const driverId = string(value, label)
  if (!DRIVER_ID.test(driverId)) throw new Error(`${label} is invalid`)
  return driverId
}

function parseDrivers<T>(value: unknown, label: string, parse: (value: unknown, label: string) => T): Readonly<Record<string, T>> {
  const rawDrivers = object(value, label)
  const entries = Object.entries(rawDrivers).sort(([left], [right]) => left.localeCompare(right))
  if (!entries.length) throw new Error(`${label} must be a non-empty object`)
  for (const [driverId] of entries) if (!DRIVER_ID.test(driverId)) throw new Error(`${label}.${driverId} driver ID is invalid`)
  return freeze(Object.fromEntries(entries.map(([driverId, columns]) => [driverId, parse(columns, `${label}.${driverId}`)]))) as Readonly<Record<string, T>>
}

function parseLapSectorColumns(value: unknown, label: string, includeQualifyingPhase: boolean): LapSectorDriverColumns {
  const item = object(value, label)
  const fields = ['lapNumber', 'lapStartMs', 'lapEndMs', 'lapDurationMs', 'sector1DurationMs', 'sector2DurationMs', 'sector3DurationMs', 'sector1SessionTimeMs', 'sector2SessionTimeMs', 'sector3SessionTimeMs', ...(includeQualifyingPhase ? ['qualifyingPhase'] : [])] as const
  exact(item, fields, ['lapKind'], label)
  const lapNumber = parseStandaloneColumn(item.lapNumber, `${label}.lapNumber`, (entry) => integer(entry, `${label}.lapNumber`, 1))
  const lapStartMs = parseColumn(item.lapStartMs, lapNumber.length, `${label}.lapStartMs`, (entry) => integer(entry, `${label}.lapStartMs`))
  const lapEndMs = parseColumn(item.lapEndMs, lapNumber.length, `${label}.lapEndMs`, (entry) => integer(entry, `${label}.lapEndMs`))
  const lapDurationMs = parseNullableNonNegativeColumn(item.lapDurationMs, `${label}.lapDurationMs`, lapNumber.length)
  const sector1DurationMs = parseNullableNonNegativeColumn(item.sector1DurationMs, `${label}.sector1DurationMs`, lapNumber.length)
  const sector2DurationMs = parseNullableNonNegativeColumn(item.sector2DurationMs, `${label}.sector2DurationMs`, lapNumber.length)
  const sector3DurationMs = parseNullableNonNegativeColumn(item.sector3DurationMs, `${label}.sector3DurationMs`, lapNumber.length)
  const sector1SessionTimeMs = parseNullableNonNegativeColumn(item.sector1SessionTimeMs, `${label}.sector1SessionTimeMs`, lapNumber.length)
  const sector2SessionTimeMs = parseNullableNonNegativeColumn(item.sector2SessionTimeMs, `${label}.sector2SessionTimeMs`, lapNumber.length)
  const sector3SessionTimeMs = parseNullableNonNegativeColumn(item.sector3SessionTimeMs, `${label}.sector3SessionTimeMs`, lapNumber.length)
  const qualifyingPhase = includeQualifyingPhase
    ? parseColumn(item.qualifyingPhase, lapNumber.length, `${label}.qualifyingPhase`, (entry) => nullable(entry, (value) => parseQualifyingPhase(value, `${label}.qualifyingPhase`)))
    : undefined
  // Optional aligned lap-kind classification. Absence means the capability is
  // unavailable (fail closed); when present every element must be one of the
  // four enum values — null or out-of-enum entries are rejected.
  const lapKind = item.lapKind === undefined
    ? undefined
    : parseColumn(item.lapKind, lapNumber.length, `${label}.lapKind`, (entry) => parseLapKind(entry, `${label}.lapKind`))
  assertStrictlyIncreasing(lapNumber, `${label}.lapNumber must be strictly increasing`)
  assertNonDecreasing(lapStartMs, `${label}.lapStartMs must be ordered`)
  assertNonDecreasing(lapEndMs, `${label}.lapEndMs must be ordered`)
  if (lapEndMs.some((endMs, index) => endMs < lapStartMs[index])) throw new Error(`${label} lap end must not precede lap start`)
  return freeze({ lapNumber, lapStartMs, lapEndMs, lapDurationMs, sector1DurationMs, sector2DurationMs, sector3DurationMs, sector1SessionTimeMs, sector2SessionTimeMs, sector3SessionTimeMs, ...(qualifyingPhase === undefined ? {} : { qualifyingPhase }), ...(lapKind === undefined ? {} : { lapKind }) })
}

function parseLapKind(value: unknown, label: string): LapKind {
  if (!LAP_KINDS.includes(value as LapKind)) throw new Error(`${label} must be flying, outlap, inlap, or unknown`)
  return value as LapKind
}

function parseQualifyingPhase(value: unknown, label: string): QualifyingPhase {
  if (!QUALIFYING_PHASES.includes(value as QualifyingPhase)) throw new Error(`${label} must be Q1, Q2, or Q3`)
  return value as QualifyingPhase
}

function parseQualifyingPhaseBoundary(value: unknown, index: number): QualifyingPhaseBoundary {
  const label = `lap sector sidecar phaseBoundaries[${index}]`
  const item = object(value, label)
  exact(item, ['phase', 'startMs'], [], label)
  return freeze({
    phase: parseQualifyingPhase(item.phase, `${label}.phase`),
    startMs: integer(item.startMs, `${label}.startMs`),
  })
}

function validateQualifyingPhaseBoundaries(
  boundaries: readonly QualifyingPhaseBoundary[],
  drivers: Readonly<Record<string, LapSectorDriverColumns>>,
): void {
  const phases = boundaries.map(({ phase }) => phase)
  const orderedPhases = [...new Set(phases)].sort((left, right) => QUALIFYING_PHASES.indexOf(left) - QUALIFYING_PHASES.indexOf(right))
  if (phases.length !== orderedPhases.length || phases.some((phase, index) => phase !== orderedPhases[index])) {
    throw new Error('lap sector sidecar phase boundaries must be ordered by phase')
  }
  if (boundaries.some((boundary, index) => index > 0 && boundary.startMs <= boundaries[index - 1].startMs)) {
    throw new Error('lap sector sidecar phase boundaries must have strictly increasing starts')
  }

  const expectedStarts = new Map<QualifyingPhase, number>()
  for (const driver of Object.values(drivers)) {
    const phasesForDriver = driver.qualifyingPhase ?? []
    for (let index = 0; index < phasesForDriver.length; index += 1) {
      const phase = phasesForDriver[index]
      if (phase === null) continue
      const lapStartMs = driver.lapStartMs[index]
      expectedStarts.set(phase, Math.min(expectedStarts.get(phase) ?? lapStartMs, lapStartMs))
    }
  }
  const actualStarts = new Map(boundaries.map(({ phase, startMs }) => [phase, startMs]))
  if (actualStarts.size !== expectedStarts.size || [...expectedStarts].some(([phase, startMs]) => actualStarts.get(phase) !== startMs)) {
    throw new Error('lap sector sidecar phase boundaries disagree with lap phases')
  }
}

function parseStintColumns(value: unknown, label: string): StintDriverColumns {
  const item = object(value, label)
  const fields = ['stintNumber', 'compound', 'startLap', 'endLap', 'startTimeMs', 'endTimeMs', 'tyreLifeAtStart', 'isFreshTyre', 'pitInTimeMs', 'pitOutTimeMs'] as const
  exact(item, fields, [], label)
  const stintNumber = parseStandaloneColumn(item.stintNumber, `${label}.stintNumber`, (entry) => integer(entry, `${label}.stintNumber`, 1))
  const compound = parseColumn(item.compound, stintNumber.length, `${label}.compound`, (entry) => nullable(entry, (value) => {
    if (typeof value !== 'string') throw new Error(`${label}.compound must contain strings or null`)
    return value
  }))
  const startLap = parseColumn(item.startLap, stintNumber.length, `${label}.startLap`, (entry) => integer(entry, `${label}.startLap`, 1))
  const endLap = parseNullablePositiveColumn(item.endLap, `${label}.endLap`, stintNumber.length)
  const startTimeMs = parseNullableNonNegativeColumn(item.startTimeMs, `${label}.startTimeMs`, stintNumber.length)
  const endTimeMs = parseNullableNonNegativeColumn(item.endTimeMs, `${label}.endTimeMs`, stintNumber.length)
  const tyreLifeAtStart = parseNullableNonNegativeColumn(item.tyreLifeAtStart, `${label}.tyreLifeAtStart`, stintNumber.length)
  const isFreshTyre = parseColumn(item.isFreshTyre, stintNumber.length, `${label}.isFreshTyre`, (entry) => nullable(entry, (value) => {
    if (typeof value !== 'boolean') throw new Error(`${label}.isFreshTyre must contain booleans or null`)
    return value
  }))
  const pitInTimeMs = parseNullableNonNegativeColumn(item.pitInTimeMs, `${label}.pitInTimeMs`, stintNumber.length)
  const pitOutTimeMs = parseNullableNonNegativeColumn(item.pitOutTimeMs, `${label}.pitOutTimeMs`, stintNumber.length)
  assertStrictlyIncreasing(stintNumber, `${label}.stintNumber must be strictly increasing`)
  if (endLap.some((end, index) => end !== null && end < startLap[index])) throw new Error(`${label} endLap must not precede startLap`)
  if (startTimeMs.some((start, index) => start !== null && endTimeMs[index] !== null && endTimeMs[index]! < start)) throw new Error(`${label} endTimeMs must not precede startTimeMs`)
  return freeze({ stintNumber, compound, startLap, endLap, startTimeMs, endTimeMs, tyreLifeAtStart, isFreshTyre, pitInTimeMs, pitOutTimeMs })
}

function parseNullableNonNegativeColumn(value: unknown, label: string, length: number): readonly (number | null)[] {
  return parseColumn(value, length, label, (entry) => nullable(entry, (value) => integer(value, `${label} value`)))
}

function parseNullablePositiveColumn(value: unknown, label: string, length: number): readonly (number | null)[] {
  return parseColumn(value, length, label, (entry) => nullable(entry, (value) => integer(value, `${label} value`, 1)))
}

function parseStandaloneColumn<T>(raw: unknown, label: string, parse: (entry: unknown) => T): readonly T[] {
  return freeze(array(raw, label).map(parse))
}

function assertStrictlyIncreasing(values: readonly number[], message: string): void {
  if (values.some((value, index) => index > 0 && value <= values[index - 1])) throw new Error(message)
}

function assertNonDecreasing(values: readonly number[], message: string): void {
  if (values.some((value, index) => index > 0 && value < values[index - 1])) throw new Error(message)
}

export function parseTimelineSummary(value: unknown): TimelineSummary {
  const item = object(value, 'timeline summary')
  exact(item, ['contractVersion', 'fixtureId', 'startMs', 'endMs', 'intervals', 'dnfMarkers'], [], 'timeline summary')
  if (item.contractVersion !== 'v2') throw new Error('timeline summary must be contract version v2')
  const fixtureId = string(item.fixtureId, 'timeline summary fixture id')
  if (!FIXTURE_ID.test(fixtureId)) throw new Error('timeline summary fixture ID is invalid')
  const startMs = integer(item.startMs, 'timeline summary start')
  const endMs = integer(item.endMs, 'timeline summary end')
  if (endMs <= startMs) throw new Error('timeline summary bounds are invalid')
  const intervals = array(item.intervals, 'timeline summary intervals').map((entry, index) => parseTimelineInterval(entry, index, startMs, endMs))
  const dnfMarkers = array(item.dnfMarkers, 'timeline summary DNF markers').map((entry, index) => parseDnfMarker(entry, index, startMs, endMs))
  if (intervals.some((interval, index) => index > 0 && compareTimelineIntervals(intervals[index - 1], interval) > 0)) throw new Error('timeline summary intervals must be deterministically ordered')
  if (dnfMarkers.some((marker, index) => index > 0 && compareDnfMarkers(dnfMarkers[index - 1], marker) > 0)) throw new Error('timeline summary DNF markers must be deterministically ordered')
  if (new Set(dnfMarkers.map(({ driverId }) => driverId)).size !== dnfMarkers.length) throw new Error('timeline summary DNF markers must have unique drivers')
  return freeze({ contractVersion: 'v2', fixtureId, startMs, endMs, intervals: freeze(intervals), dnfMarkers: freeze(dnfMarkers) })
}

function compareTimelineIntervals(left: TimelineInterval, right: TimelineInterval): number {
  return left.startMs - right.startMs || left.endMs - right.endMs || left.kind.localeCompare(right.kind)
}

function compareDnfMarkers(left: DnfMarker, right: DnfMarker): number {
  return left.timeMs - right.timeMs || left.driverId.localeCompare(right.driverId)
}

function parseTimelineInterval(value: unknown, index: number, summaryStartMs: number, summaryEndMs: number): TimelineInterval {
  const label = `timeline summary intervals[${index}]`
  const item = object(value, label)
  exact(item, ['kind', 'startMs', 'endMs'], [], label)
  if (!TIMELINE_INTERVAL_KINDS.includes(item.kind as TimelineIntervalKind)) throw new Error(`${label}.kind is invalid`)
  const startMs = integer(item.startMs, `${label}.startMs`)
  const endMs = integer(item.endMs, `${label}.endMs`)
  if (startMs < summaryStartMs || endMs > summaryEndMs || endMs <= startMs) throw new Error(`${label} is outside summary bounds`)
  return freeze({ kind: item.kind as TimelineIntervalKind, startMs, endMs })
}

function parseDnfMarker(value: unknown, index: number, summaryStartMs: number, summaryEndMs: number): DnfMarker {
  const label = `timeline summary dnfMarkers[${index}]`
  const item = object(value, label)
  exact(item, ['driverId', 'timeMs'], [], label)
  const driverId = string(item.driverId, `${label}.driverId`)
  if (!DRIVER_ID.test(driverId)) throw new Error(`${label}.driverId is invalid`)
  const timeMs = integer(item.timeMs, `${label}.timeMs`)
  if (timeMs < summaryStartMs || timeMs >= summaryEndMs) throw new Error(`${label} is outside summary bounds`)
  return freeze({ driverId, timeMs })
}

export function parseQualifyingTimeline(value: unknown): QualifyingTimeline {
  const item = object(value, 'qualifying timeline')
  exact(item, ['contractVersion', 'fixtureId', 'startMs', 'endMs', 'intervals', 'incidentMarkers'], [], 'qualifying timeline')
  if (item.contractVersion !== 'v2') throw new Error('qualifying timeline must be contract version v2')
  const fixtureId = string(item.fixtureId, 'qualifying timeline fixture id')
  if (!FIXTURE_ID.test(fixtureId)) throw new Error('qualifying timeline fixture ID is invalid')
  const startMs = integer(item.startMs, 'qualifying timeline start')
  const endMs = integer(item.endMs, 'qualifying timeline end')
  if (endMs <= startMs) throw new Error('qualifying timeline bounds are invalid')
  const intervals = array(item.intervals, 'qualifying timeline intervals').map((entry, index) => parseQualifyingTimelineInterval(entry, index, startMs, endMs))
  const incidentMarkers = array(item.incidentMarkers, 'qualifying timeline incident markers').map((entry, index) => parseQualifyingIncidentMarker(entry, index, startMs, endMs))
  if (intervals.some((interval, index) => index > 0 && compareQualifyingTimelineIntervals(intervals[index - 1], interval) > 0)) throw new Error('qualifying timeline intervals must be deterministically ordered')
  if (incidentMarkers.some((marker, index) => index > 0 && compareQualifyingIncidentMarkers(incidentMarkers[index - 1], marker) > 0)) throw new Error('qualifying timeline incident markers must be deterministically ordered')
  return freeze({ contractVersion: 'v2', fixtureId, startMs, endMs, intervals: freeze(intervals), incidentMarkers: freeze(incidentMarkers) })
}

function compareQualifyingTimelineIntervals(left: QualifyingTimelineInterval, right: QualifyingTimelineInterval): number {
  return left.startMs - right.startMs || left.endMs - right.endMs || left.kind.localeCompare(right.kind)
}

function compareQualifyingIncidentMarkers(left: QualifyingIncidentMarker, right: QualifyingIncidentMarker): number {
  return left.timeMs - right.timeMs || left.driverId.localeCompare(right.driverId) || left.rawMessage.localeCompare(right.rawMessage)
}

function parseQualifyingTimelineInterval(value: unknown, index: number, timelineStartMs: number, timelineEndMs: number): QualifyingTimelineInterval {
  const label = `qualifying timeline intervals[${index}]`
  const item = object(value, label)
  exact(item, ['kind', 'startMs', 'endMs'], [], label)
  if (!QUALIFYING_TIMELINE_INTERVAL_KINDS.includes(item.kind as QualifyingTimelineIntervalKind)) throw new Error(`${label}.kind is invalid`)
  const startMs = integer(item.startMs, `${label}.startMs`)
  const endMs = integer(item.endMs, `${label}.endMs`)
  if (startMs < timelineStartMs || endMs > timelineEndMs || endMs <= startMs) throw new Error(`${label} is outside timeline bounds`)
  return freeze({ kind: item.kind as QualifyingTimelineIntervalKind, startMs, endMs })
}

function parseQualifyingIncidentMarker(value: unknown, index: number, timelineStartMs: number, timelineEndMs: number): QualifyingIncidentMarker {
  const label = `qualifying timeline incidentMarkers[${index}]`
  const item = object(value, label)
  exact(item, ['driverId', 'timeMs', 'source', 'rawMessage'], ['lapNumber'], label)
  if (item.source !== 'race-control-car-event') throw new Error(`${label}.source is unsupported`)
  const driverId = parseDriverId(item.driverId, `${label}.driverId`)
  const timeMs = integer(item.timeMs, `${label}.timeMs`)
  if (timeMs < timelineStartMs || timeMs >= timelineEndMs) throw new Error(`${label} is outside timeline bounds`)
  const rawMessage = string(item.rawMessage, `${label}.rawMessage`)
  if (!rawMessage.trim()) throw new Error(`${label}.rawMessage must be non-blank`)
  const lapNumber = item.lapNumber === undefined ? undefined : integer(item.lapNumber, `${label}.lapNumber`, 1)
  return freeze({ driverId, timeMs, source: 'race-control-car-event', rawMessage, ...(lapNumber === undefined ? {} : { lapNumber }) })
}

function parseLapStart(value: unknown, index: number) {
  const item = object(value, `manifest.lapStarts[${index}]`)
  exact(item, ['lap', 'startMs'], [], `manifest.lapStarts[${index}]`)
  const lap = integer(item.lap, `manifest.lapStarts[${index}].lap`)
  const startMs = integer(item.startMs, `manifest.lapStarts[${index}].startMs`)
  if (lap < 1 || startMs < 0) throw new Error(`manifest.lapStarts[${index}] is invalid`)
  return freeze({ lap, startMs })
}

function parseChunkReference(raw: unknown, index: number): ChunkReference {
  const item = object(raw, `manifest.chunks[${index}]`)
  const ref = artifact(raw, `manifest.chunks[${index}]`, ['sequence', 'startMs', 'endMs', 'overlapWithPreviousMs'])
  exact(item, ['sequence', 'path', 'schemaId', 'startMs', 'endMs', 'overlapWithPreviousMs'], ['sha256'], `manifest.chunks[${index}]`)
  const startMs = integer(item.startMs, 'chunk startMs'); const endMs = integer(item.endMs, 'chunk endMs')
  const sequence = integer(item.sequence, 'chunk sequence', 1)
  if (endMs <= startMs || item.path !== `chunks/chunk-${sequence.toString().padStart(3, '0')}.json` || item.schemaId !== CHUNK_SCHEMA) throw new Error('chunk reference interval or identity is invalid')
  return freeze({ ...ref, sequence, startMs, endMs, overlapWithPreviousMs: integer(item.overlapWithPreviousMs, 'chunk overlap') })
}

function parseDriver(raw: unknown, index: number): DriverMetadata {
  const item = object(raw, `manifest.drivers[${index}]`)
  exact(item, ['id', 'displayName', 'teamName', 'colorHex', 'carNumber'], [], `manifest.drivers[${index}]`)
  const id = string(item.id, 'driver id'); const colorHex = string(item.colorHex, 'driver color'); const carNumber = string(item.carNumber, 'driver number')
  if (!/^[A-Z0-9]{2,4}$/.test(id) || !/^#[0-9A-Fa-f]{6}$/.test(colorHex) || !/^[0-9]{1,2}$/.test(carNumber)) throw new Error('driver metadata format is invalid')
  return freeze({ id, displayName: string(item.displayName, 'driver name'), teamName: string(item.teamName, 'driver team'), colorHex, carNumber })
}

export function parseTrackAssets(value: unknown): TrackAssets {
  const item = object(value, 'track assets')
  exact(item, ['contractVersion', 'fixtureId', 'trackId', 'trackName', 'coordinateSpace', 'circuitLengthMeters', 'rotationDegrees', 'startFinish', 'centerLine', 'innerBoundary', 'outerBoundary'], ['distanceMarkersMeters', 'drsZones'], 'track assets')
  if (item.contractVersion !== 'v2') throw new Error('track assets must be contract version v2')
  const space = object(item.coordinateSpace, 'track coordinate space'); exact(space, ['units', 'origin'], [], 'track coordinate space')
  if (space.units !== 'meters') throw new Error('track coordinate units must be meters')
  const finish = object(item.startFinish, 'track start finish'); exact(finish, ['center', 'inner', 'outer'], [], 'track start finish')
  const line = (raw: unknown, label: string) => { const values = array(raw, label).map((point, index) => parsePoint(point, `${label}[${index}]`)); if (values.length < 4) throw new Error(`${label} requires at least four points`); return freeze(values) }
  const length = finite(item.circuitLengthMeters, 'circuit length'); if (length <= 0) throw new Error('circuit length must be positive')
  const markers = item.distanceMarkersMeters === undefined ? undefined : array(item.distanceMarkersMeters, 'distance markers').map((entry) => integer(entry, 'distance marker'))
  if (markers && new Set(markers).size !== markers.length) throw new Error('distance markers must be unique')
  const zones = item.drsZones === undefined ? undefined : array(item.drsZones, 'DRS zones').map((raw, index) => { const zone = object(raw, `DRS zone ${index}`); exact(zone, ['startMeters', 'endMeters'], [], `DRS zone ${index}`); const startMeters = finite(zone.startMeters, 'DRS start'); const endMeters = finite(zone.endMeters, 'DRS end'); if (startMeters < 0 || endMeters <= startMeters || endMeters > length) throw new Error('DRS zone is invalid'); return freeze({ startMeters, endMeters }) })
  const fixtureId = string(item.fixtureId, 'track fixture id'); const trackId = string(item.trackId, 'track id')
  if (!FIXTURE_ID.test(fixtureId) || !FIXTURE_ID.test(trackId)) throw new Error('track fixture or track ID is invalid')
  return freeze({ contractVersion: 'v2', fixtureId, trackId, trackName: string(item.trackName, 'track name'), coordinateSpace: freeze({ units: 'meters' as const, origin: string(space.origin, 'track origin') }), circuitLengthMeters: length, rotationDegrees: finite(item.rotationDegrees, 'track rotation'), startFinish: freeze({ center: parsePoint(finish.center, 'start finish center'), inner: parsePoint(finish.inner, 'start finish inner'), outer: parsePoint(finish.outer, 'start finish outer') }), centerLine: line(item.centerLine, 'center line'), innerBoundary: line(item.innerBoundary, 'inner boundary'), outerBoundary: line(item.outerBoundary, 'outer boundary'), ...(markers ? { distanceMarkersMeters: freeze(markers) } : {}), ...(zones ? { drsZones: freeze(zones) } : {}) })
}

function parsePoint(raw: unknown, label: string): TrackPoint { const item = object(raw, label); exact(item, ['x', 'y'], [], label); return freeze({ x: finite(item.x, `${label}.x`), y: finite(item.y, `${label}.y`) }) }

export function parseChunk(value: unknown): ReplayChunk {
  const item = object(value, 'chunk')
  exact(item, ['contractVersion', 'fixtureId', 'chunkId', 'sequence', 'startMs', 'endMs', 'overlap', 'timeMs', 'authoritativeStartIndex', 'drivers', 'leaderboardOrder', 'trackStatusCode', 'weatherState', 'events'], [], 'chunk')
  if (item.contractVersion !== 'v2') throw new Error('chunk must be contract version v2')
  const timeMs = array(item.timeMs, 'chunk.timeMs').map((time, index) => integer(time, `timeMs[${index}]`))
  if (!timeMs.length || timeMs.some((time, index) => index > 0 && time <= timeMs[index - 1])) throw new Error('chunk timeline must be non-empty, sorted, and unique')
  const rawDrivers = object(item.drivers, 'chunk.drivers')
  const driverEntries = Object.entries(rawDrivers)
  if (!driverEntries.length || driverEntries.some(([id]) => !DRIVER_ID.test(id))) throw new Error('chunk driver IDs are invalid')
  const drivers = freeze(Object.fromEntries(driverEntries.map(([id, columns]) => [id, parseColumns(columns, timeMs.length, id)])))
  const leaderboardOrder = freeze(array(item.leaderboardOrder, 'leaderboard').map((row, index) => nullable(row, (entry) => { const values = array(entry, `leaderboard[${index}]`).map((id) => string(id, 'leaderboard driver')); if (!values.length || new Set(values).size !== values.length) throw new Error('leaderboard row is invalid'); return freeze(values) })))
  const trackStatusCode = freeze(array(item.trackStatusCode, 'track status').map((entry) => nullable(entry, (value) => integer(value, 'track status'))))
  const weatherState = freeze(array(item.weatherState, 'weather').map((entry) => nullable(entry, (value) => string(value, 'weather state'))))
  if ([leaderboardOrder, trackStatusCode, weatherState].some((column) => column.length !== timeMs.length)) throw new Error('chunk global columns are not aligned')
  const fixtureId = string(item.fixtureId, 'chunk fixture id'); if (!FIXTURE_ID.test(fixtureId)) throw new Error('chunk fixture ID is invalid')
  const chunkId = string(item.chunkId, 'chunk id')
  if (!/^chunk-(?:00[1-9]|0[1-9][0-9]|[1-9][0-9]{2,})$/.test(chunkId)) throw new Error('chunk id is invalid')
  const startMs = integer(item.startMs, 'chunk start'); const endMs = integer(item.endMs, 'chunk end')
  if (endMs <= startMs || integer(item.authoritativeStartIndex, 'chunk authoritative index') >= timeMs.length) throw new Error('chunk interval or authority is invalid')
  validateDerivedFields(drivers, leaderboardOrder)
  return freeze({ contractVersion: 'v2', fixtureId, chunkId, sequence: integer(item.sequence, 'chunk sequence', 1), startMs, endMs, overlap: parseOverlap(item.overlap), timeMs, authoritativeStartIndex: integer(item.authoritativeStartIndex, 'chunk authoritative index'), drivers, leaderboardOrder, trackStatusCode, weatherState, events: freeze(array(item.events, 'events').map(parseEvent)) })
}

function parseOverlap(raw: unknown): ReplayOverlap {
  const item = object(raw, 'chunk.overlap'); exact(item, ['kind', 'previousChunkPath', 'range', 'authoritativeFromMs'], [], 'chunk.overlap')
  if (item.kind === 'none') { if (item.previousChunkPath !== null || item.range !== null || item.authoritativeFromMs !== null) throw new Error('none overlap contains handoff metadata'); return freeze({ kind: 'none', previousChunkPath: null, range: null, authoritativeFromMs: null }) }
  if (item.kind !== 'handoff') throw new Error('overlap kind is invalid')
  const range = object(item.range, 'overlap range'); exact(range, ['startMs', 'endMs'], [], 'overlap range')
  return freeze({ kind: 'handoff', previousChunkPath: string(item.previousChunkPath, 'previous chunk path'), range: freeze({ startMs: integer(range.startMs, 'overlap start'), endMs: integer(range.endMs, 'overlap end') }), authoritativeFromMs: integer(item.authoritativeFromMs, 'overlap authority') })
}

function parseEvent(raw: unknown): ReplayEvent {
  const item = object(raw, 'event'); exact(item, ['sessionTimeMs', 'eventType', 'description'], ['driverId', 'payload'], 'event')
  const driverId = item.driverId === undefined ? undefined : nullable(item.driverId, (value) => parseDriverId(value, 'event driver'))
  return freeze({ sessionTimeMs: integer(item.sessionTimeMs, 'event time'), eventType: string(item.eventType, 'event type'), description: string(item.description, 'event description'), ...(driverId === undefined ? {} : { driverId }), ...(item.payload === undefined ? {} : { payload: jsonObject(item.payload, 'event payload') }) })
}

function parseColumns(value: unknown, length: number, label: string): DriverColumns {
  const columns = object(value, `driver ${label}`); exact(columns, REQUIRED_DRIVER_FIELDS, OPTIONAL_DRIVER_FIELDS, `driver ${label}`)
  const numberColumn = (field: string) => parseColumn(columns[field], length, `${label}.${field}`, (entry) => nullable(entry, (value) => finite(value, field)))
  const integerColumn = (field: string, min: number, max = Number.MAX_SAFE_INTEGER) => parseColumn(columns[field], length, `${label}.${field}`, (entry) => nullable(entry, (value) => integer(value, field, min, max)))
  const stringColumn = (field: string) => parseColumn(columns[field], length, `${label}.${field}`, (entry) => nullable(entry, (value) => string(value, field)))
  const nonNegativeNumberColumn = (field: string) => parseColumn(columns[field], length, `${label}.${field}`, (entry) => nullable(entry, (value) => { const parsed = finite(value, field); if (parsed < 0) throw new Error(`${field} must be non-negative`); return parsed }))
  const rpm = columns.rpm === undefined ? Array<null>(length).fill(null) : numberColumn('rpm')
  const tyreAge = columns.tyreAge === undefined
    ? Array<null>(length).fill(null)
    : parseColumn(columns.tyreAge, length, `${label}.tyreAge`, (entry) => nullable(entry, (value) => integer(value, 'tyre age', 0)))
  // Optional v2 columns are normalized to nulls when the producer has no samples.
  const isFinished = columns.isFinished === undefined
    ? Array<null>(length).fill(null)
    : parseColumn(columns.isFinished, length, `${label}.isFinished`, (entry) => nullable(entry, (value) => { if (typeof value !== 'boolean') throw new Error('finished state must be boolean'); return value }))
  return freeze({ x: numberColumn('x'), y: numberColumn('y'), trackDistanceMeters: nonNegativeNumberColumn('trackDistanceMeters'), speed: numberColumn('speed'), rpm, throttle: numberColumn('throttle'), brake: integerColumn('brake', Number.MIN_SAFE_INTEGER), gapToLeaderMs: nonNegativeNumberColumn('gapToLeaderMs'), lap: integerColumn('lap', 1), position: integerColumn('position', 1), gear: integerColumn('gear', 0, 8), drs: integerColumn('drs', 0), tyreCompound: stringColumn('tyreCompound'), tyreAge, status: stringColumn('status'), isInPitLane: parseColumn(columns.isInPitLane, length, `${label}.isInPitLane`, (entry) => nullable(entry, (value) => { if (typeof value !== 'boolean') throw new Error('pit state must be boolean'); return value })), isFinished })
}

function validateDerivedFields(drivers: ReplayChunk['drivers'], order: ReplayChunk['leaderboardOrder']): void {
  const driverIds = new Set(Object.keys(drivers))
  for (let index = 0; index < order.length; index += 1) {
    const row = order[index]
    if (row !== null && row.some((driverId) => !driverIds.has(driverId))) throw new Error('Leaderboard drivers disagree')
    const participants = Object.entries(drivers).filter(([, columns]) => columns.position[index] !== null)
    if (!participants.length) continue
    if (row === null) throw new Error('Populated positions require leaderboard order')
    const ranked = [...participants].sort((left, right) => left[1].position[index]! - right[1].position[index]!)
    if (ranked.some(([, columns], position) => columns.position[index] !== position + 1)) throw new Error('Positions must be unique consecutive values')
    if (row.length !== ranked.length || row.some((driverId, position) => driverId !== ranked[position][0])) throw new Error('Leaderboard order disagrees with positions')
    for (const [driverId, columns] of Object.entries(drivers)) {
      const position = columns.position[index]; const gap = columns.gapToLeaderMs[index]
      if (position === null && gap !== null) throw new Error(`Driver ${driverId} has gap without position`)
      if (position === 1 && gap !== 0) throw new Error('Leader gap must be zero')
    }
  }
}

function parseColumn<T>(raw: unknown, length: number, label: string, parse: (entry: unknown) => T): readonly T[] { const values = array(raw, label); if (values.length !== length) throw new Error(`driver ${label} is not aligned to timeMs`); return freeze(values.map(parse)) }
