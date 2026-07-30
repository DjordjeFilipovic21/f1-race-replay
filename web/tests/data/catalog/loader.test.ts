import { describe, expect, test } from 'vitest'
import { loadCatalog } from '../../../src/data/catalog'
import type { ReplaySource } from '../../../src/data/replay/types'

const payload = JSON.stringify({ schemaVersion: 2, year: 2024, atomicAcrossRaces: false, races: [] })

describe('catalog v2 loader', () => {
  test('reads the requested season catalog through ReplaySource', async () => {
    const reads: string[] = []
    const source: ReplaySource = { read: async (path) => { reads.push(path); return new TextEncoder().encode(payload) } }

    const result = await loadCatalog({ source, year: 2024 })

    expect(reads).toEqual(['2024/catalog.json'])
    expect(result.year).toBe(2024)
  })

  test('rejects a catalog for a different requested year', async () => {
    const source: ReplaySource = { read: async () => new TextEncoder().encode(payload.replace('2024', '2023')) }

    await expect(loadCatalog(source, 2024)).rejects.toThrow('disagrees')
  })
})
