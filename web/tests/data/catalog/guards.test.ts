import { describe, expect, test } from 'vitest'
import {
  getReplayReadySessions,
  isSessionReplayReady,
  parseCatalogV2,
  parseCatalogV2Race,
  resolveBrowserPointer,
  selectRace,
  selectReplaySession,
  selectSession,
} from '../../../src/data/catalog'
import { parseVisualMetadata } from '../../../src/data/catalog/guards'

const readySession = {
  session_code: 'R',
  session_name: 'Race',
  generation_id: '2024-round-05-session-race-mode-race',
  delivery_version: '2024-round-05-session-race-mode-race',
  outcome: 'generated',
  validated: true,
  canonical_pointer: null,
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
  test.each([
    ['fp1', 'Practice 1', 'practice-1', 'practice'],
    ['q', 'Qualifying', 'qualifying', 'qualifying'],
    ['r', 'Race', 'race', 'race'],
  ] as const)('accepts the v2 %s session identity', (code, name, identity, mode) => {
    const session = { ...readySession, session_code: code, session_name: name,
      generation_id: `2024-round-05-session-${identity}-mode-${mode}`,
      delivery_version: `2024-round-05-session-${identity}-mode-${mode}`,
      browser_pointer: `browser/2024-round-05/sessions/${code}/browser-current.json` }
    expect(parseCatalogV2(catalog({ races: [{ race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions: [session] }] })).races[0].sessions[0].session_code).toBe(code)
  })

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

  test('only validated sessions with complete browser artifact references are replay-ready', () => {
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

// ──────────────────────────────────────────────────────────────
// parseVisualMetadata — valid visual metadata
// ──────────────────────────────────────────────────────────────

describe('parseVisualMetadata', () => {
  test('accepts valid latitude and longitude within bounds', () => {
    // Arrange
    const input = { latitude: 26.0325, longitude: 50.5106 }

    // Act
    const result = parseVisualMetadata(input)

    // Assert
    expect(result.latitude).toBe(26.0325)
    expect(result.longitude).toBe(50.5106)
    expect(result.circuitPreview).toBeUndefined()
  })

  test('accepts boundary coordinates at ±90 latitude and ±180 longitude', () => {
    // Act + Assert
    expect(parseVisualMetadata({ latitude: 90, longitude: 180 })).toEqual({ latitude: 90, longitude: 180 })
    expect(parseVisualMetadata({ latitude: -90, longitude: -180 })).toEqual({ latitude: -90, longitude: -180 })
    expect(parseVisualMetadata({ latitude: 0, longitude: 0 })).toEqual({ latitude: 0, longitude: 0 })
  })

  test('accepts an optional circuitPreview safe pointer', () => {
    // Arrange
    const input = { latitude: 45.0, longitude: 7.0, circuitPreview: 'visuals/monza-circuit.json' }

    // Act
    const result = parseVisualMetadata(input)

    // Assert
    expect(result.circuitPreview).toBe('visuals/monza-circuit.json')
  })

  test('freezes the returned visual metadata', () => {
    // Act
    const result = parseVisualMetadata({ latitude: 10, longitude: 20 })

    // Assert
    expect(Object.isFrozen(result)).toBe(true)
  })

  // ──────────────────────────────────────────────────────────────
  // parseVisualMetadata — absent visual metadata (backward compat)
  // ──────────────────────────────────────────────────────────────

  test('race without visual metadata parses successfully for backward compatibility', () => {
    // Arrange: race with no visual field
    const raceInput = {
      race_id: '2024-round-05',
      round_number: 5,
      event_name: 'Chinese Grand Prix',
      sessions: [readySession],
    }

    // Act
    const race = parseCatalogV2Race(raceInput)

    // Assert
    expect(race.visual).toBeUndefined()
  })

  test('full catalog without visual metadata in any race parses successfully', () => {
    // Act
    const parsed = parseCatalogV2(catalog())

    // Assert
    expect(parsed.races[0].visual).toBeUndefined()
  })

  // ──────────────────────────────────────────────────────────────
  // parseVisualMetadata — malformed / rejected visual metadata
  // ──────────────────────────────────────────────────────────────

  test('rejects latitude above 90', () => {
    expect(() => parseVisualMetadata({ latitude: 91, longitude: 0 })).toThrow('between -90 and 90')
  })

  test('rejects latitude below -90', () => {
    expect(() => parseVisualMetadata({ latitude: -91, longitude: 0 })).toThrow('between -90 and 90')
  })

  test('rejects longitude above 180', () => {
    expect(() => parseVisualMetadata({ latitude: 0, longitude: 181 })).toThrow('between -180 and 180')
  })

  test('rejects longitude below -180', () => {
    expect(() => parseVisualMetadata({ latitude: 0, longitude: -181 })).toThrow('between -180 and 180')
  })

  test('rejects NaN latitude', () => {
    expect(() => parseVisualMetadata({ latitude: NaN, longitude: 0 })).toThrow('finite')
  })

  test('rejects Infinity longitude', () => {
    expect(() => parseVisualMetadata({ latitude: 0, longitude: Infinity })).toThrow('finite')
  })

  test('rejects non-numeric latitude', () => {
    expect(() => parseVisualMetadata({ latitude: '45', longitude: 0 })).toThrow('finite')
  })

  test('rejects non-numeric longitude', () => {
    expect(() => parseVisualMetadata({ latitude: 0, longitude: '7' })).toThrow('finite')
  })

  test('rejects null latitude', () => {
    expect(() => parseVisualMetadata({ latitude: null, longitude: 0 })).toThrow('finite')
  })

  test('rejects missing latitude', () => {
    expect(() => parseVisualMetadata({ longitude: 0 })).toThrow('required')
  })

  test('rejects missing longitude', () => {
    expect(() => parseVisualMetadata({ latitude: 0 })).toThrow('required')
  })

  test('rejects non-object visual metadata', () => {
    expect(() => parseVisualMetadata(null)).toThrow('must be an object')
    expect(() => parseVisualMetadata('string')).toThrow('must be an object')
    expect(() => parseVisualMetadata(42)).toThrow('must be an object')
    expect(() => parseVisualMetadata([1, 2])).toThrow('must be an object')
  })

  test('rejects unknown fields in visual metadata', () => {
    expect(() => parseVisualMetadata({ latitude: 0, longitude: 0, extra: true })).toThrow('not allowed')
  })

  // ──────────────────────────────────────────────────────────────
  // parseVisualMetadata — unsafe circuitPreview pointer
  // ──────────────────────────────────────────────────────────────

  test('rejects circuitPreview with path traversal', () => {
    expect(() => parseVisualMetadata({
      latitude: 0,
      longitude: 0,
      circuitPreview: '../secret.json',
    })).toThrow('safe relative POSIX path')
  })

  test('rejects circuitPreview with absolute path', () => {
    expect(() => parseVisualMetadata({
      latitude: 0,
      longitude: 0,
      circuitPreview: '/etc/passwd',
    })).toThrow('safe relative POSIX path')
  })

  test('rejects circuitPreview with double-dot traversal in nested segment', () => {
    expect(() => parseVisualMetadata({
      latitude: 0,
      longitude: 0,
      circuitPreview: 'visuals/../../../etc/passwd',
    })).toThrow('safe relative POSIX path')
  })

  test('rejects circuitPreview with blank string', () => {
    expect(() => parseVisualMetadata({
      latitude: 0,
      longitude: 0,
      circuitPreview: '   ',
    })).toThrow('non-blank')
  })

  test('rejects circuitPreview with non-string value', () => {
    expect(() => parseVisualMetadata({
      latitude: 0,
      longitude: 0,
      circuitPreview: 123,
    })).toThrow('non-empty string')
  })

  test('rejects circuitPreview with backslash path separator', () => {
    expect(() => parseVisualMetadata({
      latitude: 0,
      longitude: 0,
      circuitPreview: 'visuals\\circuit.json',
    })).toThrow('safe relative POSIX path')
  })
})

// ──────────────────────────────────────────────────────────────
// parseCatalogV2Race with visual metadata — integration
// ──────────────────────────────────────────────────────────────

describe('parseCatalogV2Race with visual metadata', () => {
  test('parses a race record that includes valid visual metadata', () => {
    // Arrange
    const input = {
      race_id: '2024-round-05',
      round_number: 5,
      event_name: 'Chinese Grand Prix',
      visual: { latitude: 31.3389, longitude: 121.2198 },
      sessions: [readySession],
    }

    // Act
    const race = parseCatalogV2Race(input)

    // Assert
    expect(race.visual).toEqual({ latitude: 31.3389, longitude: 121.2198 })
  })

  test('parses a race with visual metadata including a circuit preview pointer', () => {
    // Arrange
    const input = {
      race_id: '2024-round-05',
      round_number: 5,
      event_name: 'Chinese Grand Prix',
      visual: { latitude: 31.3389, longitude: 121.2198, circuitPreview: 'visuals/shanghai-circuit.json' },
      sessions: [readySession],
    }

    // Act
    const race = parseCatalogV2Race(input)

    // Assert
    expect(race.visual?.circuitPreview).toBe('visuals/shanghai-circuit.json')
  })

  test('rejects a race with out-of-range coordinates in visual metadata', () => {
    // Arrange
    const input = {
      race_id: '2024-round-05',
      round_number: 5,
      event_name: 'Chinese Grand Prix',
      visual: { latitude: 100, longitude: 0 },
      sessions: [readySession],
    }

    // Act + Assert
    expect(() => parseCatalogV2Race(input)).toThrow('between -90 and 90')
  })

  test('rejects a race with an unsafe circuitPreview pointer in visual metadata', () => {
    // Arrange
    const input = {
      race_id: '2024-round-05',
      round_number: 5,
      event_name: 'Chinese Grand Prix',
      visual: { latitude: 0, longitude: 0, circuitPreview: '../escape.json' },
      sessions: [readySession],
    }

    // Act + Assert
    expect(() => parseCatalogV2Race(input)).toThrow('safe relative POSIX path')
  })

  test('full catalog with mixed visual and non-visual races parses correctly', () => {
    // Arrange
    const catalogData = catalog({
      races: [
        {
          race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix',
          visual: { latitude: 31.3389, longitude: 121.2198 },
          sessions: [readySession],
        },
        {
          race_id: '2024-round-06', round_number: 6, event_name: 'Japanese Grand Prix',
          sessions: [{ ...readySession, session_code: 'r', generation_id: '2024-round-06-session-race-mode-race', delivery_version: '2024-round-06-session-race-mode-race', browser_pointer: 'browser/2024-round-06/sessions/r/browser-current.json' }],
        },
      ],
    })

    // Act
    const parsed = parseCatalogV2(catalogData)

    // Assert
    expect(parsed.races[0].visual).toEqual({ latitude: 31.3389, longitude: 121.2198 })
    expect(parsed.races[1].visual).toBeUndefined()
  })
})

// ──────────────────────────────────────────────────────────────
// selectRace / selectSession — additional coverage
// ──────────────────────────────────────────────────────────────

describe('selectRace and selectSession', () => {
  test('selectRace returns the matching race or null', () => {
    // Arrange
    const parsed = parseCatalogV2(catalog())

    // Act + Assert
    expect(selectRace(parsed, '2024-round-05')?.race_id).toBe('2024-round-05')
    expect(selectRace(parsed, 'missing')).toBeNull()
    expect(selectRace(parsed, null)).toBeNull()
    expect(selectRace(parsed, undefined)).toBeNull()
  })

  test('selectSession returns the matching session or null', () => {
    // Arrange
    const parsed = parseCatalogV2(catalog())
    const race = parsed.races[0]

    // Act + Assert
    expect(selectSession(race, 'R')?.session_code).toBe('r')
    expect(selectSession(race, 'missing')).toBeNull()
    expect(selectSession(race, null)).toBeNull()
    expect(selectSession(race, undefined)).toBeNull()
  })

  test('selectReplaySession returns null when race is not found', () => {
    // Arrange
    const parsed = parseCatalogV2(catalog())

    // Act + Assert
    expect(selectReplaySession(parsed, 'missing', 'R')).toBeNull()
  })

  test('selectReplaySession returns null for an unready session', () => {
    // Arrange
    const unreadyCatalog = catalog({ races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix',
      sessions: [{ ...readySession, validated: false, canonical_pointer: null, browser_pointer: null }],
    }]})
    const parsed = parseCatalogV2(unreadyCatalog)

    // Act + Assert
    expect(selectReplaySession(parsed, '2024-round-05', 'R')).toBeNull()
  })
})

// ──────────────────────────────────────────────────────────────
// Strict active V2 identities across sprint and testing variants
// ──────────────────────────────────────────────────────────────

describe('strict active v2 catalog identities', () => {
  test.each([
    ['sprint', 'Sprint', 'sprint', 'sprint', 's'],
    ['sprint-qualifying', 'Sprint qualifying', 'sprint-qualifying', 'sprint-qualifying', 'sq'],
    ['sprint-shootout', 'Sprint shootout', 'sprint-shootout', 'sprint-shootout', 'ss'],
    ['testing', 'Testing', 'testing', 'testing', 'testing'],
  ] as const)('accepts the v2 %s session identity as replay-ready', (_label, name, identity, mode, code) => {
    // Arrange
    const session = {
      ...readySession, session_code: code, session_name: name,
      generation_id: `2024-round-05-session-${identity}-mode-${mode}`,
      delivery_version: `2024-round-05-session-${identity}-mode-${mode}`,
      browser_pointer: `browser/2024-round-05/sessions/${code}/browser-current.json`,
    }
    const race = { race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions: [session] }

    // Act
    const parsed = parseCatalogV2(catalog({ races: [race] }))
    const parsedSession = parsed.races[0].sessions[0]

    // Assert
    expect(parsedSession.session_code).toBe(code)
    expect(parsedSession.session_name).toBe(name)
    expect(isSessionReplayReady(parsedSession)).toBe(true)
    expect(getReplayReadySessions(parsed.races[0]).map((entry) => entry.session_code)).toEqual([code])
  })

  test('accepts every practice session identity with the mode practice', () => {
    // Arrange
    const sessions = (['practice-1', 'practice-2', 'practice-3'] as const).map((identity, index) => ({
      ...readySession,
      session_code: `fp${index + 1}`,
      session_name: `Practice ${index + 1}`,
      generation_id: `2024-round-05-session-${identity}-mode-practice`,
      delivery_version: `2024-round-05-session-${identity}-mode-practice`,
      browser_pointer: `browser/2024-round-05/sessions/fp${index + 1}/browser-current.json`,
    }))

    // Act
    const parsed = parseCatalogV2(catalog({ races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions,
    }]}))

    // Assert
    expect(parsed.races[0].sessions.map((session) => session.session_code)).toEqual(['fp1', 'fp2', 'fp3'])
    expect(parsed.races[0].sessions.every((session) => session.session_name.startsWith('Practice'))).toBe(true)
  })

  test('accepts publisher force and browser suffixes on a valid v2 identity', () => {
    const generationId = '2024-round-05-session-race-mode-race-force-1-browser-2'
    const session = {
      ...readySession,
      generation_id: generationId,
      delivery_version: generationId,
    }

    const parsed = parseCatalogV2(catalog({ races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions: [session],
    }] }))

    expect(parsed.races[0].sessions[0].generation_id).toBe(generationId)
    expect(isSessionReplayReady(parsed.races[0].sessions[0])).toBe(true)
  })

  test('rejects an unsupported suffix on an otherwise valid v2 identity', () => {
    const session = {
      ...readySession,
      generation_id: '2024-round-05-session-race-mode-race-retry-1',
    }

    expect(() => parseCatalogV2(catalog({ races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions: [session],
    }] }))).toThrow('generation_id is not a v2 identity')
  })

  test('rejects a v1 generation identity even when pointers are complete', () => {
    // Arrange
    const v1Session = {
      ...readySession,
      generation_id: '2024-round-05-session-race',
      delivery_version: null,
      canonical_pointer: null,
      browser_pointer: null,
    }

    // Act + Assert
    expect(() => parseCatalogV2(catalog({ races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions: [v1Session],
    }] }))).toThrow('generation_id is not a v2 identity')
  })

  test('rejects a generation identity whose year disagrees with the catalog year', () => {
    // Arrange
    const mismatched = { ...readySession, generation_id: '2025-round-05-session-race-mode-race', delivery_version: '2025-round-05-session-race-mode-race' }

    // Act + Assert
    expect(() => parseCatalogV2(catalog({ races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions: [mismatched],
    }] }))).toThrow('mixed-version identity')
  })

  test('rejects a generation identity whose round disagrees with the race round', () => {
    // Arrange
    const mismatched = { ...readySession, generation_id: '2024-round-06-session-race-mode-race', delivery_version: '2024-round-06-session-race-mode-race' }

    // Act + Assert
    expect(() => parseCatalogV2(catalog({ races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions: [mismatched],
    }] }))).toThrow('mixed-version identity')
  })

  test('rejects a generation identity whose mode disagrees with its identity', () => {
    // Arrange — identity race but mode practice is not a practice-1/2/3 exception
    const mismatched = { ...readySession, generation_id: '2024-round-05-session-race-mode-practice', delivery_version: '2024-round-05-session-race-mode-practice' }

    // Act + Assert
    expect(() => parseCatalogV2(catalog({ races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions: [mismatched],
    }] }))).toThrow('mixed-version identity')
  })

  test('rejects a session whose code disagrees with its generation identity', () => {
    // Arrange — qualifying identity but the session_code claims race
    const disagreeing = { ...readySession, session_code: 'r', generation_id: '2024-round-05-session-qualifying-mode-qualifying', delivery_version: '2024-round-05-session-qualifying-mode-qualifying', browser_pointer: 'browser/2024-round-05/sessions/r/browser-current.json' }

    // Act + Assert
    expect(() => parseCatalogV2(catalog({ races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions: [disagreeing],
    }] }))).toThrow('disagrees with generation_id')
  })

  test('rejects a race whose identity disagrees with its catalog round', () => {
    // Arrange — race_id claims round 5 but round_number is 6
    const race = { race_id: '2024-round-05', round_number: 6, event_name: 'Chinese Grand Prix', sessions: [readySession] }

    // Act + Assert
    expect(() => parseCatalogV2(catalog({ races: [race] }))).toThrow('mixed-version identity')
  })
})

// ──────────────────────────────────────────────────────────────
// Invalid mode/artifact combinations and replay-readiness gating
// ──────────────────────────────────────────────────────────────

describe('catalog v2 artifact reference gating', () => {
  test('rejects a validated session without complete artifact references', () => {
    // Arrange — validated with no browser pointer
    const incomplete = { ...readySession, canonical_pointer: null, browser_pointer: null }

    // Act + Assert
    expect(() => parseCatalogV2(catalog({ races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions: [incomplete],
    }] }))).toThrow('validated sessions require complete artifact references')
  })

  test('rejects an unvalidated session that claims pointer paths', () => {
    // Arrange — validated false but a browser pointer is present
    const claiming = { ...readySession, validated: false }

    // Act + Assert
    expect(() => parseCatalogV2(catalog({ races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions: [claiming],
    }] }))).toThrow('unvalidated sessions must not claim pointer paths')
  })

  test('rejects pointers without generation and delivery metadata', () => {
    // Arrange — browser pointer present but generation identity missing
    const missing = { ...readySession, generation_id: null, delivery_version: null }

    // Act + Assert
    expect(() => parseCatalogV2(catalog({ races: [{
      race_id: '2024-round-05', round_number: 5, event_name: 'Chinese Grand Prix', sessions: [missing],
    }] }))).toThrow('pointers require generation_id and delivery_version')
  })

  test('isSessionReplayReady is false for missing or unsafe metadata', () => {
    // Arrange — each session misses exactly one replay-readiness requirement
    const cases: Array<[string, Record<string, unknown>]> = [
      ['unvalidated', { validated: false }],
      ['v1 generation', { generation_id: '2024-round-05-session-race', delivery_version: null }],
      ['missing delivery version', { delivery_version: null, browser_pointer: null }],
      ['missing browser pointer', { browser_pointer: null }],
      ['unsafe browser pointer', { browser_pointer: '../escape/browser-current.json' }],
    ]

    // Act + Assert
    for (const [_description, overrides] of cases) {
      expect(isSessionReplayReady({ ...readySession, ...overrides })).toBe(false)
    }
  })

  test('selectReplaySession rejects unsafe and mismatched browser pointers', () => {
    // Act + Assert — unsafe traversal and a session-code identity mismatch
    expect(() => resolveBrowserPointer(
      'browser/2024-round-05/sessions/r/../browser-current.json', '2024-round-05', 'r',
    )).toThrow('Unsafe replay-data path')
    expect(() => resolveBrowserPointer(
      'browser/2024-round-05/sessions/r/browser-current.json', '2024-round-05', 'q',
    )).toThrow('session identity disagrees')
  })
})
