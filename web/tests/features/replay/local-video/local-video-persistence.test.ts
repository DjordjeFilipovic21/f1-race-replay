import { describe, expect, test } from 'vitest'
import {
  LOCAL_VIDEO_PERSISTENCE_STORAGE_KEY,
  LOCAL_VIDEO_PERSISTENCE_VERSION,
  createLocalVideoFileMetadata,
  hasPersistedLocalVideoAlignment,
  loadLocalVideoAlignment,
  saveLocalVideoAlignment,
  type LocalVideoFileMetadata,
  type LocalVideoPersistenceStorage,
} from '../../../../src/features/replay/local-video/local-video-persistence'

const replayIdentity = { season: '2024', round: '21' } as const
const fileMetadata: LocalVideoFileMetadata = {
  name: 'race.mp4',
  size: 1_024,
  lastModified: 42,
  type: 'video/mp4',
}
const alignment = { replayTimeMs: 1_500, videoTimeMs: 500 } as const

describe('local video alignment persistence', () => {
  test('round-trips an alignment only for matching replay and file metadata', () => {
    const storage = createMemoryStorage()
    const saved = saveLocalVideoAlignment(replayIdentity, fileMetadata, alignment, storage.storage)

    expect(saved).toBe(true)
    expect(loadLocalVideoAlignment(replayIdentity, fileMetadata, storage.storage)).toStrictEqual(alignment)
  })

  test('rejects replay identities and every changed file metadata field', () => {
    const storage = createMemoryStorage()
    saveLocalVideoAlignment(replayIdentity, fileMetadata, alignment, storage.storage)

    expect(loadLocalVideoAlignment({ season: '2025', round: '21' }, fileMetadata, storage.storage)).toBeNull()
    expect(loadLocalVideoAlignment(replayIdentity, { ...fileMetadata, name: 'other.mp4' }, storage.storage)).toBeNull()
    expect(loadLocalVideoAlignment(replayIdentity, { ...fileMetadata, size: 1_025 }, storage.storage)).toBeNull()
    expect(loadLocalVideoAlignment(replayIdentity, { ...fileMetadata, lastModified: 43 }, storage.storage)).toBeNull()
    expect(loadLocalVideoAlignment(replayIdentity, { ...fileMetadata, type: 'video/webm' }, storage.storage)).toBeNull()
  })

  test('serializes metadata and anchors without file bytes, File objects, paths, or blob URLs', () => {
    const storage = createMemoryStorage()
    const file = new File(['private video bytes'], 'race.mp4', { type: 'video/mp4', lastModified: 42 })
    const metadata = createLocalVideoFileMetadata(file)

    expect(saveLocalVideoAlignment('replay-1', metadata, alignment, storage.storage)).toBe(true)

    const serialized = storage.value()
    expect(serialized).toBeDefined()
    expect(serialized).not.toContain('private video bytes')
    expect(serialized).not.toContain('blob:')
    expect(serialized).not.toContain('sourceUrl')
    expect(serialized).not.toContain('path')
    const persisted = JSON.parse(serialized ?? '') as Record<string, unknown>
    expect(persisted).not.toHaveProperty('file')
    expect(persisted).not.toHaveProperty('bytes')
    expect(persisted).not.toHaveProperty('path')
    expect(persisted).not.toHaveProperty('sourceUrl')
    expect(persisted).not.toHaveProperty('blobUrl')
  })

  test('returns null for malformed stored JSON and malformed persistence records', () => {
    const malformedJson = createMemoryStorage('{not valid json')
    const malformedRecord = createMemoryStorage(JSON.stringify({
      version: LOCAL_VIDEO_PERSISTENCE_VERSION,
      replayIdentity: 'replay-1',
      fileMetadata,
      alignment: { replayTimeMs: 1.5, videoTimeMs: 500 },
    }))

    expect(loadLocalVideoAlignment('replay-1', fileMetadata, malformedJson.storage)).toBeNull()
    expect(loadLocalVideoAlignment('replay-1', fileMetadata, malformedRecord.storage)).toBeNull()
  })

  test('reports whether a valid stored alignment belongs to the requested replay', () => {
    const storage = createMemoryStorage(JSON.stringify(createRecord()))

    expect(hasPersistedLocalVideoAlignment('replay-1', storage.storage)).toBe(true)
    expect(hasPersistedLocalVideoAlignment('replay-2', storage.storage)).toBe(false)
  })

  test('returns false for malformed, invalid-version, and missing records', () => {
    const malformedJson = createMemoryStorage('{not valid json')
    const malformedRecord = createMemoryStorage(JSON.stringify({ ...createRecord(), alignment: { replayTimeMs: 1.5, videoTimeMs: 500 } }))
    const invalidVersion = createMemoryStorage(JSON.stringify({ ...createRecord(), version: LOCAL_VIDEO_PERSISTENCE_VERSION + 1 }))
    const missingRecord = createMemoryStorage()

    expect(hasPersistedLocalVideoAlignment('replay-1', malformedJson.storage)).toBe(false)
    expect(hasPersistedLocalVideoAlignment('replay-1', malformedRecord.storage)).toBe(false)
    expect(hasPersistedLocalVideoAlignment('replay-1', invalidVersion.storage)).toBe(false)
    expect(hasPersistedLocalVideoAlignment('replay-1', missingRecord.storage)).toBe(false)
  })

  test('returns false when storage writes or reads fail', () => {
    const failingWriteStorage: LocalVideoPersistenceStorage = {
      getItem: () => null,
      setItem: () => { throw new Error('quota exceeded') },
    }
    const failingReadStorage: LocalVideoPersistenceStorage = {
      getItem: () => { throw new Error('storage unavailable') },
      setItem: () => undefined,
    }

    expect(saveLocalVideoAlignment('replay-1', fileMetadata, alignment, failingWriteStorage)).toBe(false)
    expect(loadLocalVideoAlignment('replay-1', fileMetadata, failingReadStorage)).toBeNull()
    expect(hasPersistedLocalVideoAlignment('replay-1', failingReadStorage)).toBe(false)
  })

  test('returns safe empty results when storage is missing', () => {
    expect(saveLocalVideoAlignment('replay-1', fileMetadata, alignment, null)).toBe(false)
    expect(loadLocalVideoAlignment('replay-1', fileMetadata, null)).toBeNull()
    expect(hasPersistedLocalVideoAlignment('replay-1', null)).toBe(false)
  })

  test('isolates persistence by the canonical key and current schema version', () => {
    const requestedKeys: string[] = []
    const wrongKeyStorage: LocalVideoPersistenceStorage = {
      getItem: (key) => {
        requestedKeys.push(key)
        return key === 'other-key' ? JSON.stringify(createRecord()) : null
      },
      setItem: () => undefined,
    }
    const wrongVersionStorage = createMemoryStorage(JSON.stringify({ ...createRecord(), version: LOCAL_VIDEO_PERSISTENCE_VERSION + 1 }))

    expect(loadLocalVideoAlignment('replay-1', fileMetadata, wrongKeyStorage)).toBeNull()
    expect(requestedKeys).toStrictEqual([LOCAL_VIDEO_PERSISTENCE_STORAGE_KEY])
    expect(loadLocalVideoAlignment('replay-1', fileMetadata, wrongVersionStorage.storage)).toBeNull()
  })
})

interface MemoryStorage {
  readonly storage: LocalVideoPersistenceStorage
  readonly value: () => string | null
}

function createMemoryStorage(initial: string | null = null): MemoryStorage {
  let stored = initial
  return {
    storage: {
      getItem: () => stored,
      setItem: (_key, value) => { stored = value },
    },
    value: () => stored,
  }
}

function createRecord(): object {
  return {
    version: LOCAL_VIDEO_PERSISTENCE_VERSION,
    replayIdentity: 'replay-1',
    fileMetadata,
    alignment,
  }
}
