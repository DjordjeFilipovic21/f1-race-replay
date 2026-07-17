import { verifyDigest } from './digest'
import { parseChunk, parseManifest, parsePointer, parseTrackAssets } from './guards'
import { assertSafeRelativePath, readJson, resolveRelativePath } from './source'
import type { ChunkReference, ReplayChunk, ReplayData, ReplayIndex, ReplayManifest, ReplaySource } from './types'

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
  const manifest = parseManifest(decodeJson(manifestBytes, manifestPath))
  if (pointer && manifest.deliveryVersion !== pointer.deliveryVersion) throw new Error('Pointer and manifest delivery identities disagree')
  const trackPath = resolveRelativePath(manifestPath, manifest.trackAssets.path)
  const trackBytes = await options.source.read(trackPath)
  if (manifest.trackAssets.sha256) await verifyDigest(trackBytes, manifest.trackAssets.sha256)
  const trackAssets = parseTrackAssets(decodeJson(trackBytes, trackPath))
  if (trackAssets.fixtureId !== manifest.fixtureId) throw new Error('Track assets and manifest fixture identities disagree')

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
  return Object.freeze({ ...(pointer ? { pointer } : {}), manifest, trackAssets, loadChunk, loadAllChunks })
}

export async function loadReplayData(options: LoadReplayDataOptions): Promise<ReplayData> {
  const index = await loadReplayIndex(options)
  const chunks = await index.loadAllChunks()
  return Object.freeze({ ...(index.pointer ? { pointer: index.pointer } : {}), manifest: index.manifest, trackAssets: index.trackAssets, chunks })
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
  for (const row of chunk.leaderboardOrder) if (row && (row.length !== driverIds.size || row.some((id) => !driverIds.has(id)))) throw new Error('Leaderboard drivers disagree with manifest')
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
