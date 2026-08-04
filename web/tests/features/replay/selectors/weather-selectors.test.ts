import { describe, expect, test } from 'vitest'
import type { WeatherSidecar } from '../../../../src/data/replay/types'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'
import {
  causalWeatherSelector,
  selectWeather,
  STALE_WEATHER_MS,
} from '../../../../src/features/replay/selectors/weather-selectors'

/** Sparse native-cadence rows (~1/min) with complete measurements at each timestamp. */
function sparseSidecar(overrides: Partial<WeatherSidecar> = {}): WeatherSidecar {
  return {
    contractVersion: 'v1',
    fixtureId: 'weather-fixture',
    timeMs: [10_000, 60_000, 120_000],
    airTempC: [20.0, 21.0, 22.0],
    humidityPct: [60, 59, 58],
    pressureMbar: [1014.0, 1013.5, 1013.0],
    rainfall: [false, false, true],
    trackTempC: [28.0, 30.0, 32.0],
    windDirectionDeg: [90, 180, 315],
    windSpeedMps: [2.0, 3.0, 4.0],
    ...overrides,
  }
}

/** Single observation at t=0 used to probe age/status boundaries without extra rows. */
function singleObservation(overrides: Partial<WeatherSidecar> = {}): WeatherSidecar {
  return {
    contractVersion: 'v1',
    fixtureId: 'weather-fixture',
    timeMs: [0],
    airTempC: [21.0],
    humidityPct: [60],
    pressureMbar: [1013.2],
    rainfall: [false],
    trackTempC: [30.5],
    windDirectionDeg: [180],
    windSpeedMps: [3.2],
    ...overrides,
  }
}

function snapshotAt(sessionTimeMs: number): ReplaySnapshot {
  return {
    sessionTimeMs,
    drivers: {},
    leaderboardOrder: null,
    trackStatusCode: null,
    weatherState: null,
    events: [],
  }
}

describe('causalWeatherSelector unavailable states', () => {
  test('returns no-sidecar unavailable for null and undefined sidecars', () => {
    // Arrange — a replay without any weather artifact.
    const sidecars = [null, undefined]

    // Act & Assert — every measurement stays null and no value is fabricated.
    for (const sidecar of sidecars) {
      const selection = causalWeatherSelector(sidecar, 10_000)
      expect(selection.status).toBe('unavailable')
      expect(selection.reason).toBe('no-sidecar')
      expect(selection.observationTimeMs).toBeNull()
      expect(selection.ageMs).toBeNull()
      expect(selection.airTempC).toBeNull()
      expect(selection.humidityPct).toBeNull()
      expect(selection.pressureMbar).toBeNull()
      expect(selection.rainfall).toBeNull()
      expect(selection.trackTempC).toBeNull()
      expect(selection.windDirectionDeg).toBeNull()
      expect(selection.windSpeedMps).toBeNull()
    }
  })

  test('returns before-first-observation unavailable ahead of the earliest sample', () => {
    // Arrange — the cursor precedes the first native weather row.
    const sidecar = sparseSidecar()

    // Act
    const selection = causalWeatherSelector(sidecar, 9_999)

    // Assert
    expect(selection.status).toBe('unavailable')
    expect(selection.reason).toBe('before-first-observation')
    expect(selection.observationTimeMs).toBeNull()
    expect(selection.ageMs).toBeNull()
    expect(selection.airTempC).toBeNull()
    expect(selection.windDirectionDeg).toBeNull()
  })

  test('returns no-usable-measurements unavailable when the causal row is all null', () => {
    // Arrange — one row whose every measurement is missing.
    const sidecar = sparseSidecar({
      timeMs: [10_000],
      airTempC: [null],
      humidityPct: [null],
      pressureMbar: [null],
      rainfall: [null],
      trackTempC: [null],
      windDirectionDeg: [null],
      windSpeedMps: [null],
    })

    // Act
    const selection = causalWeatherSelector(sidecar, 10_000)

    // Assert — the row is located but carries no usable value; fields stay null.
    expect(selection.status).toBe('unavailable')
    expect(selection.reason).toBe('no-usable-measurements')
    expect(selection.observationTimeMs).toBe(10_000)
    expect(selection.ageMs).toBe(0)
    expect(selection.airTempC).toBeNull()
    expect(selection.humidityPct).toBeNull()
    expect(selection.pressureMbar).toBeNull()
    expect(selection.rainfall).toBeNull()
    expect(selection.trackTempC).toBeNull()
    expect(selection.windDirectionDeg).toBeNull()
    expect(selection.windSpeedMps).toBeNull()
  })
})

describe('causalWeatherSelector sparse sampling', () => {
  test('selects the exact row at an observation timestamp', () => {
    // Arrange — cursor lands on the first native row.
    const sidecar = sparseSidecar()

    // Act
    const selection = causalWeatherSelector(sidecar, 10_000)

    // Assert — row 0 values are exposed as fresh with zero age.
    expect(selection.status).toBe('fresh')
    expect(selection.reason).toBe('fresh')
    expect(selection.observationTimeMs).toBe(10_000)
    expect(selection.ageMs).toBe(0)
    expect(selection.airTempC).toBe(20.0)
    expect(selection.humidityPct).toBe(60)
    expect(selection.pressureMbar).toBe(1014.0)
    expect(selection.rainfall).toBe(false)
    expect(selection.trackTempC).toBe(28.0)
    expect(selection.windDirectionDeg).toBe(90)
    expect(selection.windSpeedMps).toBe(2.0)
  })

  test('uses the last-known row between sparse samples without interpolating', () => {
    // Arrange — cursor sits between the first and second native rows.
    const sidecar = sparseSidecar()

    // Act
    const selection = causalWeatherSelector(sidecar, 35_000)

    // Assert — the earlier row is repeated exactly, never averaged with the next one.
    expect(selection.observationTimeMs).toBe(10_000)
    expect(selection.ageMs).toBe(25_000)
    expect(selection.airTempC).toBe(20.0)
    expect(selection.humidityPct).toBe(60)
    expect(selection.windDirectionDeg).toBe(90)
    expect(selection.windSpeedMps).toBe(2.0)
  })

  test('keeps the last-known row until the next observation timestamp', () => {
    // Arrange — cursor one millisecond before the second row.
    const sidecar = sparseSidecar()

    // Act
    const selection = causalWeatherSelector(sidecar, 59_999)

    // Assert — the first row is still current and fresh.
    expect(selection.observationTimeMs).toBe(10_000)
    expect(selection.ageMs).toBe(49_999)
    expect(selection.airTempC).toBe(20.0)
    expect(selection.status).toBe('fresh')
  })

  test('steps to the next row exactly at its timestamp', () => {
    // Arrange — cursor lands on the second native row.
    const sidecar = sparseSidecar()

    // Act
    const selection = causalWeatherSelector(sidecar, 60_000)

    // Assert — row 1 values replace the previous row at the boundary.
    expect(selection.observationTimeMs).toBe(60_000)
    expect(selection.ageMs).toBe(0)
    expect(selection.airTempC).toBe(21.0)
    expect(selection.humidityPct).toBe(59)
    expect(selection.windDirectionDeg).toBe(180)
    expect(selection.rainfall).toBe(false)
  })

  test('never leaks observations after the cursor', () => {
    // Arrange — cursor before the second row.
    const sidecar = sparseSidecar()

    // Act
    const selection = causalWeatherSelector(sidecar, 30_000)

    // Assert — later rows (180°, rain at 120s) must not influence the selection.
    expect(selection.observationTimeMs).toBe(10_000)
    expect(selection.windDirectionDeg).toBe(90)
    expect(selection.windSpeedMps).toBe(2.0)
    expect(selection.rainfall).toBe(false)
  })

  test('supports backward seeks and re-observes earlier rows deterministically', () => {
    // Arrange — a sidecar and a cursor that jumps forward then back.
    const sidecar = sparseSidecar()

    // Act — seek to the final row.
    const forward = causalWeatherSelector(sidecar, 120_000)
    expect(forward.observationTimeMs).toBe(120_000)
    expect(forward.windDirectionDeg).toBe(315)
    expect(forward.rainfall).toBe(true)

    // Act — seek backward between the first two rows.
    const backward = causalWeatherSelector(sidecar, 35_000)
    expect(backward.observationTimeMs).toBe(10_000)
    expect(backward.windDirectionDeg).toBe(90)
    expect(backward.rainfall).toBe(false)

    // Act — seek backward before the first row.
    const beforeStart = causalWeatherSelector(sidecar, 5_000)
    expect(beforeStart.status).toBe('unavailable')
    expect(beforeStart.reason).toBe('before-first-observation')
  })

  test('accepts a replay snapshot boundary and uses its session time', () => {
    // Arrange
    const sidecar = sparseSidecar()

    // Act — a number boundary and an equivalent snapshot boundary agree.
    const numberBoundary = causalWeatherSelector(sidecar, 35_000)
    const snapshotBoundary = causalWeatherSelector(sidecar, snapshotAt(35_000))

    // Assert
    expect(snapshotBoundary).toEqual(numberBoundary)
    expect(snapshotBoundary.observationTimeMs).toBe(10_000)
  })
})

describe('causalWeatherSelector fresh, stale, and boundary states', () => {
  test('marks fresh observations at and under the staleness threshold', () => {
    // Arrange — a single observation at t=0.
    const sidecar = singleObservation()

    // Act — cursor exactly at the 90s threshold.
    const selection = causalWeatherSelector(sidecar, STALE_WEATHER_MS)

    // Assert — equality is fresh; only strictly older ages are stale.
    expect(selection.status).toBe('fresh')
    expect(selection.reason).toBe('fresh')
    expect(selection.ageMs).toBe(STALE_WEATHER_MS)
    expect(selection.airTempC).toBe(21.0)
  })

  test('marks stale observations one millisecond past the threshold', () => {
    // Arrange — a single observation at t=0.
    const sidecar = singleObservation()

    // Act
    const selection = causalWeatherSelector(sidecar, STALE_WEATHER_MS + 1)

    // Assert
    expect(selection.status).toBe('stale')
    expect(selection.reason).toBe('stale')
    expect(selection.ageMs).toBe(STALE_WEATHER_MS + 1)
  })

  test('reports stale status and age while retaining raw values for the view to mask', () => {
    // Arrange — one observation well past the threshold.
    const sidecar = singleObservation()

    // Act
    const selection = causalWeatherSelector(sidecar, 200_000)

    // Assert — selector metadata remains rich; the panel applies fail-closed rendering.
    expect(selection.status).toBe('stale')
    expect(selection.reason).toBe('stale')
    expect(selection.observationTimeMs).toBe(0)
    expect(selection.ageMs).toBe(200_000)
    expect(selection.airTempC).toBe(21.0)
    expect(selection.windDirectionDeg).toBe(180)
  })
})

describe('causalWeatherSelector partial nulls and fabricated zeros', () => {
  test('preserves partial nulls while exposing the usable measurements', () => {
    // Arrange — the causal row lacks air temperature but keeps humidity and wind.
    const sidecar = sparseSidecar({
      timeMs: [60_000],
      airTempC: [null],
      humidityPct: [59],
      pressureMbar: [1013.5],
      rainfall: [false],
      trackTempC: [30.0],
      windDirectionDeg: [null],
      windSpeedMps: [3.0],
    })

    // Act
    const selection = causalWeatherSelector(sidecar, 60_000)

    // Assert — missing fields stay null (never 0) and present fields are exact.
    expect(selection.status).toBe('fresh')
    expect(selection.airTempC).toBeNull()
    expect(selection.windDirectionDeg).toBeNull()
    expect(selection.humidityPct).toBe(59)
    expect(selection.windSpeedMps).toBe(3.0)
    expect(selection.pressureMbar).toBe(1013.5)
  })

  test('returns a stale partial row as stale without filling its nulls', () => {
    // Arrange — one sparse partial row read long after its timestamp.
    const sidecar = sparseSidecar({
      timeMs: [10_000],
      airTempC: [null],
      humidityPct: [null],
      pressureMbar: [null],
      rainfall: [null],
      trackTempC: [null],
      windDirectionDeg: [90],
      windSpeedMps: [2.0],
    })

    // Act
    const selection = causalWeatherSelector(sidecar, 200_000)

    // Assert — wind data is usable, the row is stale, and nothing is interpolated.
    expect(selection.status).toBe('stale')
    expect(selection.reason).toBe('stale')
    expect(selection.observationTimeMs).toBe(10_000)
    expect(selection.windDirectionDeg).toBe(90)
    expect(selection.windSpeedMps).toBe(2.0)
    expect(selection.airTempC).toBeNull()
  })

  test('never fabricates zero values in any unavailable state', () => {
    // Arrange — every unavailable reason from a fixed sidecar set.
    const noSidecar = causalWeatherSelector(undefined, 10_000)
    const beforeFirst = causalWeatherSelector(sparseSidecar(), 0)
    const noMeasurements = causalWeatherSelector(singleObservation({
      airTempC: [null],
      humidityPct: [null],
      pressureMbar: [null],
      rainfall: [null],
      trackTempC: [null],
      windDirectionDeg: [null],
      windSpeedMps: [null],
    }), 0)

    // Act & Assert — unavailable selections expose only nulls, never 0 or defaults.
    const selections = [noSidecar, beforeFirst, noMeasurements]
    for (const selection of selections) {
      expect(selection.status).toBe('unavailable')
      expect(selection.airTempC).toBeNull()
      expect(selection.humidityPct).toBeNull()
      expect(selection.pressureMbar).toBeNull()
      expect(selection.rainfall).toBeNull()
      expect(selection.trackTempC).toBeNull()
      expect(selection.windDirectionDeg).toBeNull()
      expect(selection.windSpeedMps).toBeNull()
    }
  })
})

describe('selectWeather alias', () => {
  test('exposes the same causal selection behavior as the named selector', () => {
    // Arrange
    const sidecar = sparseSidecar()

    // Act
    const selection = selectWeather(sidecar, 35_000)

    // Assert
    expect(selectWeather).toBe(causalWeatherSelector)
    expect(selection).toEqual(causalWeatherSelector(sidecar, 35_000))
    expect(selection.observationTimeMs).toBe(10_000)
    expect(selection.airTempC).toBe(20.0)
  })
})
