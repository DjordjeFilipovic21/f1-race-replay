import type {
  ArtifactReference, BrowserPointer, ChunkReference, DriverColumns, DriverMetadata,
  ReplayChunk, ReplayEvent, ReplayManifest, ReplayOverlap, TrackAssets, TrackPoint,
} from './types'
import { array, exact, finite, freeze, integer, jsonObject, nullable, object, string } from './value-guards'

export const MANIFEST_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v1:manifest'
export const CHUNK_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v1:chunk'
export const TRACK_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v1:track-assets'
const DRIVER_FIELDS = ['x', 'y', 'trackDistanceMeters', 'speed', 'throttle', 'brake', 'gapToLeaderMs', 'lap', 'position', 'gear', 'drs', 'tyreCompound', 'status', 'isInPitLane'] as const
const FIXTURE_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
const SHA256 = /^[0-9a-f]{64}$/
const DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/

function artifact(value: unknown, label: string, extraFields: readonly string[] = []): ArtifactReference {
  const item = object(value, label)
  exact(item, ['path', 'schemaId'], ['sha256', ...extraFields], label)
  const sha256 = item.sha256
  if (sha256 !== undefined && (typeof sha256 !== 'string' || !SHA256.test(sha256))) throw new Error(`${label}.sha256 is invalid`)
  return freeze({ path: string(item.path, `${label}.path`), schemaId: string(item.schemaId, `${label}.schemaId`), ...(sha256 === undefined ? {} : { sha256 }) })
}

export function parsePointer(value: unknown): BrowserPointer {
  const item = object(value, 'pointer')
  exact(item, ['formatVersion', 'deliveryVersion', 'manifestPath', 'manifestSha256'], [], 'pointer')
  if (item.formatVersion !== 'browser-delivery-v1') throw new Error('Unsupported browser pointer format version')
  if (typeof item.manifestSha256 !== 'string' || !SHA256.test(item.manifestSha256)) throw new Error('pointer.manifestSha256 is invalid')
  return freeze({ formatVersion: item.formatVersion, deliveryVersion: string(item.deliveryVersion, 'pointer.deliveryVersion'), manifestPath: string(item.manifestPath, 'pointer.manifestPath'), manifestSha256: item.manifestSha256 })
}

export function parseManifest(value: unknown): ReplayManifest {
  const item = object(value, 'manifest')
  exact(item, ['contractVersion', 'fixtureId', 'fixtureName', 'schemas', 'trackAssets', 'chunks', 'drivers'], ['description', 'formatVersion', 'deliveryVersion', 'sourceGenerationId', 'sourceManifestSha256', 'goldenSnapshots', 'createdAt', 'lapStarts'], 'manifest')
  if (item.contractVersion !== 'v1') throw new Error('manifest must be contract version v1')
  const schemas = object(item.schemas, 'manifest.schemas')
  exact(schemas, ['manifest', 'chunk', 'trackAssets'], [], 'manifest.schemas')
  if (schemas.manifest !== MANIFEST_SCHEMA || schemas.chunk !== CHUNK_SCHEMA || schemas.trackAssets !== TRACK_SCHEMA) throw new Error('manifest schema identities are unsupported')
  const trackAssets = artifact(item.trackAssets, 'manifest.trackAssets')
  if (trackAssets.schemaId !== TRACK_SCHEMA) throw new Error('track asset schema identity is unsupported')
  const chunks = array(item.chunks, 'manifest.chunks').map(parseChunkReference)
  const drivers = array(item.drivers, 'manifest.drivers').map(parseDriver)
  const lapStarts = item.lapStarts === undefined ? undefined : array(item.lapStarts, 'manifest.lapStarts').map(parseLapStart)
  if (!chunks.length || !drivers.length || new Set(drivers.map(({ id }) => id)).size !== drivers.length) throw new Error('manifest requires chunks and unique drivers')
  chunks.forEach((chunk, index) => {
    if (chunk.schemaId !== CHUNK_SCHEMA || chunk.sequence !== index + 1 || (index > 0 && chunks[index - 1].endMs !== chunk.startMs)) throw new Error('manifest chunk references are invalid')
  })
  if (item.formatVersion !== undefined && item.formatVersion !== 'browser-delivery-v1') throw new Error('Unsupported manifest format version')
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
  return freeze({ contractVersion: 'v1', fixtureId, fixtureName: string(item.fixtureName, 'manifest.fixtureName'), schemas: freeze({ manifest: MANIFEST_SCHEMA, chunk: CHUNK_SCHEMA, trackAssets: TRACK_SCHEMA }), trackAssets, chunks, drivers, ...(lapStarts === undefined ? {} : { lapStarts: freeze(lapStarts) }), ...(item.description === undefined ? {} : { description: item.description as string }), ...(item.formatVersion === undefined ? {} : { formatVersion: item.formatVersion }), ...(item.deliveryVersion === undefined ? {} : { deliveryVersion: item.deliveryVersion as string }), ...(item.sourceGenerationId === undefined ? {} : { sourceGenerationId: item.sourceGenerationId as string }), ...(item.sourceManifestSha256 === undefined ? {} : { sourceManifestSha256: item.sourceManifestSha256 as string }), ...(golden ? { goldenSnapshots: freeze({ path: 'golden-snapshots.json' as const }) } : {}), ...(item.createdAt === undefined ? {} : { createdAt: item.createdAt as string }) })
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
  if (endMs <= startMs) throw new Error('chunk reference interval is invalid')
  return freeze({ ...ref, sequence: integer(item.sequence, 'chunk sequence', 1), startMs, endMs, overlapWithPreviousMs: integer(item.overlapWithPreviousMs, 'chunk overlap') })
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
  if (item.contractVersion !== 'v1') throw new Error('track assets must be contract version v1')
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
  return freeze({ contractVersion: 'v1', fixtureId, trackId, trackName: string(item.trackName, 'track name'), coordinateSpace: freeze({ units: 'meters' as const, origin: string(space.origin, 'track origin') }), circuitLengthMeters: length, rotationDegrees: finite(item.rotationDegrees, 'track rotation'), startFinish: freeze({ center: parsePoint(finish.center, 'start finish center'), inner: parsePoint(finish.inner, 'start finish inner'), outer: parsePoint(finish.outer, 'start finish outer') }), centerLine: line(item.centerLine, 'center line'), innerBoundary: line(item.innerBoundary, 'inner boundary'), outerBoundary: line(item.outerBoundary, 'outer boundary'), ...(markers ? { distanceMarkersMeters: freeze(markers) } : {}), ...(zones ? { drsZones: freeze(zones) } : {}) })
}

function parsePoint(raw: unknown, label: string): TrackPoint { const item = object(raw, label); exact(item, ['x', 'y'], [], label); return freeze({ x: finite(item.x, `${label}.x`), y: finite(item.y, `${label}.y`) }) }

export function parseChunk(value: unknown): ReplayChunk {
  const item = object(value, 'chunk')
  exact(item, ['contractVersion', 'fixtureId', 'chunkId', 'sequence', 'startMs', 'endMs', 'overlap', 'timeMs', 'authoritativeStartIndex', 'drivers', 'leaderboardOrder', 'trackStatusCode', 'weatherState', 'events'], [], 'chunk')
  if (item.contractVersion !== 'v1') throw new Error('chunk must be contract version v1')
  const timeMs = array(item.timeMs, 'chunk.timeMs').map((time, index) => integer(time, `timeMs[${index}]`))
  if (!timeMs.length || timeMs.some((time, index) => index > 0 && time <= timeMs[index - 1])) throw new Error('chunk timeline must be non-empty, sorted, and unique')
  const rawDrivers = object(item.drivers, 'chunk.drivers'); const drivers = freeze(Object.fromEntries(Object.entries(rawDrivers).map(([id, columns]) => [id, parseColumns(columns, timeMs.length, id)])))
  const leaderboardOrder = freeze(array(item.leaderboardOrder, 'leaderboard').map((row, index) => nullable(row, (entry) => { const values = array(entry, `leaderboard[${index}]`).map((id) => string(id, 'leaderboard driver')); if (!values.length || new Set(values).size !== values.length) throw new Error('leaderboard row is invalid'); return freeze(values) })))
  const trackStatusCode = freeze(array(item.trackStatusCode, 'track status').map((entry) => nullable(entry, (value) => integer(value, 'track status'))))
  const weatherState = freeze(array(item.weatherState, 'weather').map((entry) => nullable(entry, (value) => string(value, 'weather state'))))
  if ([leaderboardOrder, trackStatusCode, weatherState].some((column) => column.length !== timeMs.length)) throw new Error('chunk global columns are not aligned')
  const fixtureId = string(item.fixtureId, 'chunk fixture id'); if (!FIXTURE_ID.test(fixtureId)) throw new Error('chunk fixture ID is invalid')
  validateDerivedFields(drivers, leaderboardOrder)
  return freeze({ contractVersion: 'v1', fixtureId, chunkId: string(item.chunkId, 'chunk id'), sequence: integer(item.sequence, 'chunk sequence', 1), startMs: integer(item.startMs, 'chunk start'), endMs: integer(item.endMs, 'chunk end'), overlap: parseOverlap(item.overlap), timeMs, authoritativeStartIndex: integer(item.authoritativeStartIndex, 'chunk authoritative index'), drivers, leaderboardOrder, trackStatusCode, weatherState, events: freeze(array(item.events, 'events').map(parseEvent)) })
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
  const driverId = item.driverId === undefined ? undefined : nullable(item.driverId, (value) => string(value, 'event driver'))
  return freeze({ sessionTimeMs: integer(item.sessionTimeMs, 'event time'), eventType: string(item.eventType, 'event type'), description: string(item.description, 'event description'), ...(driverId === undefined ? {} : { driverId }), ...(item.payload === undefined ? {} : { payload: jsonObject(item.payload, 'event payload') }) })
}

function parseColumns(value: unknown, length: number, label: string): DriverColumns {
  const columns = object(value, `driver ${label}`); exact(columns, DRIVER_FIELDS, [], `driver ${label}`)
  const numberColumn = (field: string) => parseColumn(columns[field], length, `${label}.${field}`, (entry) => nullable(entry, (value) => finite(value, field)))
  const integerColumn = (field: string, min: number, max = Number.MAX_SAFE_INTEGER) => parseColumn(columns[field], length, `${label}.${field}`, (entry) => nullable(entry, (value) => integer(value, field, min, max)))
  const stringColumn = (field: string) => parseColumn(columns[field], length, `${label}.${field}`, (entry) => nullable(entry, (value) => string(value, field)))
  const nonNegativeNumberColumn = (field: string) => parseColumn(columns[field], length, `${label}.${field}`, (entry) => nullable(entry, (value) => { const parsed = finite(value, field); if (parsed < 0) throw new Error(`${field} must be non-negative`); return parsed }))
  return freeze({ x: numberColumn('x'), y: numberColumn('y'), trackDistanceMeters: nonNegativeNumberColumn('trackDistanceMeters'), speed: numberColumn('speed'), throttle: numberColumn('throttle'), brake: numberColumn('brake'), gapToLeaderMs: nonNegativeNumberColumn('gapToLeaderMs'), lap: integerColumn('lap', 1), position: integerColumn('position', 1), gear: integerColumn('gear', 0, 8), drs: integerColumn('drs', 0), tyreCompound: stringColumn('tyreCompound'), status: stringColumn('status'), isInPitLane: parseColumn(columns.isInPitLane, length, `${label}.isInPitLane`, (entry) => nullable(entry, (value) => { if (typeof value !== 'boolean') throw new Error('pit state must be boolean'); return value })) })
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
