import { describe, expect, test } from 'vitest'
import {
  getReplayReadySessions,
  isSessionReplayReady,
  parseCatalogV2,
  resolveBrowserPointer,
  selectReplaySession,
} from '../../../src/data/catalog'

const readySession = {
  session_code: 'R',
  session_name: 'Race',
  generation_id: '2024-round-05-r',
  delivery_version: '2024-round-05-r',
  outcome: 'generated',
  validated: true,
  canonical_pointer: 'canonical/2024-round-05/sessions/r/current.json',
  browser_pointer: 'browser/2024-round-05/sessions/r/browser-current.json',
}

function catalog(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schemaVersion: 2,
    year: 2024,
    atomicAcrossRaces: false,
    races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix',
      sessions: [readySession],
    }],
    ...overrides,
  }
}

describe('catalog v2 guards and selection', () => {
  test('parses the Python catalog v2 shape and freezes the result', () => {
    const parsed = parseCatalogV2(catalog())

    expect(parsed.schemaVersion).toBe(2)
    expect(parsed.races[0].sessions[0].session_code).toBe('r')
    expect(Object.isFrozen(parsed.races[0].sessions[0])).toBe(true)
  })

  test('rejects unsupported schema versions and unknown fields', () => {
    expect(() => parseCatalogV2(catalog({ schemaVersion: 1 }))).toThrow('exactly 2')
    expect(() => parseCatalogV2(catalog({ extra: true }))).toThrow('not allowed')
  })

  test('only validated sessions with complete artifact references are replay-ready', () => {
    const missingPointer = { ...readySession, browser_pointer: null }
    expect(isSessionReplayReady(missingPointer)).toBe(false)
    expect(getReplayReadySessions(parseCatalogV2(catalog({ races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions: [readySession],
    }]})).races[0]).length).toBe(1)
  })

  test('selects a valid race and session without allowing an unready session', () => {
    const parsed = parseCatalogV2(catalog())
    expect(selectReplaySession(parsed, '2024-round-05', 'R')?.session.session_code).toBe('r')
    expect(selectReplaySession(parsed, '2024-round-05', 'missing')).toBeNull()
  })

  test('resolves a session pointer to the race browser root and nested path', () => {
    expect(resolveBrowserPointer(
      'browser/2024-round-05/sessions/r/browser-current.json', '2024-round-05', 'r',
    )).toEqual({
      browserBasePath: 'browser/2024-round-05',
      pointerPath: 'sessions/r/browser-current.json',
    })
  })

  test('rejects mismatched identities and traversal paths', () => {
    expect(() => resolveBrowserPointer(
      'browser/2024-round-06/sessions/r/browser-current.json', '2024-round-05', 'r',
    )).toThrow('race identity')
    expect(() => resolveBrowserPointer(
      'browser/2024-round-05/sessions/../browser-current.json', '2024-round-05', 'r',
    )).toThrow('Unsafe replay-data path')
  })
})
