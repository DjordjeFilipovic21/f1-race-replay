import { verifyDigest } from './digest'
import { parseChunk, parseLapSectorSidecar, parseManifest, parsePenaltySidecar, parsePitLossEstimateSidecar, parsePitLossModel, parsePointer, parseQualifyingLapStatus, parseQualifyingSummary, parseQualifyingTimeline, parseStintSummary, parseTimelineSummary, parseTrackAssets, validateQualifyingLikeLapSectorSidecar } from './guards'
import { assertSafeRelativePath, readJson, resolveRelativePath } from './source'
import { parseWeatherSidecar, parseWeatherSidecarReference } from './guards'
import type { ChunkReference, PitLossEstimateSidecar, QualifyingTimeline, ReplayChunk, ReplayData, ReplayIndex, ReplayManifest, ReplaySource, TimelineSummary, TrackAssets, WeatherSidecar } from './types'

export interface LoadReplayDataOptions {
  readonly source: ReplaySource
  readonly pointerPath?: string
  readonly manifestPath?: string
}

export async function loadReplayIndex(options: LoadReplayDataOptions): Promise<ReplayIndex> {
  if (options.pointerPath && options.manifestPath) throw new Error('Specify a pointer or manifest path, not both')
  const pointer = options.pointerPath ? await loadPointer(options.source, options.pointerPath) : undefined
  const manifestPath = assertSafeRelativePath(pointer?.manifestPath ?? options.manifestPath ?? 'manifest.json')
  const manifestBytes = await options.source.read(manifestPath)
  if (pointer) await verifyDigest(manifestBytes, pointer.manifestSha256)
  const manifest = parseManifestWithOptionalWeather(decodeJson(manifestBytes, manifestPath))
  if (pointer && manifest.deliveryVersion !== pointer.deliveryVersion) throw new Error('Pointer and manifest delivery identities disagree')
  const trackPath = resolveRelativePath(manifestPath, manifest.trackAssets.path)
  const trackBytes = await options.source.read(trackPath)
  if (manifest.trackAssets.sha256) await verifyDigest(trackBytes, manifest.trackAssets.sha256)
  const trackAssets = parseTrackAssets(decodeJson(trackBytes, trackPath))
  if (trackAssets.fixtureId !== manifest.fixtureId) throw new Error('Track assets and manifest fixture identities disagree')
  const [timelineSummary, lapSectorSidecar, stintSummary, pitLossModel, pitLossEstimateSidecar, penaltySidecar, qualifyingSummary, qualifyingLapStatus, qualifyingTimeline, weatherSidecar] = await Promise.all([
    manifest.timelineSummary === undefined
      ? Promise.resolve(undefined)
      : loadTimelineSummary(options.source, manifestPath, manifest),
    loadOptionalSidecar(options.source, manifestPath, manifest.lapSectorSidecar, parseLapSectorSidecar, (sidecar) => {
      validateSidecarIdentity(manifest, sidecar, 'Lap sector sidecar')
      validateSidecarDrivers(manifest, sidecar, 'Lap sector sidecar')
      if (manifest.sessionMode === 'qualifying' || manifest.sessionMode === 'sprint-qualifying' || manifest.sessionMode === 'sprint-shootout') {
        validateQualifyingLikeLapSectorSidecar(sidecar)
      }
    }),
    loadOptionalSidecar(options.source, manifestPath, manifest.stintSummary, parseStintSummary, (summary) => {
      validateSidecarIdentity(manifest, summary, 'Stint summary')
      validateSidecarDrivers(manifest, summary, 'Stint summary')
    }),
    loadOptionalSidecar(options.source, manifestPath, manifest.pitLossModel, parsePitLossModel, (model) => {
      validateSidecarIdentity(manifest, model, 'Pit loss model')
    }),
    loadOptionalSidecar(options.source, manifestPath, manifest.pitLossEstimateSidecar, parsePitLossEstimateSidecar, (sidecar) => {
      validatePitLossEstimateSidecar(manifest, trackAssets, sidecar)
    }),
    loadOptionalSidecar(options.source, manifestPath, manifest.penaltySidecar, parsePenaltySidecar, (sidecar) => {
      validateSidecarIdentity(manifest, sidecar, 'Penalty sidecar')
      validatePenaltyDrivers(manifest, sidecar.penaltyIssuances)
    }),
    loadOptionalSidecar(options.source, manifestPath, manifest.qualifyingSummary, parseQualifyingSummary, (summary) => {
      validateSidecarIdentity(manifest, summary, 'Qualifying summary')
      validateSidecarDrivers(manifest, summary, 'Qualifying summary')
    }),
    loadOptionalSidecar(options.source, manifestPath, manifest.qualifyingLapStatus, parseQualifyingLapStatus, (sidecar) => {
      validateSidecarIdentity(manifest, sidecar, 'Qualifying lap status')
      validateSidecarDrivers(manifest, sidecar, 'Qualifying lap status')
    }),
    loadOptionalSidecar(options.source, manifestPath, manifest.qualifyingTimeline, parseQualifyingTimeline, (timeline) => {
      validateSidecarIdentity(manifest, timeline, 'Qualifying timeline')
      validateQualifyingTimelineBounds(manifest, timeline)
      validateQualifyingTimelineDrivers(manifest, timeline)
    }),
    loadOptionalWeatherSidecar(options.source, manifestPath, manifest.weatherSidecar, manifest),
  ])

  const loadChunk = async (sequence: number): Promise<ReplayChunk> => {
    const reference = manifest.chunks[sequence - 1]
    if (!reference || reference.sequence !== sequence) throw new Error(`Unknown chunk sequence: ${sequence}`)
    const path = resolveRelativePath(manifestPath, reference.path)
    const bytes = await options.source.read(path)
    if (reference.sha256) await verifyDigest(bytes, reference.sha256)
    const chunk = parseChunk(decodeJson(bytes, path))
    validateChunk(manifest, reference, chunk, sequence - 1)
    return chunk
  }
  const loadAllChunks = async (concurrency = 4): Promise<readonly ReplayChunk[]> => {
    if (!Number.isSafeInteger(concurrency) || concurrency < 1) throw new Error('Chunk concurrency must be a positive integer')
    const chunks = await mapBounded(manifest.chunks.map(({ sequence }) => sequence), concurrency, loadChunk)
    validateBundle(manifest, chunks)
    return Object.freeze(chunks)
  }
  return Object.freeze({
    ...(pointer ? { pointer } : {}),
    manifest,
    trackAssets,
    ...(manifest.seasonMetadata === undefined ? {} : { seasonMetadata: manifest.seasonMetadata }),
    ...(manifest.telemetryCapabilities === undefined ? {} : { telemetryCapabilities: manifest.telemetryCapabilities }),
    ...(timelineSummary === undefined ? {} : { timelineSummary }),
    ...(lapSectorSidecar === undefined ? {} : { lapSectorSidecar }),
    ...(stintSummary === undefined ? {} : { stintSummary }),
    ...(pitLossModel === undefined ? {} : { pitLossModel }),
    ...(pitLossEstimateSidecar === undefined ? {} : { pitLossEstimateSidecar }),
    ...(penaltySidecar === undefined ? {} : { penaltySidecar }),
    ...(qualifyingSummary === undefined ? {} : { qualifyingSummary }),
    ...(qualifyingLapStatus === undefined ? {} : { qualifyingLapStatus }),
    ...(qualifyingTimeline === undefined ? {} : { qualifyingTimeline }),
    ...(weatherSidecar === undefined ? {} : { weatherSidecar }),
    loadChunk,
    loadAllChunks,
  })
}

export async function loadReplayData(options: LoadReplayDataOptions): Promise<ReplayData> {
  const index = await loadReplayIndex(options)
  const chunks = await index.loadAllChunks()
  return Object.freeze({
    ...(index.pointer ? { pointer: index.pointer } : {}),
    manifest: index.manifest,
    trackAssets: index.trackAssets,
    ...(index.seasonMetadata === undefined ? {} : { seasonMetadata: index.seasonMetadata }),
    ...(index.telemetryCapabilities === undefined ? {} : { telemetryCapabilities: index.telemetryCapabilities }),
    ...(index.timelineSummary === undefined ? {} : { timelineSummary: index.timelineSummary }),
    ...(index.lapSectorSidecar === undefined ? {} : { lapSectorSidecar: index.lapSectorSidecar }),
    ...(index.stintSummary === undefined ? {} : { stintSummary: index.stintSummary }),
    ...(index.pitLossModel === undefined ? {} : { pitLossModel: index.pitLossModel }),
    ...(index.pitLossEstimateSidecar === undefined ? {} : { pitLossEstimateSidecar: index.pitLossEstimateSidecar }),
    ...(index.penaltySidecar === undefined ? {} : { penaltySidecar: index.penaltySidecar }),
    ...(index.qualifyingSummary === undefined ? {} : { qualifyingSummary: index.qualifyingSummary }),
    ...(index.qualifyingLapStatus === undefined ? {} : { qualifyingLapStatus: index.qualifyingLapStatus }),
    ...(index.qualifyingTimeline === undefined ? {} : { qualifyingTimeline: index.qualifyingTimeline }),
    ...(index.weatherSidecar === undefined ? {} : { weatherSidecar: index.weatherSidecar }),
    chunks,
  })
}

async function loadOptionalSidecar<T extends { readonly fixtureId: string }>(
  source: ReplaySource,
  manifestPath: string,
  reference: { readonly path: string; readonly sha256: string } | undefined,
  parse: (value: unknown) => T,
  validate: (value: T) => void,
): Promise<T | undefined> {
  if (reference === undefined) return undefined
  const path = resolveRelativePath(manifestPath, reference.path)
  const bytes = await source.read(path)
  await verifyDigest(bytes, reference.sha256)
  const sidecar = parse(decodeJson(bytes, path))
  validate(sidecar)
  return sidecar
}

/**
 * Weather is an optional enhancement to an otherwise usable replay. Its
 * payload boundary is fail-closed, unlike the existing required sidecars.
 */
async function loadOptionalWeatherSidecar(
  source: ReplaySource,
  manifestPath: string,
  reference: ReplayManifest['weatherSidecar'],
  manifest: ReplayManifest,
): Promise<WeatherSidecar | undefined> {
  if (reference === undefined) return undefined
  try {
    return await loadOptionalSidecar(source, manifestPath, reference, parseWeatherSidecar, (sidecar) => {
      validateSidecarIdentity(manifest, sidecar, 'Weather sidecar')
    })
  } catch {
    return undefined
  }
}

/**
 * Keep malformed weather references from turning an otherwise valid manifest
 * into a replay outage. Core manifest validation still runs strictly after the
 * optional field is removed; other sidecar references retain their strictness.
 */
function parseManifestWithOptionalWeather(value: unknown): ReplayManifest {
  if (!isRecord(value) || !('weatherSidecar' in value)) return parseManifest(value)
  try {
    parseWeatherSidecarReference(value.weatherSidecar)
    return parseManifest(value)
  } catch (weatherManifestError) {
    const coreManifest = withoutOptionalWeather(value)
    try {
      return parseManifest(coreManifest)
    } catch {
      throw weatherManifestError
    }
  }
}

function withoutOptionalWeather(value: Record<string, unknown>): Record<string, unknown> {
  const coreManifest = { ...value }
  delete coreManifest.weatherSidecar
  if (isRecord(coreManifest.schemas)) {
    const schemas = { ...coreManifest.schemas }
    delete schemas.weatherSidecar
    coreManifest.schemas = schemas
  }
  return coreManifest
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function validateSidecarIdentity<T extends { readonly fixtureId: string }>(manifest: ReplayManifest, sidecar: T, label: string): void {
  if (sidecar.fixtureId !== manifest.fixtureId) throw new Error(`${label} and manifest fixture identities disagree`)
}

function validatePitLossEstimateSidecar(manifest: ReplayManifest, trackAssets: TrackAssets, sidecar: PitLossEstimateSidecar): void {
  validateSidecarIdentity(manifest, sidecar, 'Pit loss estimate sidecar')
  if (sidecar.trackId !== trackAssets.trackId) throw new Error('Pit loss estimate sidecar and track asset track identities disagree')
}

function validateSidecarDrivers<T extends { readonly drivers: Readonly<Record<string, unknown>> }>(manifest: ReplayManifest, sidecar: T, label: string): void {
  const expected = new Set(manifest.drivers.map(({ id }) => id))
  const actual = Object.keys(sidecar.drivers)
  if (actual.length !== expected.size || actual.some((driverId) => !expected.has(driverId))) throw new Error(`${label} drivers disagree with manifest`)
}

function validatePenaltyDrivers(manifest: ReplayManifest, penalties: readonly { readonly driverId: string }[]): void {
  const expected = new Set(manifest.drivers.map(({ id }) => id))
  if (penalties.some(({ driverId }) => !expected.has(driverId))) throw new Error('Penalty sidecar drivers disagree with manifest')
}

async function loadTimelineSummary(source: ReplaySource, manifestPath: string, manifest: ReplayManifest): Promise<TimelineSummary> {
  const reference = manifest.timelineSummary
  if (reference === undefined) throw new Error('Timeline summary reference is missing')
  const path = resolveRelativePath(manifestPath, reference.path)
  const bytes = await source.read(path)
  await verifyDigest(bytes, reference.sha256)
  const summary = parseTimelineSummary(decodeJson(bytes, path))
  validateTimelineSummary(manifest, summary)
  return summary
}

function validateTimelineSummary(manifest: ReplayManifest, summary: TimelineSummary): void {
  if (summary.fixtureId !== manifest.fixtureId) throw new Error('Timeline summary and manifest fixture identities disagree')
  const startMs = manifest.chunks[0].startMs
  const endMs = manifest.chunks[manifest.chunks.length - 1].endMs
  if (summary.startMs !== startMs || summary.endMs !== endMs) throw new Error('Timeline summary bounds disagree with manifest')
  const driverIds = new Set(manifest.drivers.map(({ id }) => id))
  if (summary.dnfMarkers.some(({ driverId }) => !driverIds.has(driverId))) throw new Error('Timeline summary drivers disagree with manifest')
}

function validateQualifyingTimelineBounds(manifest: ReplayManifest, timeline: QualifyingTimeline): void {
  const startMs = manifest.chunks[0].startMs
  const endMs = manifest.chunks[manifest.chunks.length - 1].endMs
  if (timeline.startMs !== startMs || timeline.endMs !== endMs) throw new Error('Qualifying timeline bounds disagree with manifest')
}

function validateQualifyingTimelineDrivers(manifest: ReplayManifest, timeline: QualifyingTimeline): void {
  const driverIds = new Set(manifest.drivers.map(({ id }) => id))
  if (timeline.incidentMarkers.some(({ driverId }) => !driverIds.has(driverId))) throw new Error('Qualifying timeline drivers disagree with manifest')
}

async function loadPointer(source: ReplaySource, path: string) { return parsePointer(await readJson(source, path)) }

function decodeJson(bytes: Uint8Array, path: string): unknown {
  try { return JSON.parse(new TextDecoder().decode(bytes)) as unknown }
  catch (error) { throw new Error(`Replay-data JSON is invalid at ${path}`, { cause: error }) }
}

async function mapBounded<T, R>(values: readonly T[], concurrency: number, transform: (value: T) => Promise<R>): Promise<R[]> {
  const results = new Array<R>(values.length); let next = 0
  const worker = async () => { while (next < values.length) { const index = next++; results[index] = await transform(values[index]) } }
  await Promise.all(Array.from({ length: Math.min(concurrency, values.length) }, worker))
  return results
}

function validateChunk(manifest: ReplayManifest, reference: ChunkReference, chunk: ReplayChunk, index: number): void {
  const driverIds = new Set(manifest.drivers.map(({ id }) => id))
  if (chunk.fixtureId !== manifest.fixtureId || chunk.sequence !== reference.sequence || chunk.chunkId !== `chunk-${reference.sequence.toString().padStart(3, '0')}` || chunk.startMs !== reference.startMs || chunk.endMs !== reference.endMs) throw new Error('Chunk identity disagrees with its manifest reference')
  if (chunk.authoritativeStartIndex >= chunk.timeMs.length) throw new Error('Chunk authority is invalid')
  if (chunk.timeMs.slice(0, chunk.authoritativeStartIndex).some((time) => time >= chunk.startMs) || chunk.timeMs.slice(chunk.authoritativeStartIndex).some((time) => time < chunk.startMs || time >= chunk.endMs)) throw new Error('Chunk ownership is invalid')
  if (new Set(Object.keys(chunk.drivers)).size !== driverIds.size || Object.keys(chunk.drivers).some((id) => !driverIds.has(id))) throw new Error('Chunk drivers disagree with manifest')
  for (const row of chunk.leaderboardOrder) if (row && row.some((id) => !driverIds.has(id))) throw new Error('Leaderboard drivers disagree with manifest')
  if (chunk.events.some((event) => event.sessionTimeMs < chunk.startMs || event.sessionTimeMs >= chunk.endMs || (event.driverId != null && !driverIds.has(event.driverId)))) throw new Error('Chunk events are invalid')
  if (index === 0) { if (chunk.overlap.kind !== 'none' || reference.overlapWithPreviousMs !== 0) throw new Error('First chunk overlap is invalid'); return }
  const previousReference = manifest.chunks[index - 1]
  if (chunk.overlap.kind !== 'handoff') throw new Error('Chunk handoff is invalid')
  const overlap = chunk.overlap
  if (overlap.previousChunkPath !== previousReference.path || overlap.authoritativeFromMs !== chunk.startMs || overlap.range.endMs !== chunk.startMs || overlap.range.endMs - overlap.range.startMs !== reference.overlapWithPreviousMs) throw new Error('Chunk handoff is invalid')
  if (chunk.timeMs.slice(0, chunk.authoritativeStartIndex).some((time) => time < overlap.range.startMs || time >= overlap.range.endMs)) throw new Error('Chunk overlap samples are outside the declared range')
}

function validateBundle(manifest: ReplayManifest, chunks: readonly ReplayChunk[]): void {
  chunks.forEach((chunk, index) => {
    validateChunk(manifest, manifest.chunks[index], chunk, index)
    if (index === 0) return
    const previous = chunks[index - 1]
    chunk.timeMs.slice(0, chunk.authoritativeStartIndex).forEach((time, overlapIndex) => {
      const previousIndex = previous.timeMs.indexOf(time)
      if (previousIndex < previous.authoritativeStartIndex || JSON.stringify(sampleAt(previous, previousIndex)) !== JSON.stringify(sampleAt(chunk, overlapIndex))) throw new Error('Chunk overlap sample disagrees with its authoritative predecessor')
    })
  })
}

function sampleAt(chunk: ReplayChunk, index: number): unknown {
  return {
    drivers: Object.fromEntries(Object.entries(chunk.drivers).map(([id, columns]) => [id, Object.fromEntries(Object.entries(columns).map(([field, values]) => [field, values[index]]))])),
    leaderboardOrder: chunk.leaderboardOrder[index], trackStatusCode: chunk.trackStatusCode[index], weatherState: chunk.weatherState[index],
  }
}
