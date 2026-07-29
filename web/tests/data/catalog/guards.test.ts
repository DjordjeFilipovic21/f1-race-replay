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
          sessions: [{ ...readySession, session_code: 'R2' }],
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
