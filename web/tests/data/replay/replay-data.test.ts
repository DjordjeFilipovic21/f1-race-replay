import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'
import { sha256Hex } from '../../../src/data/replay/digest'
import { loadReplayData, loadReplayIndex } from '../../../src/data/replay/loader'
import { assertSafeRelativePath, resolveRelativePath } from '../../../src/data/replay/source'
import type { ReplaySource } from '../../../src/data/replay/types'

const fixtureRoot = resolve(import.meta.dirname, '../../../../contracts/replay-data/v1/fixtures/deterministic-race')
const fixtureSource: ReplaySource = { read: (path) => readFile(resolve(fixtureRoot, path)) }
const decoder = new TextDecoder()
const encoder = new TextEncoder()

describe('replay-data v1 loader', () => {
  test('loads a lazy index before reading chunks, then validates the complete fixture', async () => {
    const reads: string[] = []
    const source: ReplaySource = { read: async (path) => { reads.push(path); return fixtureSource.read(path) } }

    const index = await loadReplayIndex({ source })
    expect(reads).toEqual(['manifest.json', 'track-assets.json'])
    expect(index.trackAssets.distanceMarkersMeters).toEqual([0, 500, 1000, 1500, 2000, 2500, 3000])
    const chunks = await index.loadAllChunks(1)

    expect(chunks[1].timeMs[chunks[1].authoritativeStartIndex]).toBe(2000)
    expect(Object.isFrozen(index.manifest)).toBe(true)
    expect(Object.isFrozen(index.manifest.chunks)).toBe(true)
    expect(Object.isFrozen(index.trackAssets)).toBe(true)
    expect(Object.isFrozen(chunks[1])).toBe(true)
    expect(Object.isFrozen(chunks[1].drivers.HAM.x)).toBe(true)
    expect(Object.isFrozen(chunks[1].timeMs)).toBe(true)
    expect(Object.isFrozen(chunks[1].leaderboardOrder)).toBe(true)
    expect(Object.isFrozen(chunks[1].trackStatusCode)).toBe(true)
    expect(Object.isFrozen(chunks[1].weatherState)).toBe(true)
    expect(Object.isFrozen(chunks[1].events[0])).toBe(true)
    expect(Object.isFrozen(chunks[1].events[0].payload)).toBe(true)
    expect(() => { (chunks[1].timeMs as number[])[0] = -1 }).toThrow(TypeError)
    expect(() => { (chunks[1].events[0].payload as Record<string, unknown>).forPosition = 2 }).toThrow(TypeError)
  })

  test('loads the exact production pointer layout relative to its manifest', async () => {
    const { source, reads } = await publishedFixtureSource()

    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })
    const chunk = await index.loadChunk(1)

    expect(chunk.chunkId).toBe('chunk-001')
    expect(reads).toEqual([
      'browser-current.json', 'generations/demo/manifest.json',
      'generations/demo/track-assets.json', 'generations/demo/chunks/chunk-001.json',
    ])
  })

  test('verifies track asset digest before parsing', async () => {
    const { source } = await publishedFixtureSource({ corruptTrackDigest: true })
    await expect(loadReplayIndex({ source, pointerPath: 'browser-current.json' })).rejects.toThrow('digest does not match')
  })

  test('verifies chunk digest on success and rejects a mismatch', async () => {
    const valid = await publishedFixtureSource()
    await expect((await loadReplayIndex({ source: valid.source, pointerPath: 'browser-current.json' })).loadChunk(1)).resolves.toMatchObject({ chunkId: 'chunk-001' })
    const invalid = await publishedFixtureSource({ corruptChunkDigest: true })
    const index = await loadReplayIndex({ source: invalid.source, pointerPath: 'browser-current.json' })
    await expect(index.loadChunk(1)).rejects.toThrow('digest does not match')
  })

  test('rejects a misaligned driver column', async () => {
    const source = mutateFixture('chunks/chunk-001.json', (chunk) => {
      ;(chunk as { drivers: { HAM: { x: number[] } } }).drivers.HAM.x.pop()
    })
    await expect(loadReplayData({ source })).rejects.toThrow('not aligned to timeMs')
  })

  test('accepts legacy null-only derived fields', async () => {
    const source = mutateFixture('chunks/chunk-001.json', (chunk) => {
      const value = chunk as { drivers: Record<string, { trackDistanceMeters: null[]; gapToLeaderMs: null[]; position: null[] }>; leaderboardOrder: null[] }
      value.leaderboardOrder = [null, null, null]
      for (const driver of Object.values(value.drivers)) {
        driver.trackDistanceMeters = [null, null, null]; driver.gapToLeaderMs = [null, null, null]; driver.position = [null, null, null]
      }
    })
    const chunk = await (await loadReplayIndex({ source })).loadChunk(1)
    expect(chunk.drivers.HAM.rpm).toEqual([null, null, null])
  })

  test('accepts an optional nullable RPM column and preserves aligned values', async () => {
    const source = mutateFixtures({
      'chunks/chunk-001.json': (chunk) => {
        const value = chunk as { drivers: Record<string, { rpm?: unknown[] }> }
        value.drivers.HAM.rpm = [11_000, null, 0]
        value.drivers.RUS.rpm = [null, 10_500, null]
      },
      'chunks/chunk-002.json': (chunk) => {
        const value = chunk as { drivers: Record<string, { rpm?: unknown[] }> }
        value.drivers.HAM.rpm = [0, 12_000, 12_500]
        value.drivers.RUS.rpm = [null, 11_000, 10_500]
      },
    })

    const replay = await loadReplayData({ source })
    expect(replay.chunks[0].drivers.HAM.rpm).toEqual([11_000, null, 0])
    expect(replay.chunks[0].drivers.RUS.rpm).toEqual([null, 10_500, null])
  })

  test('rejects an RPM column with non-finite values', async () => {
    const source = mutateFixture('chunks/chunk-001.json', (chunk) => {
      const value = chunk as { drivers: Record<string, { rpm?: unknown[] }> }
      value.drivers.HAM.rpm = [11_000, 'missing', 0]
    })
    await expect(loadReplayData({ source })).rejects.toThrow('rpm must be finite')
  })

  test('accepts a valid partial dynamic leaderboard and still rejects duplicate IDs', async () => {
    const partial = mutateFixture('chunks/chunk-001.json', (chunk) => {
      const value = chunk as {
        drivers: { RUS: { gapToLeaderMs: Array<number | null>; position: Array<number | null> } }
        leaderboardOrder: string[][]
      }
      value.leaderboardOrder[0] = ['HAM']
      value.drivers.RUS.position[0] = null
      value.drivers.RUS.gapToLeaderMs[0] = null
    })
    await expect(loadReplayData({ source: partial })).resolves.toBeDefined()

    const duplicate = mutateFixture('chunks/chunk-001.json', (chunk) => {
      ;(chunk as { leaderboardOrder: string[][] }).leaderboardOrder[0] = ['HAM', 'HAM']
    })
    await expect(loadReplayData({ source: duplicate })).rejects.toThrow('leaderboard row is invalid')
  })

  test('rejects populated position and leaderboard disagreement', async () => {
    const source = mutateFixture('chunks/chunk-001.json', (chunk) => {
      ;(chunk as { leaderboardOrder: string[][] }).leaderboardOrder[0] = ['RUS', 'HAM']
    })
    await expect(loadReplayData({ source })).rejects.toThrow('Leaderboard order disagrees')
  })

  test('rejects duplicate positions and a nonzero leader gap', async () => {
    const duplicate = mutateFixture('chunks/chunk-001.json', (chunk) => {
      ;(chunk as { drivers: { RUS: { position: number[] } } }).drivers.RUS.position[0] = 1
    })
    await expect(loadReplayData({ source: duplicate })).rejects.toThrow('Positions must be unique consecutive')
    const leaderGap = mutateFixture('chunks/chunk-001.json', (chunk) => {
      ;(chunk as { drivers: { HAM: { gapToLeaderMs: number[] } } }).drivers.HAM.gapToLeaderMs[0] = 1
    })
    await expect(loadReplayData({ source: leaderGap })).rejects.toThrow('Leader gap must be zero')
  })

  test('rejects a typed column with the wrong value domain', async () => {
    const source = mutateFixture('chunks/chunk-001.json', (chunk) => {
      ;(chunk as { drivers: { HAM: { gear: unknown[] } } }).drivers.HAM.gear[0] = 'eight'
    })
    await expect(loadReplayData({ source })).rejects.toThrow('gear must be an integer')
  })

  test('rejects duplicate manifest drivers', async () => {
    const source = mutateFixture('manifest.json', (manifest) => {
      const value = manifest as { drivers: unknown[] }; value.drivers.push(value.drivers[0])
    })
    await expect(loadReplayIndex({ source })).rejects.toThrow('unique drivers')
  })

  test('rejects an unsupported artifact-reference schema identity and malformed optional metadata', async () => {
    const schemaSource = mutateFixture('manifest.json', (manifest) => {
      ;(manifest as { trackAssets: { schemaId: string } }).trackAssets.schemaId = 'urn:unsupported'
    })
    await expect(loadReplayIndex({ source: schemaSource })).rejects.toThrow('track asset schema identity is unsupported')
    const metadataSource = mutateFixture('manifest.json', (manifest) => {
      ;(manifest as { sourceManifestSha256?: unknown }).sourceManifestSha256 = 'invalid'
    })
    await expect(loadReplayIndex({ source: metadataSource })).rejects.toThrow('sourceManifestSha256 is invalid')
  })

  test('accepts frozen optional lap navigation metadata and rejects malformed order', async () => {
    const valid = mutateFixture('manifest.json', (manifest) => {
      ;(manifest as { lapStarts?: unknown }).lapStarts = [{ lap: 1, startMs: 0 }, { lap: 3, startMs: 2_000 }]
    })
    const index = await loadReplayIndex({ source: valid })
    expect(index.manifest.lapStarts).toEqual([{ lap: 1, startMs: 0 }, { lap: 3, startMs: 2_000 }])
    expect(Object.isFrozen(index.manifest.lapStarts)).toBe(true)
    const malformed = mutateFixture('manifest.json', (manifest) => {
      ;(manifest as { lapStarts?: unknown }).lapStarts = [{ lap: 2, startMs: 2_000 }, { lap: 1, startMs: 3_000 }]
    })
    await expect(loadReplayIndex({ source: malformed })).rejects.toThrow('lapStarts must be ordered')
  })

  test('rejects lap navigation markers outside replay bounds', async () => {
    const beforeStart = mutateFixture('manifest.json', (manifest) => {
      const value = manifest as { chunks: Array<{ startMs: number }>; lapStarts?: unknown }
      value.chunks[0].startMs = 1
      value.lapStarts = [{ lap: 1, startMs: 0 }]
    })
    await expect(loadReplayIndex({ source: beforeStart })).rejects.toThrow('lapStarts must be within replay bounds')

    const atEnd = mutateFixture('manifest.json', (manifest) => {
      const value = manifest as { chunks: Array<{ endMs: number }>; lapStarts?: unknown }
      value.lapStarts = [{ lap: 1, startMs: value.chunks[value.chunks.length - 1].endMs }]
    })
    await expect(loadReplayIndex({ source: atEnd })).rejects.toThrow('lapStarts must be within replay bounds')
  })

  test('rejects an incomplete handoff overlap', async () => {
    const source = mutateFixture('chunks/chunk-002.json', (chunk) => {
      ;(chunk as { overlap: { range: null } }).overlap.range = null
    })
    await expect(loadReplayData({ source })).rejects.toThrow('overlap range must be an object')
  })

  test('rejects event and leaderboard identities outside their accepted domains', async () => {
    const source = mutateFixture('chunks/chunk-001.json', (chunk) => {
      const value = chunk as { leaderboardOrder: string[][] }; value.leaderboardOrder[0][0] = 'BAD'
    })
    await expect(loadReplayData({ source })).rejects.toThrow('Leaderboard drivers disagree')
    const eventSource = mutateFixture('chunks/chunk-002.json', (chunk) => {
      const value = chunk as { events: Array<{ driverId: string }> }; value.events[0].driverId = ''
    })
    await expect(loadReplayData({ source: eventSource })).rejects.toThrow('event driver must be a non-empty string')
  })

  test('rejects handoff duration and duplicate overlap sample disagreements', async () => {
    const durationSource = mutateFixture('manifest.json', (manifest) => {
      const value = manifest as { chunks: Array<{ overlapWithPreviousMs: number }> }; value.chunks[1].overlapWithPreviousMs += 1
    })
    await expect(loadReplayData({ source: durationSource })).rejects.toThrow('Chunk handoff is invalid')
    const sampleSource = mutateFixture('chunks/chunk-002.json', (chunk) => {
      const value = chunk as { drivers: { HAM: { x: Array<number | null> } } }; value.drivers.HAM.x[0] = 999
    })
    await expect(loadReplayData({ source: sampleSource })).rejects.toThrow('overlap sample disagrees')
  })

  test('preserves representative nullable fields', async () => {
    const source = mutateFixture('chunks/chunk-001.json', (chunk) => {
      const fields = (chunk as { drivers: { HAM: { x: Array<number | null>; isInPitLane: Array<boolean | null> } } }).drivers.HAM
      fields.x[0] = null
      fields.isInPitLane[0] = null
    })
    const replay = await loadReplayData({ source })
    expect(replay.chunks[0].drivers.HAM.x[0]).toBeNull()
    expect(replay.chunks[0].drivers.HAM.isInPitLane[0]).toBeNull()
  })

  test('rejects a pointer whose manifest digest does not match', async () => {
    const { files } = await publishedFixtureSource()
    const pointer = JSON.parse(decoder.decode(files.get('browser-current.json'))) as { manifestSha256: string }
    pointer.manifestSha256 = `${pointer.manifestSha256[0] === '0' ? '1' : '0'}${pointer.manifestSha256.slice(1)}`
    files.set('browser-current.json', encoder.encode(JSON.stringify(pointer)))
    await expect(loadReplayIndex({ source: mapSource(files), pointerPath: 'browser-current.json' })).rejects.toThrow('digest does not match')
  })

  test('rejects unsafe paths and resolves safe manifest-relative paths', () => {
    expect(() => assertSafeRelativePath('../manifest.json')).toThrow('Unsafe replay-data path')
    expect(resolveRelativePath('generations/demo/manifest.json', 'chunks/chunk-001.json')).toBe('generations/demo/chunks/chunk-001.json')
  })
})

function mutateFixture(target: string, mutate: (value: unknown) => void): ReplaySource {
  return {
    async read(path) {
      const bytes = await fixtureSource.read(path)
      if (path !== target) return bytes
      const value = JSON.parse(decoder.decode(bytes)) as unknown
      mutate(value)
      return encoder.encode(JSON.stringify(value))
    },
  }
}

function mutateFixtures(mutations: Readonly<Record<string, (value: unknown) => void>>): ReplaySource {
  return {
    async read(path) {
      const bytes = await fixtureSource.read(path)
      const mutate = mutations[path]
      if (!mutate) return bytes
      const value = JSON.parse(decoder.decode(bytes)) as unknown
      mutate(value)
      return encoder.encode(JSON.stringify(value))
    },
  }
}

async function publishedFixtureSource(options: { corruptTrackDigest?: boolean; corruptChunkDigest?: boolean } = {}) {
  const manifest = JSON.parse(decoder.decode(await fixtureSource.read('manifest.json'))) as Record<string, unknown>
  const track = await fixtureSource.read('track-assets.json')
  const chunkOne = await fixtureSource.read('chunks/chunk-001.json')
  const chunkTwo = await fixtureSource.read('chunks/chunk-002.json')
  const trackReference = manifest.trackAssets as Record<string, unknown>
  trackReference.sha256 = options.corruptTrackDigest ? '0'.repeat(64) : await sha256Hex(track)
  const chunkReferences = manifest.chunks as Array<Record<string, unknown>>
  chunkReferences[0].sha256 = options.corruptChunkDigest ? '0'.repeat(64) : await sha256Hex(chunkOne)
  chunkReferences[1].sha256 = await sha256Hex(chunkTwo)
  manifest.formatVersion = 'browser-delivery-v1'
  manifest.deliveryVersion = 'demo-v1'
  const manifestBytes = encoder.encode(JSON.stringify(manifest))
  const files = new Map<string, Uint8Array>([
    ['generations/demo/manifest.json', manifestBytes],
    ['generations/demo/track-assets.json', track],
    ['generations/demo/chunks/chunk-001.json', chunkOne],
    ['generations/demo/chunks/chunk-002.json', chunkTwo],
  ])
  files.set('browser-current.json', encoder.encode(JSON.stringify({
    formatVersion: 'browser-delivery-v1', deliveryVersion: 'demo-v1',
    manifestPath: 'generations/demo/manifest.json', manifestSha256: await sha256Hex(manifestBytes),
  })))
  const reads: string[] = []
  return { files, reads, source: mapSource(files, reads) }
}

function mapSource(files: Map<string, Uint8Array>, reads?: string[]): ReplaySource {
  return { async read(path) { reads?.push(path); const value = files.get(path); if (!value) throw new Error(`Missing fixture path: ${path}`); return value } }
}
