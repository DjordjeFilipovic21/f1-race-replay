import type { ReplayChunk } from '../../data/replay/types'
import type { AuthoritativeSample, AuthoritativeTimeline } from './types'

export function createAuthoritativeTimeline(chunks: readonly ReplayChunk[]): AuthoritativeTimeline {
  const owners = new Map<number, AuthoritativeSample>()
  const orderedChunks = chunks.map((chunk, chunkIndex) => ({ chunk, chunkIndex })).sort(byChunkSequence)

  for (const { chunk, chunkIndex } of orderedChunks) {
    for (let timeIndex = chunk.authoritativeStartIndex; timeIndex < chunk.timeMs.length; timeIndex += 1) {
      const timeMs = chunk.timeMs[timeIndex]
      if (!ownsTime(chunk, timeMs)) continue
      const candidate = Object.freeze({ timeMs, chunkIndex, timeIndex })
      const current = owners.get(timeMs)
      if (!current || hasLaterOwnership(candidate, current, chunks)) owners.set(timeMs, candidate)
    }
  }

  return Object.freeze({ samples: Object.freeze([...owners.values()].sort((left, right) => left.timeMs - right.timeMs)) })
}

export function resolveAuthoritativeSample(timeline: AuthoritativeTimeline, timeMs: number): AuthoritativeSample | null {
  const index = findFirstAtOrAfter(timeline.samples, timeMs)
  const sample = timeline.samples[index]
  return sample?.timeMs === timeMs ? sample : null
}

export function findFirstAtOrAfter<T extends { readonly timeMs: number }>(samples: readonly T[], timeMs: number): number {
  let low = 0
  let high = samples.length
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    if (samples[middle].timeMs < timeMs) low = middle + 1
    else high = middle
  }
  return low
}

function ownsTime(chunk: ReplayChunk, timeMs: number): boolean {
  return timeMs >= chunk.startMs && timeMs < chunk.endMs
}

function byChunkSequence(left: { readonly chunk: ReplayChunk }, right: { readonly chunk: ReplayChunk }): number {
  return left.chunk.sequence - right.chunk.sequence
}

function hasLaterOwnership(candidate: AuthoritativeSample, current: AuthoritativeSample, chunks: readonly ReplayChunk[]): boolean {
  const candidateChunk = chunks[candidate.chunkIndex]
  const currentChunk = chunks[current.chunkIndex]
  return candidateChunk.startMs > currentChunk.startMs || (
    candidateChunk.startMs === currentChunk.startMs && candidateChunk.sequence > currentChunk.sequence
  )
}
