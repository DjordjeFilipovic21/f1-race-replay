import { describe, expect, it } from 'vitest'
import type { ReplaySource } from '../../src/data/replay/types'
import {
  calculatePathBounds,
  calculatePathViewBox,
  loadCircuitPreview,
  normalizeSvgPath,
} from '../../src/geo/circuit-preview'

function sourceFor(value: unknown): ReplaySource {
  return {
    read: async () => new TextEncoder().encode(JSON.stringify(value)),
  }
}

function sourceError(message: string): ReplaySource {
  return {
    read: async () => { throw new Error(message) },
  }
}

// ──────────────────────────────────────────────────────────────
// normalizeSvgPath — positive cases
// ──────────────────────────────────────────────────────────────

describe('normalizeSvgPath', () => {
  it('normalizes valid path commands and numeric separators', () => {
    // Act
    const result = normalizeSvgPath('m-1.5,.25e+2 l 3 -4 z')

    // Assert
    expect(result).toBe('m -1.5 25 l 3 -4 z')
  })

  it('handles the full set of SVG path commands', () => {
    // Arrange: M, L, H, V, C, S, Q, T, A, Z
    const path = 'M 0 0 L 10 0 H 20 V 10 C 30 10 30 20 20 20 S 10 20 0 20 Q 5 15 0 10 T 0 0 A 5 5 0 0 1 5 5 Z'

    // Act
    const result = normalizeSvgPath(path)

    // Assert: should produce a valid normalized string
    expect(result.startsWith('M')).toBe(true)
    expect(result.endsWith('Z')).toBe(true)
  })

  // ──────────────────────────────────────────────────────────────
  // normalizeSvgPath — negative / edge cases
  // ──────────────────────────────────────────────────────────────

  it('rejects blank or empty path data', () => {
    expect(() => normalizeSvgPath('')).toThrow('non-blank')
    expect(() => normalizeSvgPath('   ')).toThrow('non-blank')
  })

  it('rejects path data that does not start with M', () => {
    expect(() => normalizeSvgPath('L 10 20')).toThrow('must begin with M')
  })

  it('rejects unsupported SVG path commands', () => {
    expect(() => normalizeSvgPath('M 0 0 X 10 20')).toThrow('unsupported token')
  })

  it('rejects path commands with incomplete parameters', () => {
    expect(() => normalizeSvgPath('M 0 0 L 10')).toThrow('incomplete parameters')
  })

  it('rejects non-numeric tokens in path data', () => {
    expect(() => normalizeSvgPath('M 0 0 L abc 20')).toThrow('unsupported token')
  })

  it('rejects arc commands with negative radii', () => {
    expect(() => normalizeSvgPath('M 0 0 A -5 5 0 0 1 10 10')).toThrow('radii must not be negative')
  })

  it('rejects arc commands with invalid flags', () => {
    expect(() => normalizeSvgPath('M 0 0 A 5 5 0 2 1 10 10')).toThrow('arc flags must be 0 or 1')
  })
})

// ──────────────────────────────────────────────────────────────
// calculatePathBounds / calculatePathViewBox — positive cases
// ──────────────────────────────────────────────────────────────

describe('calculatePathBounds', () => {
  it('calculates a deterministic bounds viewBox without a DOM', () => {
    // Act + Assert
    const bounds = calculatePathBounds('M 0 0 C 10 10 20 -10 30 0')
    expect(bounds.minX).toBeCloseTo(0, 12)
    expect(bounds.minY).toBeCloseTo(-2.886751345948129, 12)
    expect(bounds.width).toBeCloseTo(30, 12)
    expect(bounds.height).toBeCloseTo(5.773502691896258, 12)
    expect(calculatePathViewBox('M 0 0 L 10 20 Z')).toBe('0 0 10 20')
  })

  it('adds padding for degenerate single-point paths', () => {
    // Arrange: a single-point path (M with no geometry after)
    const bounds = calculatePathBounds('M 5 5 Z')

    // Assert: padding ensures a minimum 1x1 area
    expect(bounds.width).toBeGreaterThanOrEqual(1)
    expect(bounds.height).toBeGreaterThanOrEqual(1)
  })

  // ──────────────────────────────────────────────────────────────
  // calculatePathBounds — negative / edge cases
  // ──────────────────────────────────────────────────────────────

  it('rejects blank path data', () => {
    expect(() => calculatePathBounds('')).toThrow('non-blank')
  })

  it('rejects path data with no drawable geometry', () => {
    expect(() => calculatePathBounds('M')).toThrow('no parameters')
  })

  it('rejects automatic bounds for arc paths instead of returning endpoint-only geometry', () => {
    const arcPaths = [
      'M 150 50 A 100 100 0 1 1 149.9999 50',
      'M 150 50 a 100 100 0 1 1 -0.0001 0',
    ]

    for (const arcPath of arcPaths) {
      expect(() => calculatePathBounds(arcPath)).toThrow('explicit viewBox or bounds')
      expect(() => calculatePathViewBox(arcPath)).toThrow('automatic arc bounds are not supported')
    }
  })
})

// ──────────────────────────────────────────────────────────────
// loadCircuitPreview — positive cases
// ──────────────────────────────────────────────────────────────

describe('loadCircuitPreview', () => {
  it('loads the exact JSON contract through the injected ReplaySource', async () => {
    // Arrange
    const source = sourceFor({
      pathData: 'M0 0 L10 20 Z',
      bounds: { minX: 0, minY: 0, maxX: 10, maxY: 20 },
    })

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ pathData: 'M 0 0 L 10 20 Z', viewBox: '0 0 10 20' })
  })

  it('computes viewBox when the contract omits embedded bounds', async () => {
    // Arrange
    const source = sourceFor({ pathData: 'M-2,-3 h 8 v 6 z' })

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ pathData: 'M -2 -3 h 8 v 6 z', viewBox: '-2 -3 8 6' })
  })

  it('accepts a viewBox string format', async () => {
    // Arrange
    const source = sourceFor({ pathData: 'M0 0 L10 20 Z', viewBox: '0 0 10 20' })

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ pathData: 'M 0 0 L 10 20 Z', viewBox: '0 0 10 20' })
  })

  it('requires explicit bounds for arc paths', async () => {
    const arcPath = 'M 150 50 A 100 100 0 1 1 149.9999 50'

    const missingBounds = await loadCircuitPreview(sourceFor({ pathData: arcPath }), 'visuals/circuit.json')
    const explicitBounds = await loadCircuitPreview(
      sourceFor({ pathData: arcPath, viewBox: '50 -50 200 200' }),
      'visuals/circuit.json',
    )

    expect(missingBounds).toEqual({
      error: true,
      message: expect.stringContaining('explicit viewBox or bounds'),
    })
    expect(explicitBounds).toEqual({ pathData: 'M 150 50 A 100 100 0 1 1 149.9999 50', viewBox: '50 -50 200 200' })
  })

  it('accepts a bounds object with minX/minY/width/height', async () => {
    // Arrange
    const source = sourceFor({
      pathData: 'M0 0 L10 20 Z',
      bounds: { minX: 0, minY: 0, width: 10, height: 20 },
    })

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ pathData: 'M 0 0 L 10 20 Z', viewBox: '0 0 10 20' })
  })

  it('accepts a 4-element number array for bounds', async () => {
    // Arrange
    const source = sourceFor({
      pathData: 'M0 0 L10 20 Z',
      bounds: [0, 0, 10, 20],
    })

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ pathData: 'M 0 0 L 10 20 Z', viewBox: '0 0 10 20' })
  })

  // ──────────────────────────────────────────────────────────────
  // loadCircuitPreview — negative / edge cases
  // ──────────────────────────────────────────────────────────────

  it('rejects XML, scripts, URLs, and text payloads', async () => {
    // Arrange
    const dangerousPaths = [
      '<path d="M0 0"/>',
      'M0 0;script',
      'M0 0 url(javascript:alert(1))',
      'M0 0 text',
    ]

    // Act + Assert
    for (const pathData of dangerousPaths) {
      const result = await loadCircuitPreview(sourceFor({ pathData }), 'visuals/circuit.json')
      expect('error' in result && result.error).toBe(true)
    }
  })

  it('rejects unsafe pointers and malformed bounds as explicit errors', async () => {
    // Act
    const unsafe = await loadCircuitPreview(sourceFor({ pathData: 'M0 0 L1 1' }), '../circuit.json')
    const malformed = await loadCircuitPreview(
      sourceFor({ pathData: 'M0 0 L1 1', viewBox: { minX: 0, minY: 0, width: 0, height: 1 } }),
      'visuals/circuit.json',
    )

    // Assert
    expect(unsafe).toEqual({ error: true, message: expect.stringContaining('Unsafe replay-data path') })
    expect(malformed).toEqual({ error: true, message: expect.stringContaining('positive width') })
  })

  it('rejects unknown JSON fields instead of accepting arbitrary SVG metadata', async () => {
    // Arrange
    const source = sourceFor({ pathData: 'M0 0 L1 1', script: '<script>alert(1)</script>' })

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ error: true, message: expect.stringContaining('unsupported field') })
  })

  it('rejects blank pathData in the JSON asset', async () => {
    // Arrange
    const source = sourceFor({ pathData: '   ' })

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ error: true, message: expect.stringContaining('non-blank') })
  })

  it('rejects missing pathData field', async () => {
    // Arrange
    const source = sourceFor({ viewBox: '0 0 10 10' })

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ error: true, message: expect.stringContaining('required') })
  })

  it('rejects non-object JSON payloads', async () => {
    // Arrange
    const source = sourceFor('not an object')

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ error: true, message: expect.stringContaining('must be an object') })
  })

  it('rejects array JSON payloads', async () => {
    // Arrange
    const source = sourceFor([1, 2, 3])

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ error: true, message: expect.stringContaining('must be an object') })
  })

  it('rejects both viewBox and bounds provided simultaneously', async () => {
    // Arrange
    const source = sourceFor({
      pathData: 'M0 0 L10 20 Z',
      viewBox: '0 0 10 20',
      bounds: { minX: 0, minY: 0, maxX: 10, maxY: 20 },
    })

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ error: true, message: expect.stringContaining('viewBox or bounds, not both') })
  })

  it('rejects bounds with negative width or height', async () => {
    // Arrange
    const source = sourceFor({
      pathData: 'M0 0 L10 20 Z',
      bounds: { minX: 0, minY: 0, width: -10, height: 20 },
    })

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ error: true, message: expect.stringContaining('positive width') })
  })

  it('rejects bounds with non-finite numbers', async () => {
    // Arrange
    const source = sourceFor({
      pathData: 'M0 0 L10 20 Z',
      bounds: { minX: 0, minY: 0, maxX: Infinity, maxY: 20 },
    })

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ error: true, message: expect.stringContaining('finite') })
  })

  it('returns an error when the source read fails', async () => {
    // Arrange
    const source = sourceError('network timeout')

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ error: true, message: 'network timeout' })
  })

  it('returns an error for invalid JSON in the source', async () => {
    // Arrange
    const source: ReplaySource = {
      read: async () => new TextEncoder().encode('{invalid json'),
    }

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ error: true, message: expect.stringContaining('JSON is invalid') })
  })

  it('rejects viewBox with wrong number of values', async () => {
    // Arrange
    const source = sourceFor({ pathData: 'M0 0 L10 20 Z', viewBox: '0 0 10' })

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ error: true, message: expect.stringContaining('four numbers') })
  })

  it('rejects bounds object with unexpected key shapes', async () => {
    // Arrange
    const source = sourceFor({
      pathData: 'M0 0 L10 20 Z',
      bounds: { x: 0, y: 0, w: 10, h: 20 },
    })

    // Act
    const result = await loadCircuitPreview(source, 'visuals/circuit.json')

    // Assert
    expect(result).toEqual({ error: true, message: expect.stringContaining('must be') })
  })
})
