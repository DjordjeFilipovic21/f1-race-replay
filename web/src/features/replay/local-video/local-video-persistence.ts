/**
 * The persistence schema deliberately contains metadata and anchors only. A
 * File, its bytes, and the object URL created for it are all session-local.
 */
export const LOCAL_VIDEO_PERSISTENCE_STORAGE_KEY = 'f1-race-replay:local-video-alignment'
export const LOCAL_VIDEO_PERSISTENCE_VERSION = 1
export const LOCAL_VIDEO_ALIGNMENT_STORAGE_KEY = LOCAL_VIDEO_PERSISTENCE_STORAGE_KEY
export const LOCAL_VIDEO_ALIGNMENT_VERSION = LOCAL_VIDEO_PERSISTENCE_VERSION

export type LocalVideoReplayIdentity = string | Readonly<Record<string, string>>

export interface LocalVideoFileMetadata {
  readonly name: string
  readonly size: number
  readonly lastModified: number
  readonly type: string
}

export interface LocalVideoAlignment {
  readonly replayTimeMs: number
  readonly videoTimeMs: number
}

export interface LocalVideoPersistenceStorage {
  readonly getItem: (key: string) => string | null
  readonly setItem: (key: string, value: string) => void
}

export interface LocalVideoPersistenceRecord {
  readonly version: typeof LOCAL_VIDEO_PERSISTENCE_VERSION
  readonly replayIdentity: LocalVideoReplayIdentity
  readonly fileMetadata: LocalVideoFileMetadata
  readonly alignment: LocalVideoAlignment
}

export interface SaveLocalVideoAlignmentOptions {
  readonly replayIdentity: LocalVideoReplayIdentity
  readonly fileMetadata: LocalVideoFileMetadata
  readonly alignment: LocalVideoAlignment
}

export interface LoadLocalVideoAlignmentOptions {
  readonly replayIdentity: LocalVideoReplayIdentity
  readonly fileMetadata: LocalVideoFileMetadata
}

export function createLocalVideoFileMetadata(file: Pick<File, 'name' | 'size' | 'lastModified' | 'type'>): LocalVideoFileMetadata {
  return {
    name: file.name,
    size: file.size,
    lastModified: file.lastModified,
    type: file.type,
  }
}

export function saveLocalVideoAlignment(
  replayIdentity: LocalVideoReplayIdentity,
  fileMetadata: LocalVideoFileMetadata,
  alignment: LocalVideoAlignment,
  storage?: LocalVideoPersistenceStorage | null,
): boolean
export function saveLocalVideoAlignment(
  options: SaveLocalVideoAlignmentOptions,
  storage?: LocalVideoPersistenceStorage | null,
): boolean
export function saveLocalVideoAlignment(
  replayIdentityOrOptions: LocalVideoReplayIdentity | SaveLocalVideoAlignmentOptions,
  fileMetadataOrStorage?: LocalVideoFileMetadata | LocalVideoPersistenceStorage | null,
  alignment?: LocalVideoAlignment,
  suppliedStorage?: LocalVideoPersistenceStorage | null,
): boolean {
  const optionsRequest = isSaveOptions(replayIdentityOrOptions)
  const request: SaveLocalVideoAlignmentOptions = optionsRequest
    ? replayIdentityOrOptions
    : {
      replayIdentity: replayIdentityOrOptions,
      fileMetadata: fileMetadataOrStorage as LocalVideoFileMetadata,
      alignment: alignment as LocalVideoAlignment,
    }
  const selectedStorage = optionsRequest
    ? fileMetadataOrStorage as LocalVideoPersistenceStorage | null | undefined
    : suppliedStorage
  const storage = selectedStorage === undefined ? browserStorage() : selectedStorage
  const record = createPersistenceRecord(request)
  if (record === null || storage === null || storage === undefined) return false

  try {
    storage.setItem(LOCAL_VIDEO_PERSISTENCE_STORAGE_KEY, JSON.stringify(record))
    return true
  } catch {
    return false
  }
}

export function loadLocalVideoAlignment(
  replayIdentity: LocalVideoReplayIdentity,
  fileMetadata: LocalVideoFileMetadata,
  storage?: LocalVideoPersistenceStorage | null,
): LocalVideoAlignment | null
export function loadLocalVideoAlignment(
  options: LoadLocalVideoAlignmentOptions,
  storage?: LocalVideoPersistenceStorage | null,
): LocalVideoAlignment | null
export function loadLocalVideoAlignment(
  replayIdentityOrOptions: LocalVideoReplayIdentity | LoadLocalVideoAlignmentOptions,
  fileMetadataOrStorage?: LocalVideoFileMetadata | LocalVideoPersistenceStorage | null,
  suppliedStorage?: LocalVideoPersistenceStorage | null,
): LocalVideoAlignment | null {
  const optionsRequest = isLoadOptions(replayIdentityOrOptions)
  const request: LoadLocalVideoAlignmentOptions = optionsRequest
    ? replayIdentityOrOptions
    : {
      replayIdentity: replayIdentityOrOptions,
      fileMetadata: fileMetadataOrStorage as LocalVideoFileMetadata,
    }
  const selectedStorage = optionsRequest
    ? fileMetadataOrStorage as LocalVideoPersistenceStorage | null | undefined
    : suppliedStorage
  const storage = selectedStorage === undefined ? browserStorage() : selectedStorage
  if (storage === null || storage === undefined) return null

  try {
    const stored = storage.getItem(LOCAL_VIDEO_PERSISTENCE_STORAGE_KEY)
    if (stored === null) return null
    const record = parsePersistenceRecord(JSON.parse(stored))
    if (record === null || !sameReplayIdentity(record.replayIdentity, request.replayIdentity) || !sameFileMetadata(record.fileMetadata, request.fileMetadata)) return null
    return record.alignment
  } catch {
    return null
  }
}

export function hasPersistedLocalVideoAlignment(
  replayIdentity: LocalVideoReplayIdentity,
  storage?: LocalVideoPersistenceStorage | null,
): boolean {
  const normalizedReplayIdentity = normalizeReplayIdentity(replayIdentity)
  const selectedStorage = storage === undefined ? browserStorage() : storage
  if (normalizedReplayIdentity === null || selectedStorage === null || selectedStorage === undefined) return false

  try {
    const stored = selectedStorage.getItem(LOCAL_VIDEO_PERSISTENCE_STORAGE_KEY)
    if (stored === null) return false
    const record = parsePersistenceRecord(JSON.parse(stored))
    return record !== null && sameReplayIdentity(record.replayIdentity, normalizedReplayIdentity)
  } catch {
    return false
  }
}

function createPersistenceRecord(options: SaveLocalVideoAlignmentOptions): LocalVideoPersistenceRecord | null {
  const replayIdentity = normalizeReplayIdentity(options.replayIdentity)
  const fileMetadata = normalizeFileMetadata(options.fileMetadata)
  const alignment = normalizeAlignment(options.alignment)
  if (replayIdentity === null || fileMetadata === null || alignment === null) return null
  return { version: LOCAL_VIDEO_PERSISTENCE_VERSION, replayIdentity, fileMetadata, alignment }
}

function parsePersistenceRecord(value: unknown): LocalVideoPersistenceRecord | null {
  if (!isRecord(value) || !hasExactKeys(value, ['version', 'replayIdentity', 'fileMetadata', 'alignment']) || value.version !== LOCAL_VIDEO_PERSISTENCE_VERSION) return null
  const replayIdentity = normalizeReplayIdentity(value.replayIdentity)
  const fileMetadata = normalizeFileMetadata(value.fileMetadata)
  const alignment = normalizeAlignment(value.alignment, true)
  if (replayIdentity === null || fileMetadata === null || alignment === null) return null
  return { version: LOCAL_VIDEO_PERSISTENCE_VERSION, replayIdentity, fileMetadata, alignment }
}

function normalizeReplayIdentity(value: unknown): LocalVideoReplayIdentity | null {
  if (typeof value === 'string') return value.length > 0 ? value : null
  if (!isRecord(value) || Object.keys(value).length === 0) return null
  const entries = Object.entries(value)
  if (entries.some(([, entryValue]) => typeof entryValue !== 'string')) return null
  return Object.fromEntries(entries.sort(([left], [right]) => left.localeCompare(right))) as Readonly<Record<string, string>>
}

function normalizeFileMetadata(value: unknown): LocalVideoFileMetadata | null {
  if (!isRecord(value) || !hasExactKeys(value, ['name', 'size', 'lastModified', 'type'])) return null
  if (typeof value.name !== 'string' || value.name.length === 0 || /[\\/]/u.test(value.name)) return null
  if (!isNonNegativeSafeInteger(value.size) || !isNonNegativeSafeInteger(value.lastModified) || typeof value.type !== 'string') return null
  return { name: value.name, size: value.size, lastModified: value.lastModified, type: value.type }
}

function normalizeAlignment(value: unknown, requireExactKeys = false): LocalVideoAlignment | null {
  if (!isRecord(value) || (requireExactKeys && !hasExactKeys(value, ['replayTimeMs', 'videoTimeMs']))) return null
  if (!isSafeInteger(value.replayTimeMs) || !isSafeInteger(value.videoTimeMs)) return null
  return { replayTimeMs: value.replayTimeMs, videoTimeMs: value.videoTimeMs }
}

function sameReplayIdentity(left: LocalVideoReplayIdentity, right: LocalVideoReplayIdentity): boolean {
  return serializeReplayIdentity(left) === serializeReplayIdentity(right)
}

function sameFileMetadata(left: LocalVideoFileMetadata, right: LocalVideoFileMetadata): boolean {
  return left.name === right.name
    && left.size === right.size
    && left.lastModified === right.lastModified
    && left.type === right.type
}

function serializeReplayIdentity(identity: LocalVideoReplayIdentity): string {
  return typeof identity === 'string' ? JSON.stringify(identity) : JSON.stringify(identity, Object.keys(identity).sort())
}

function isSaveOptions(value: LocalVideoReplayIdentity | SaveLocalVideoAlignmentOptions): value is SaveLocalVideoAlignmentOptions {
  return isRecord(value) && 'replayIdentity' in value && 'fileMetadata' in value && 'alignment' in value
}

function isLoadOptions(value: LocalVideoReplayIdentity | LoadLocalVideoAlignmentOptions): value is LoadLocalVideoAlignmentOptions {
  return isRecord(value) && 'replayIdentity' in value && 'fileMetadata' in value
}

function hasExactKeys(value: Readonly<Record<string, unknown>>, keys: readonly string[]): boolean {
  const actualKeys = Object.keys(value).sort()
  return actualKeys.length === keys.length && actualKeys.every((key, index) => key === [...keys].sort()[index])
}

function isNonNegativeSafeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

function isSafeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value)
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function browserStorage(): LocalVideoPersistenceStorage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage ?? null
  } catch {
    return null
  }
}
