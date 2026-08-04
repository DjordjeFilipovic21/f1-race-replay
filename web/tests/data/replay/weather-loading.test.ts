import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'
import { sha256Hex } from '../../../src/data/replay/digest'
import {
  parseManifest,
  parseWeatherSidecar,
  parseWeatherSidecarReference,
} from '../../../src/data/replay/guards'
import { loadReplayData, loadReplayIndex } from '../../../src/data/replay/loader'
import type { ReplaySource } from '../../../src/data/replay/types'

const fixtureRoot = resolve(import.meta.dirname, '../../../../contracts/replay-data/v1/fixtures/deterministic-race')
const fixtureSource: ReplaySource = { read: (path) => readFile(resolve(fixtureRoot, path)) }
const decoder = new TextDecoder()
const encoder = new TextEncoder()
const WEATHER_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v1:weather-sidecar'
const WEATHER_PATH = 'weather-sidecar.json'

// ---------------------------------------------------------------------------
// Payload builders — native-cadence weather rows with nullable measurements
// ---------------------------------------------------------------------------

function weatherPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    contractVersion: 'v1',
    fixtureId: 'deterministic-race',
    timeMs: [0, 60_000, 120_000],
    airTempC: [21.5, 22.1, 22.8],
    humidityPct: [58, 57, 56],
    pressureMbar: [1013.2, 1013.0, 1012.7],
    rainfall: [false, false, true],
    trackTempC: [30.2, 31.8, 33.4],
    windDirectionDeg: [90, 180, 315],
    windSpeedMps: [2.5, 3.1, 4.2],
    ...overrides,
  }
}

function weatherReference(sha256 = 'a'.repeat(64)): Record<string, string> {
  return { path: WEATHER_PATH, schemaId: WEATHER_SCHEMA, sha256 }
}

// ---------------------------------------------------------------------------
// Source builder — fixture manifest extended with an optional weather artifact
// ---------------------------------------------------------------------------

async function sourceWithWeather(options: {
  payload?: Record<string, unknown>
  corruptDigest?: boolean
  malformedJson?: boolean
  wrongFixture?: boolean
  missingFile?: boolean
  omitReference?: boolean
  reference?: Record<string, unknown>
  includeChunks?: boolean
} = {}): Promise<{ source: ReplaySource; reads: string[]; payload: Uint8Array }> {
  const manifest = JSON.parse(decoder.decode(await fixtureSource.read('manifest.json'))) as Record<string, unknown>
  const track = await fixtureSource.read('track-assets.json')
  const payload = options.malformedJson
    ? encoder.encode('not json')
    : encoder.encode(JSON.stringify(options.wrongFixture ? weatherPayload({ fixtureId: 'wrong-race' }) : options.payload ?? weatherPayload()))

  if (!options.omitReference) {
    manifest.weatherSidecar = options.reference ?? weatherReference(options.corruptDigest ? '0'.repeat(64) : await sha256Hex(payload))
  }

  const files = new Map<string, Uint8Array>([['track-assets.json', track]])
  if (!options.omitReference && !options.missingFile) files.set(WEATHER_PATH, payload)
  if (options.includeChunks) {
    files.set('chunks/chunk-001.json', await fixtureSource.read('chunks/chunk-001.json'))
    files.set('chunks/chunk-002.json', await fixtureSource.read('chunks/chunk-002.json'))
  }
  files.set('manifest.json', encoder.encode(JSON.stringify(manifest)))

  const reads: string[] = []
  const source: ReplaySource = {
    async read(path) {
      reads.push(path)
      const value = files.get(path)
      if (!value) throw new Error(`Missing fixture path: ${path}`)
      return value
    },
  }
  return { source, reads, payload }
}

// ---------------------------------------------------------------------------
// parseWeatherSidecar
// ---------------------------------------------------------------------------

describe('parseWeatherSidecar', () => {
  test('parses a valid native-cadence payload and deeply freezes it', () => {
    // Arrange
    const payload = weatherPayload()

    // Act
    const result = parseWeatherSidecar(payload)

    // Assert
    expect(result.contractVersion).toBe('v1')
    expect(result.fixtureId).toBe('deterministic-race')
    expect(result.timeMs).toEqual([0, 60_000, 120_000])
    expect(result.airTempC).toEqual([21.5, 22.1, 22.8])
    expect(result.rainfall).toEqual([false, false, true])
    expect(result.windDirectionDeg).toEqual([90, 180, 315])
    expect(Object.isFrozen(result)).toBe(true)
    expect(Object.isFrozen(result.timeMs)).toBe(true)
    expect(Object.isFrozen(result.airTempC)).toBe(true)
    expect(Object.isFrozen(result.rainfall)).toBe(true)
  })

  test('accepts nullable measurements and null rainfall rows', () => {
    // Arrange
    const payload = weatherPayload({
      airTempC: [21.5, null, 22.8],
      humidityPct: [null, 57, 56],
      rainfall: [null, false, true],
      windDirectionDeg: [90, null, 315],
    })

    // Act
    const result = parseWeatherSidecar(payload)

    // Assert
    expect(result.airTempC).toEqual([21.5, null, 22.8])
    expect(result.humidityPct).toEqual([null, 57, 56])
    expect(result.rainfall).toEqual([null, false, true])
    expect(result.windDirectionDeg).toEqual([90, null, 315])
  })

  test('accepts zero values where the schema treats them as physically meaningful', () => {
    // Arrange — humidity 0, wind direction 0, wind speed 0 are valid measurements.
    const payload = weatherPayload({
      humidityPct: [0, 57, 56],
      windDirectionDeg: [0, 180, 315],
      windSpeedMps: [0, 3.1, 4.2],
    })

    // Act
    const result = parseWeatherSidecar(payload)

    // Assert
    expect(result.humidityPct[0]).toBe(0)
    expect(result.windDirectionDeg[0]).toBe(0)
    expect(result.windSpeedMps[0]).toBe(0)
  })

  test('rejects a payload missing a required field', () => {
    // Arrange — omit rainfall
    const payload = weatherPayload()
    delete payload.rainfall

    // Act & Assert
    expect(() => parseWeatherSidecar(payload)).toThrow('weather sidecar.rainfall is required')
  })

  test('rejects a payload with an extra field', () => {
    // Arrange
    const payload = weatherPayload({ extra: true })

    // Act & Assert
    expect(() => parseWeatherSidecar(payload)).toThrow('not allowed')
  })

  test('rejects a payload with the wrong contract version', () => {
    // Arrange
    const payload = weatherPayload({ contractVersion: 'v2' })

    // Act & Assert
    expect(() => parseWeatherSidecar(payload)).toThrow('must be contract version v1')
  })

  test('rejects an invalid fixture id', () => {
    // Arrange
    const payload = weatherPayload({ fixtureId: 'Invalid!' })

    // Act & Assert
    expect(() => parseWeatherSidecar(payload)).toThrow('fixture id is invalid')
  })

  test('rejects an empty timeMs column', () => {
    // Arrange
    const payload = weatherPayload({
      timeMs: [],
      airTempC: [],
      humidityPct: [],
      pressureMbar: [],
      rainfall: [],
      trackTempC: [],
      windDirectionDeg: [],
      windSpeedMps: [],
    })

    // Act & Assert
    expect(() => parseWeatherSidecar(payload)).toThrow('timeMs must be non-empty')
  })

  test('rejects a non-strictly-increasing timeMs column', () => {
    // Arrange — duplicate timestamp
    const payload = weatherPayload({ timeMs: [0, 60_000, 60_000] })

    // Act & Assert
    expect(() => parseWeatherSidecar(payload)).toThrow('timeMs must be strictly increasing')
  })

  test('rejects a measurement column not aligned to timeMs', () => {
    // Arrange — airTempC is one row shorter
    const payload = weatherPayload({ airTempC: [21.5, 22.1] })

    // Act & Assert
    expect(() => parseWeatherSidecar(payload)).toThrow('not aligned to timeMs')
  })

  test('rejects a non-finite measurement value', () => {
    // Arrange — string instead of a number
    const payload = weatherPayload({ airTempC: [21.5, '22.1', 22.8] })

    // Act & Assert
    expect(() => parseWeatherSidecar(payload)).toThrow('airTempC value must be finite')
  })

  test('rejects a zero air temperature sentinel', () => {
    // Arrange — FastF1 replaces malformed rows with 0; the guard rejects them for air temperature.
    const payload = weatherPayload({ airTempC: [0, 22.1, 22.8] })

    // Act & Assert
    expect(() => parseWeatherSidecar(payload)).toThrow('airTempC value must be greater than zero')
  })

  test('rejects a humidity measurement outside 0-100', () => {
    // Arrange
    const payload = weatherPayload({ humidityPct: [58, 101, 56] })

    // Act & Assert
    expect(() => parseWeatherSidecar(payload)).toThrow('humidityPct value must be between 0 and 100')
  })

  test('rejects a wind direction outside the 0-359 degree range', () => {
    // Arrange
    const payload = weatherPayload({ windDirectionDeg: [90, 180, 360] })

    // Act & Assert
    expect(() => parseWeatherSidecar(payload)).toThrow('windDirectionDeg value must be an integer from 0 to 359')
  })

  test('rejects a non-boolean rainfall entry', () => {
    // Arrange — FastF1 passes '1'/'0' strings; the guard only accepts booleans or null.
    const payload = weatherPayload({ rainfall: [false, '1', true] })

    // Act & Assert
    expect(() => parseWeatherSidecar(payload)).toThrow('rainfall must contain booleans or null')
  })
})

// ---------------------------------------------------------------------------
// parseWeatherSidecarReference
// ---------------------------------------------------------------------------

describe('parseWeatherSidecarReference', () => {
  test('parses a valid weather sidecar reference and freezes it', () => {
    // Arrange
    const reference = weatherReference()

    // Act
    const result = parseWeatherSidecarReference(reference)

    // Assert
    expect(result.path).toBe(WEATHER_PATH)
    expect(result.schemaId).toBe(WEATHER_SCHEMA)
    expect(result.sha256).toBe('a'.repeat(64))
    expect(Object.isFrozen(result)).toBe(true)
  })

  test('rejects an unsafe or unsupported path', () => {
    // Arrange — a traversal path must never be accepted as a weather reference.
    const reference = { ...weatherReference(), path: '../weather-sidecar.json' }

    // Act & Assert
    expect(() => parseWeatherSidecarReference(reference)).toThrow('weather sidecar path is unsupported')
  })

  test('rejects a wrong schema identity', () => {
    // Arrange
    const reference = { ...weatherReference(), schemaId: 'urn:f1-cache-replay:schema:replay-data:v1:timeline-summary' }

    // Act & Assert
    expect(() => parseWeatherSidecarReference(reference)).toThrow('weather sidecar schema identity is unsupported')
  })

  test('rejects an invalid sha256 digest', () => {
    // Arrange
    const reference = { ...weatherReference(), sha256: 'not-a-digest' }

    // Act & Assert
    expect(() => parseWeatherSidecarReference(reference)).toThrow('manifest.weatherSidecar.sha256 is invalid')
  })

  test('rejects extra fields on the reference', () => {
    // Arrange
    const reference = { ...weatherReference(), unexpected: true }

    // Act & Assert
    expect(() => parseWeatherSidecarReference(reference)).toThrow('not allowed')
  })
})

// ---------------------------------------------------------------------------
// Manifest reference compatibility
// ---------------------------------------------------------------------------

describe('weather sidecar manifest reference', () => {
  test('parses a manifest that declares a weatherSidecar reference', async () => {
    // Arrange
    const manifest = JSON.parse(decoder.decode(await fixtureSource.read('manifest.json'))) as Record<string, unknown>
    manifest.weatherSidecar = weatherReference()

    // Act
    const parsed = parseManifest(manifest)

    // Assert
    expect(parsed.weatherSidecar).toBeDefined()
    expect(parsed.weatherSidecar!.path).toBe(WEATHER_PATH)
    expect(parsed.weatherSidecar!.schemaId).toBe(WEATHER_SCHEMA)
    expect(Object.isFrozen(parsed.weatherSidecar)).toBe(true)
  })

  test('keeps legacy manifests without weatherSidecar loadable and property-free', async () => {
    // Arrange — the published fixture manifest predates the weather artifact.
    const manifest = JSON.parse(decoder.decode(await fixtureSource.read('manifest.json'))) as Record<string, unknown>

    // Act
    const parsed = parseManifest(manifest)

    // Assert
    expect(parsed).not.toHaveProperty('weatherSidecar')
  })
})

// ---------------------------------------------------------------------------
// Weather sidecar loader integration
// ---------------------------------------------------------------------------

describe('weather sidecar loader integration', () => {
  test('fetches, checksum-checks, parses, fixture-checks, and exposes the optional weather sidecar', async () => {
    // Arrange
    const { source, reads } = await sourceWithWeather()

    // Act
    const index = await loadReplayIndex({ source })

    // Assert — the artifact is read and its reference resolved from the manifest path.
    expect(reads).toEqual(['manifest.json', 'track-assets.json', WEATHER_PATH])
    expect(index.weatherSidecar).toBeDefined()
    expect(index.weatherSidecar!.fixtureId).toBe('deterministic-race')
    expect(index.weatherSidecar!.timeMs).toEqual([0, 60_000, 120_000])
    expect(index.weatherSidecar!.rainfall).toEqual([false, false, true])
  })

  test('performs no weather fetch for a legacy manifest and remains loadable', async () => {
    // Arrange
    const { source, reads } = await sourceWithWeather({ omitReference: true })

    // Act
    const index = await loadReplayIndex({ source })

    // Assert — no weather artifact is requested and the index stays valid.
    expect(reads).toEqual(['manifest.json', 'track-assets.json'])
    expect(index).not.toHaveProperty('weatherSidecar')
  })

  test('treats a weather sidecar SHA-256 digest mismatch as unavailable while chunks remain usable', async () => {
    // Arrange
    const { source } = await sourceWithWeather({ corruptDigest: true, includeChunks: true })

    // Act
    const replay = await loadReplayData({ source })

    // Assert
    expect(replay.weatherSidecar).toBeUndefined()
    expect(replay.chunks).toHaveLength(2)
  })

  test('treats a weather sidecar fixture mismatch as unavailable while chunks remain usable', async () => {
    // Arrange
    const { source } = await sourceWithWeather({ wrongFixture: true, includeChunks: true })

    // Act
    const replay = await loadReplayData({ source })

    // Assert
    expect(replay.weatherSidecar).toBeUndefined()
    expect(replay.chunks).toHaveLength(2)
  })

  test('treats malformed weather JSON as unavailable while chunks remain usable', async () => {
    // Arrange
    const { source } = await sourceWithWeather({ malformedJson: true, includeChunks: true })

    // Act
    const replay = await loadReplayData({ source })

    // Assert
    expect(replay.weatherSidecar).toBeUndefined()
    expect(replay.chunks).toHaveLength(2)
  })

  test('treats schema-invalid weather JSON as unavailable while chunks remain usable', async () => {
    // Arrange — the bytes are valid JSON but violate the strict weather shape.
    const { source } = await sourceWithWeather({
      payload: weatherPayload({ extra: true }),
      includeChunks: true,
    })

    // Act
    const replay = await loadReplayData({ source })

    // Assert
    expect(replay.weatherSidecar).toBeUndefined()
    expect(replay.chunks).toHaveLength(2)
  })

  test('treats a missing weather sidecar as unavailable while chunks remain usable', async () => {
    // Arrange — the manifest declares the reference but the artifact is missing.
    const { source } = await sourceWithWeather({ missingFile: true, includeChunks: true })

    // Act
    const replay = await loadReplayData({ source })

    // Assert
    expect(replay.weatherSidecar).toBeUndefined()
    expect(replay.chunks).toHaveLength(2)
  })

  test('treats an unsafe weather reference as unavailable without weakening core manifest validation', async () => {
    // Arrange — the guard rejects this path, but the weather field is optional.
    const { source } = await sourceWithWeather({
      reference: { ...weatherReference(), path: '../weather-sidecar.json' },
      includeChunks: true,
    })

    // Act
    const replay = await loadReplayData({ source })

    // Assert
    expect(replay.weatherSidecar).toBeUndefined()
    expect(replay.chunks).toHaveLength(2)
  })

  test('treats an unsupported weather schema identity as unavailable while chunks remain usable', async () => {
    // Arrange — a bad optional reference must not relax strict core artifacts.
    const { source } = await sourceWithWeather({
      reference: { ...weatherReference(), schemaId: 'urn:f1-cache-replay:schema:replay-data:v1:wrong' },
      includeChunks: true,
    })

    // Act
    const replay = await loadReplayData({ source })

    // Assert
    expect(replay.weatherSidecar).toBeUndefined()
    expect(replay.chunks).toHaveLength(2)
  })

  test('treats a malformed weather reference digest as unavailable while chunks remain usable', async () => {
    // Arrange — an invalid reference digest is rejected before any weather fetch.
    const { source } = await sourceWithWeather({
      reference: { ...weatherReference(), sha256: 'not-a-digest' },
      includeChunks: true,
    })

    // Act
    const replay = await loadReplayData({ source })

    // Assert
    expect(replay.weatherSidecar).toBeUndefined()
    expect(replay.chunks).toHaveLength(2)
  })

  test('resolves the nested production pointer layout and its weather artifact', async () => {
    // Arrange
    const { source, reads } = await pointerSourceWithWeather()

    // Act
    const index = await loadReplayIndex({ source, pointerPath: 'browser-current.json' })

    // Assert — the manifest-relative artifact path resolves under the generation folder.
    expect(reads.slice(0, 3)).toEqual([
      'browser-current.json',
      'generations/demo/manifest.json',
      'generations/demo/track-assets.json',
    ])
    expect(reads).toContain('generations/demo/weather-sidecar.json')
    expect(index.weatherSidecar?.fixtureId).toBe('deterministic-race')
  })

  test('exposes the weather sidecar on the full replay data bundle with chunks', async () => {
    // Arrange
    const { source } = await sourceWithWeather({ includeChunks: true })

    // Act
    const replay = await loadReplayData({ source })

    // Assert — the weather artifact survives into ReplayData alongside validated chunks.
    expect(replay.weatherSidecar?.timeMs).toEqual([0, 60_000, 120_000])
    expect(replay.chunks).toHaveLength(2)
  })

  test('deeply freezes the parsed weather sidecar on the index', async () => {
    // Arrange
    const { source } = await sourceWithWeather()

    // Act
    const index = await loadReplayIndex({ source })

    // Assert
    expect(Object.isFrozen(index.weatherSidecar)).toBe(true)
    expect(Object.isFrozen(index.weatherSidecar!.timeMs)).toBe(true)
    expect(Object.isFrozen(index.weatherSidecar!.airTempC)).toBe(true)
    expect(Object.isFrozen(index.weatherSidecar!.rainfall)).toBe(true)
  })
})

async function pointerSourceWithWeather(): Promise<{ source: ReplaySource; reads: string[] }> {
  const manifest = JSON.parse(decoder.decode(await fixtureSource.read('manifest.json'))) as Record<string, unknown>
  const track = await fixtureSource.read('track-assets.json')
  const payload = encoder.encode(JSON.stringify(weatherPayload()))
  manifest.formatVersion = 'browser-delivery-v1'
  manifest.deliveryVersion = 'demo-v1'
  manifest.weatherSidecar = weatherReference(await sha256Hex(payload))
  const manifestBytes = encoder.encode(JSON.stringify(manifest))
  const files = new Map<string, Uint8Array>([
    ['generations/demo/manifest.json', manifestBytes],
    ['generations/demo/track-assets.json', track],
    ['generations/demo/weather-sidecar.json', payload],
  ])
  files.set('browser-current.json', encoder.encode(JSON.stringify({
    formatVersion: 'browser-delivery-v1',
    deliveryVersion: 'demo-v1',
    manifestPath: 'generations/demo/manifest.json',
    manifestSha256: await sha256Hex(manifestBytes),
  })))
  const reads: string[] = []
  const source: ReplaySource = {
    async read(path) {
      reads.push(path)
      const value = files.get(path)
      if (!value) throw new Error(`Missing fixture path: ${path}`)
      return value
    },
  }
  return { source, reads }
}
