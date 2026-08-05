import { describe, expect, test } from 'vitest'
import { loadCatalog } from '../../../src/data/catalog'
import type { ReplaySource } from '../../../src/data/replay/types'

const emptyPayload = JSON.stringify({ schemaVersion: 2, year: 2024, atomicAcrossRaces: false, races: [] })

function sessionPayload(code: string, name: string, identity: string, mode: string): Record<string, unknown> {
  return {
    session_code: code,
    session_name: name,
    generation_id: `2024-round-05-session-${identity}-mode-${mode}`,
    delivery_version: `2024-round-05-session-${identity}-mode-${mode}`,
    outcome: 'generated',
    validated: true,
    canonical_pointer: null,
    browser_pointer: `browser/2024-round-05/sessions/${code}/browser-current.json`,
  }
}

const multiSessionPayload = JSON.stringify({
  schemaVersion: 2,
  year: 2024,
  atomicAcrossRaces: false,
  races: [{
    race_id: '2024-round-05',
    round_number: 5,
    event_name: 'Chinese Grand Prix',
    sessions: [
      sessionPayload('fp1', 'Practice 1', 'practice-1', 'practice'),
      sessionPayload('q', 'Qualifying', 'qualifying', 'qualifying'),
      sessionPayload('s', 'Sprint', 'sprint', 'sprint'),
      sessionPayload('r', 'Race', 'race', 'race'),
    ],
  }],
})

describe('catalog v2 loader', () => {
  test('reads the requested season catalog through ReplaySource', async () => {
    const reads: string[] = []
    const source: ReplaySource = { read: async (path) => { reads.push(path); return new TextEncoder().encode(emptyPayload) } }

    const result = await loadCatalog({ source, year: 2024 })

    expect(reads).toEqual(['2024/catalog.json'])
    expect(result.year).toBe(2024)
  })

  test('rejects a catalog for a different requested year', async () => {
    const source: ReplaySource = { read: async () => new TextEncoder().encode(emptyPayload.replace('2024', '2023')) }

    await expect(loadCatalog(source, 2024)).rejects.toThrow('disagrees')
  })

  test('loads Practice, Qualifying, Sprint and Race sessions as replay-ready', async () => {
    // Arrange
    const source: ReplaySource = { read: async () => new TextEncoder().encode(multiSessionPayload) }

    // Act
    const result = await loadCatalog({ source, year: 2024 })

    // Assert — every v2 session identity survives the strict loader
    expect(result.races[0].sessions.map((session) => session.session_code)).toEqual(['fp1', 'q', 's', 'r'])
    expect(result.races[0].sessions.every((session) => session.validated)).toBe(true)
  })

  test('reads through an optional seasonsBase prefix', async () => {
    // Arrange
    const reads: string[] = []
    const source: ReplaySource = { read: async (path) => { reads.push(path); return new TextEncoder().encode(emptyPayload) } }

    // Act
    await loadCatalog({ source, year: 2024, seasonsBase: 'archives/' })

    // Assert
    expect(reads).toEqual(['archives/2024/catalog.json'])
  })

  test('rejects a v1 catalog through the loader', async () => {
    // Arrange
    const v1 = JSON.stringify({ schemaVersion: 1, year: 2024, atomicAcrossRaces: false, races: [] })
    const source: ReplaySource = { read: async () => new TextEncoder().encode(v1) }

    // Act + Assert
    await expect(loadCatalog({ source, year: 2024 })).rejects.toThrow('exactly 2')
  })

  test('rejects a catalog with a mixed-version session identity', async () => {
    // Arrange — generation identity claims 2025 inside a 2024 catalog
    const payload = JSON.stringify({
      schemaVersion: 2,
      year: 2024,
      atomicAcrossRaces: false,
      races: [{
        race_id: '2024-round-05',
        round_number: 5,
        event_name: 'Chinese Grand Prix',
        sessions: [{
          ...sessionPayload('r', 'Race', 'race', 'race'),
          generation_id: '2025-round-05-session-race-mode-race',
          delivery_version: '2025-round-05-session-race-mode-race',
        }],
      }],
    })
    const source: ReplaySource = { read: async () => new TextEncoder().encode(payload) }

    // Act + Assert
    await expect(loadCatalog({ source, year: 2024 })).rejects.toThrow('mixed-version identity')
  })

  test('rejects an invalid mode/artifact combination through the loader', async () => {
    // Arrange — validated session claims a browser pointer without delivery metadata
    const payload = JSON.stringify({
      schemaVersion: 2,
      year: 2024,
      atomicAcrossRaces: false,
      races: [{
        race_id: '2024-round-05',
        round_number: 5,
        event_name: 'Chinese Grand Prix',
        sessions: [{
          ...sessionPayload('r', 'Race', 'race', 'race'),
          generation_id: null,
          delivery_version: null,
        }],
      }],
    })
    const source: ReplaySource = { read: async () => new TextEncoder().encode(payload) }

    // Act + Assert
    await expect(loadCatalog({ source, year: 2024 })).rejects.toThrow('pointers require generation_id and delivery_version')
  })

  test('rejects an unsafe catalog path when the seasonsBase contains traversal', async () => {
    // Arrange
    const source: ReplaySource = { read: async () => new TextEncoder().encode(emptyPayload) }

    // Act + Assert
    await expect(loadCatalog({ source, year: 2024, seasonsBase: '../../escape' })).rejects.toThrow('Unsafe replay-data path')
  })
})
