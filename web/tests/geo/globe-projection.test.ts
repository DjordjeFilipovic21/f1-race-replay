import { describe, expect, it } from 'vitest'
import {
  buildGraticulePath,
  computeRotation,
  createOrthographicProjection,
  worldLand,
} from '../../src/geo/globe-projection'

function expectTupleCloseTo(actual: readonly number[], expected: readonly number[]): void {
  expect(actual).toHaveLength(expected.length)
  expected.forEach((value, index) => {
    expect(actual[index]).toBeCloseTo(value, 10)
  })
}

// ──────────────────────────────────────────────────────────────
// computeRotation — positive cases
// ──────────────────────────────────────────────────────────────

describe('computeRotation', () => {
  it('returns the inverse rotation for a selected race coordinate', () => {
    // Arrange: Bahrain GP coordinates
    const latitude = 26.0325
    const longitude = 50.5106

    // Act
    const result = computeRotation(latitude, longitude)

    // Assert
    expectTupleCloseTo(result, [-50.5106, -26.0325, 0])
  })

  it('keeps longitude changes on the shortest rotation path', () => {
    // Arrange: near-antipodal longitudes
    const from: [number, number, number] = [179, 0, 0]

    // Act
    const result = computeRotation(0, 179, from)

    // Assert
    expectTupleCloseTo(result, [181, 0, 0])
  })

  it('handles the prime meridian and equator', () => {
    // Act
    const result = computeRotation(0, 0)

    // Assert
    expectTupleCloseTo(result, [0, 0, 0])
  })

  it('handles boundary coordinates at ±90 latitude and ±180 longitude', () => {
    // Act + Assert
    expectTupleCloseTo(computeRotation(90, 0), [0, -90, 0])
    expectTupleCloseTo(computeRotation(-90, 0), [0, 90, 0])
    expectTupleCloseTo(computeRotation(0, 180), [-180, 0, 0])
    expectTupleCloseTo(computeRotation(0, -180), [-180, 0, 0])
  })

  // ──────────────────────────────────────────────────────────────
  // computeRotation — negative / edge cases
  // ──────────────────────────────────────────────────────────────

  it('rejects latitude outside ±90', () => {
    expect(() => computeRotation(91, 0)).toThrow(RangeError)
    expect(() => computeRotation(-91, 0)).toThrow(RangeError)
  })

  it('rejects longitude outside ±180', () => {
    expect(() => computeRotation(0, 181)).toThrow(RangeError)
    expect(() => computeRotation(0, -181)).toThrow(RangeError)
  })

  it('rejects NaN and Infinity coordinates', () => {
    expect(() => computeRotation(NaN, 0)).toThrow(RangeError)
    expect(() => computeRotation(0, Infinity)).toThrow(RangeError)
    expect(() => computeRotation(-Infinity, 0)).toThrow(RangeError)
  })

  it('rejects non-finite fromRotation values', () => {
    expect(() => computeRotation(0, 0, [NaN, 0, 0])).toThrow(RangeError)
    expect(() => computeRotation(0, 0, [0, Infinity, 0])).toThrow(RangeError)
  })
})

// ──────────────────────────────────────────────────────────────
// createOrthographicProjection — positive cases
// ──────────────────────────────────────────────────────────────

describe('createOrthographicProjection', () => {
  it('creates a deterministic orthographic projection without a DOM', () => {
    // Arrange + Act
    const first = createOrthographicProjection(0, 0, { width: 400, height: 300 })
    const second = createOrthographicProjection(0, 0, { width: 400, height: 300 })

    // Assert
    expectTupleCloseTo(first.rotate(), [0, 0, 0])
    expect(first.translate()).toEqual([200, 150])
    expect(first.scale()).toBe(135)
    expectTupleCloseTo(second.rotate(), first.rotate())
    expect(second.translate()).toEqual(first.translate())
  })

  it('accepts a square size shorthand for projection dimensions', () => {
    // Act
    const projection = createOrthographicProjection(0, 0, 400)

    // Assert
    expect(projection.translate()).toEqual([200, 200])
  })

  it('uses custom scale and translate when provided', () => {
    // Act
    const projection = createOrthographicProjection(0, 0, {
      width: 400,
      height: 400,
      scale: 150,
      translate: [10, 20] as const,
    })

    // Assert
    expect(projection.scale()).toBe(150)
    expect(projection.translate()).toEqual([10, 20])
  })

  it('clips geometry beyond 90 degrees from the projection centre', () => {
    // Act
    const projection = createOrthographicProjection(0, 0, { width: 400, height: 400 })

    // Assert
    expect(projection.clipAngle()).toBe(90)
  })

  // ──────────────────────────────────────────────────────────────
  // createOrthographicProjection — negative / edge cases
  // ──────────────────────────────────────────────────────────────

  it('rejects out-of-range coordinates', () => {
    expect(() => createOrthographicProjection(91, 0, { width: 400, height: 400 })).toThrow(RangeError)
    expect(() => createOrthographicProjection(0, 181, { width: 400, height: 400 })).toThrow(RangeError)
  })

  it('rejects non-finite coordinates', () => {
    expect(() => createOrthographicProjection(NaN, 0, { width: 400, height: 400 })).toThrow(RangeError)
    expect(() => createOrthographicProjection(0, Infinity, { width: 400, height: 400 })).toThrow(RangeError)
  })

  it('rejects zero or negative width and height', () => {
    expect(() => createOrthographicProjection(0, 0, { width: 0, height: 400 })).toThrow(RangeError)
    expect(() => createOrthographicProjection(0, 0, { width: 400, height: -1 })).toThrow(RangeError)
    expect(() => createOrthographicProjection(0, 0, 0)).toThrow(RangeError)
  })

  it('rejects non-finite translate values', () => {
    expect(() => createOrthographicProjection(0, 0, {
      width: 400,
      height: 400,
      translate: [NaN, 0] as unknown as readonly [number, number],
    })).toThrow(RangeError)
  })

  it('rejects zero or negative scale', () => {
    expect(() => createOrthographicProjection(0, 0, {
      width: 400,
      height: 400,
      scale: 0,
    })).toThrow(RangeError)
    expect(() => createOrthographicProjection(0, 0, {
      width: 400,
      height: 400,
      scale: -10,
    })).toThrow(RangeError)
  })
})

// ──────────────────────────────────────────────────────────────
// buildGraticulePath — positive cases
// ──────────────────────────────────────────────────────────────

describe('buildGraticulePath', () => {
  it('builds stable SVG path data for a fifteen-degree graticule', () => {
    // Arrange
    const projection = createOrthographicProjection(0, 0, { width: 400, height: 400 })

    // Act
    const first = buildGraticulePath(projection)
    const second = buildGraticulePath(projection)

    // Assert
    expect(first).toBe(second)
    expect(first.startsWith('M')).toBe(true)
  })

  it('respects a custom step parameter', () => {
    // Arrange
    const projection = createOrthographicProjection(0, 0, { width: 400, height: 400 })

    // Act
    const path10 = buildGraticulePath(projection, 10)
    const path30 = buildGraticulePath(projection, 30)

    // Assert: finer graticule produces a longer path string
    expect(path10.length).toBeGreaterThan(path30.length)
  })

  // ──────────────────────────────────────────────────────────────
  // buildGraticulePath — negative / edge cases
  // ──────────────────────────────────────────────────────────────

  it('rejects a non-projection argument', () => {
    expect(() => buildGraticulePath(null as unknown as Parameters<typeof buildGraticulePath>[0])).toThrow(TypeError)
    expect(() => buildGraticulePath('not a projection' as unknown as Parameters<typeof buildGraticulePath>[0])).toThrow(TypeError)
  })

  it('rejects zero or negative step', () => {
    const projection = createOrthographicProjection(0, 0, { width: 400, height: 400 })
    expect(() => buildGraticulePath(projection, 0)).toThrow(RangeError)
    expect(() => buildGraticulePath(projection, -5)).toThrow(RangeError)
  })
})

// ──────────────────────────────────────────────────────────────
// worldLand — positive case
// ──────────────────────────────────────────────────────────────

describe('worldLand', () => {
  it('exposes bundled world-atlas countries as GeoJSON features', () => {
    // Assert
    expect(worldLand.type).toBe('FeatureCollection')
    expect(worldLand.features.length).toBeGreaterThan(0)
    expect(worldLand.features[0]?.type).toBe('Feature')
  })
})
