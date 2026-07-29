import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'
import { sha256Hex } from '../../../src/data/replay/digest'
import {
  parseManifest,
  parsePenaltySidecar,
  parsePenaltySidecarReference,
} from '../../../src/data/replay/guards'
import { loadReplayIndex } from '../../../src/data/replay/loader'
import type { ReplaySource } from '../../../src/data/replay/types'

const fixtureRoot = resolve(import.meta.dirname, '../../../../contracts/replay-data/v1/fixtures/deterministic-race')
const fixtureSource: ReplaySource = { read: (path) => readFile(resolve(fixtureRoot, path)) }
const decoder = new TextDecoder()
const encoder = new TextEncoder()
const PENALTY_SCHEMA = 'urn:f1-cache-replay:schema:replay-data:v1:penalty-sidecar'

function penaltyIssuance(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    driverId: 'HAM',
    sessionTimeMs: 12_500,
    penaltyType: '10 second time penalty',
    reason: 'Causing a collision',
    rawMessage: 'FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 44 (HAM) - CAUSING A COLLISION',
    lapNumber: 9,
    ...overrides,
  }
}

function penaltyPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    contractVersion: 'v1',
    fixtureId: 'deterministic-race',
    penaltyIssuances: [penaltyIssuance()],
    ...overrides,
  }
}

function penaltyReference(sha256 = 'a'.repeat(64)): Record<string, string> {
  return { path: 'penalty-sidecar.json', schemaId: PENALTY_SCHEMA, sha256 }
}

async function sourceWithPenalty(options: { corruptDigest?: boolean } = {}): Promise<{ source: ReplaySource; payload: Uint8Array }> {
  const manifest = JSON.parse(decoder.decode(await fixtureSource.read('manifest.json'))) as Record<string, unknown>
  const track = await fixtureSource.read('track-assets.json')
  const payload = encoder.encode(JSON.stringify(penaltyPayload()))
  const digest = await sha256Hex(payload)
  manifest.penaltySidecar = penaltyReference(options.corruptDigest ? 'a'.repeat(64) : digest)
  const files = new Map<string, Uint8Array>([
    ['manifest.json', encoder.encode(JSON.stringify(manifest))],
    ['track-assets.json', track],
    ['penalty-sidecar.json', payload],
  ])
  return {
    payload,
    source: {
      async read(path) {
        const value = files.get(path)
        if (!value) throw new Error(`Missing fixture path: ${path}`)
        return value
      },
    },
  }
}

describe('penalty sidecar guards', () => {
  test('parses a valid issuance and freezes the result', () => {
    const result = parsePenaltySidecar(penaltyPayload())

    expect(result.penaltyIssuances[0]).toMatchObject({ driverId: 'HAM', lapNumber: 9 })
    expect(Object.isFrozen(result)).toBe(true)
    expect(Object.isFrozen(result.penaltyIssuances)).toBe(true)
  })

  test('rejects invalid issuance entries', () => {
    const payload = penaltyPayload({
      penaltyIssuances: [penaltyIssuance({ driverId: 'bad' })],
    })

    expect(() => parsePenaltySidecar(payload)).toThrow('driverId is invalid')
  })

  test('validates the optional manifest reference and preserves legacy manifests', async () => {
    const manifest = JSON.parse(decoder.decode(await fixtureSource.read('manifest.json'))) as Record<string, unknown>
    expect(parseManifest(manifest)).not.toHaveProperty('penaltySidecar')
    expect(parsePenaltySidecarReference(penaltyReference())).toEqual(penaltyReference())
    expect(() => parsePenaltySidecarReference({ ...penaltyReference(), path: 'wrong.json' })).toThrow('path is unsupported')
    expect(() => parsePenaltySidecarReference({ ...penaltyReference(), sha256: 'bad' })).toThrow('sha256 is invalid')
  })
})

describe('penalty sidecar loader integration', () => {
  test('loads the optional sidecar and validates its digest', async () => {
    const { source, payload } = await sourceWithPenalty()
    const index = await loadReplayIndex({ source })

    expect(index.penaltySidecar?.penaltyIssuances[0].rawMessage).toContain('10 SECOND TIME PENALTY')
    expect(index.penaltySidecar?.fixtureId).toBe('deterministic-race')
    expect(index.penaltySidecar?.penaltyIssuances).toHaveLength(1)
    expect(await sha256Hex(payload)).toHaveLength(64)
  })

  test('loads successfully when the optional sidecar is absent', async () => {
    const index = await loadReplayIndex({ source: fixtureSource })

    expect(index).not.toHaveProperty('penaltySidecar')
  })

  test('rejects a penalty sidecar digest mismatch', async () => {
    const { source } = await sourceWithPenalty({ corruptDigest: true })

    await expect(loadReplayIndex({ source })).rejects.toThrow('digest does not match')
  })
})
