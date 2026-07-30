import { assertSafeRelativePath, readJson } from '../data/replay/source'
import type { ReplaySource } from '../data/replay/types'

const NUMBER = /^[+-]?(?:(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)/
const COMMANDS = new Set('MmZzLlHhVvCcSsQqTtAa'.split(''))
const PARAMETER_COUNTS: Readonly<Record<string, number>> = {
  M: 2, L: 2, H: 1, V: 1, C: 6, S: 4, Q: 4, T: 2, A: 7, Z: 0,
}

export interface CircuitPreviewBounds {
  readonly minX: number
  readonly minY: number
  readonly width: number
  readonly height: number
}

export interface CircuitPreviewExtent {
  readonly minX: number
  readonly minY: number
  readonly maxX: number
  readonly maxY: number
}

export type CircuitPreviewBox = string | readonly [number, number, number, number] | CircuitPreviewBounds | CircuitPreviewExtent

/** Exact JSON asset shape; unknown fields are rejected at the runtime boundary. */
export interface CircuitPreviewAsset {
  readonly pathData: string
  readonly viewBox?: CircuitPreviewBox
  readonly bounds?: CircuitPreviewBox
}

export interface CircuitPreviewSuccess {
  readonly pathData: string
  readonly viewBox: string
}

export interface CircuitPreviewError {
  readonly error: true
  readonly message: string
}

export type CircuitPreviewResult = CircuitPreviewSuccess | CircuitPreviewError

interface PathCommand {
  readonly command: string
  readonly values: readonly number[]
}

interface PathPoint {
  readonly x: number
  readonly y: number
}

interface MutableBounds {
  minX: number
  minY: number
  maxX: number
  maxY: number
}

/** Loads the deliberately small JSON circuit-preview contract through ReplaySource. */
export async function loadCircuitPreview(source: ReplaySource, circuitPreviewPath: string): Promise<CircuitPreviewResult> {
  try {
    const safePath = assertSafeRelativePath(circuitPreviewPath)
    const asset = parseCircuitPreviewAsset(await readJson(source, safePath))
    const pathData = normalizeSvgPath(asset.pathData)
    const viewBox = asset.viewBox === undefined
      ? calculatePathViewBox(pathData)
      : formatViewBox(asset.viewBox)
    return Object.freeze({ pathData, viewBox })
  } catch (error: unknown) {
    return Object.freeze({ error: true as const, message: describeError(error) })
  }
}

/** Tokenizes and validates only the SVG path grammar; XML and presentation payloads are not accepted. */
export function normalizeSvgPath(pathData: string): string {
  const commands = parsePath(pathData)
  return commands.flatMap(({ command, values }) => [command, ...values.map(formatNumber)]).join(' ')
}

/** Computes a finite positive bounds rectangle from validated SVG path geometry. */
export function calculatePathBounds(pathData: string): CircuitPreviewBounds {
  const commands = parsePath(pathData)
  rejectArcBounds(commands)
  const bounds = tracePath(commands)
  return toPositiveBounds(bounds)
}

/** Returns the SVG viewBox string for path geometry, without requiring a DOM or SVG implementation. */
export function calculatePathViewBox(pathData: string): string {
  return formatViewBox(calculatePathBounds(pathData))
}

/** Compatibility alias for callers that refer to path data rather than SVG paths. */
export const normalizePathData = normalizeSvgPath

/** Compatibility alias for callers that refer to a path's viewBox rather than its bounds. */
export const calculateViewBox = calculatePathViewBox

function parseCircuitPreviewAsset(value: unknown): { readonly pathData: string; readonly viewBox?: CircuitPreviewBounds } {
  const item = asObject(value, 'circuit preview')
  assertExactKeys(item, ['pathData', 'viewBox', 'bounds'])
  if (typeof item.pathData !== 'string' || item.pathData.trim() === '') throw new Error('circuit preview.pathData must be non-blank')
  if (item.viewBox !== undefined && item.bounds !== undefined) throw new Error('circuit preview must provide viewBox or bounds, not both')
  const embedded = item.viewBox === undefined ? item.bounds : item.viewBox
  return Object.freeze({
    pathData: item.pathData,
    ...(embedded === undefined ? {} : { viewBox: parseBounds(embedded, item.viewBox === undefined ? 'circuit preview.bounds' : 'circuit preview.viewBox') }),
  })
}

function parseBounds(value: unknown, label: string): CircuitPreviewBounds {
  const values = typeof value === 'string' ? parseViewBoxString(value, label) : value
  if (Array.isArray(values)) {
    if (values.length !== 4 || !values.every(isFiniteNumber)) throw new Error(`${label} must contain four finite numbers`)
    return createBounds(values[0], values[1], values[2], values[3], label)
  }

  const item = asObject(values, label)
  if (hasExactKeys(item, ['minX', 'minY', 'width', 'height'])) {
    return createBounds(item.minX, item.minY, item.width, item.height, label)
  }
  if (hasExactKeys(item, ['minX', 'minY', 'maxX', 'maxY'])) {
    if (!isFiniteNumber(item.minX) || !isFiniteNumber(item.minY)
      || !isFiniteNumber(item.maxX) || !isFiniteNumber(item.maxY)) {
      throw new Error(`${label} must contain finite numbers`)
    }
    return createBounds(item.minX, item.minY, item.maxX - item.minX, item.maxY - item.minY, label)
  }
  throw new Error(`${label} must be {minX, minY, width, height} or {minX, minY, maxX, maxY}`)
}

function parseViewBoxString(value: string, label: string): readonly number[] {
  const tokens = tokenizeNumbers(value)
  if (tokens.length !== 4) throw new Error(`${label} must contain four numbers`)
  return tokens
}

function createBounds(minX: unknown, minY: unknown, width: unknown, height: unknown, label: string): CircuitPreviewBounds {
  if (![minX, minY, width, height].every(isFiniteNumber) || (width as number) <= 0 || (height as number) <= 0) {
    throw new Error(`${label} must contain finite coordinates and positive width and height`)
  }
  return Object.freeze({ minX: minX as number, minY: minY as number, width: width as number, height: height as number })
}

function parsePath(pathData: string): readonly PathCommand[] {
  if (typeof pathData !== 'string' || pathData.trim() === '') throw new Error('SVG path data must be non-blank')
  const tokens = tokenizePath(pathData)
  const commands: PathCommand[] = []
  let index = 0
  let hasGeometry = false

  while (index < tokens.length) {
    const token = tokens[index]
    if (token.kind !== 'command') throw new Error('SVG path data must start each segment with a path command')
    if (commands.length === 0 && token.value.toUpperCase() !== 'M') throw new Error('SVG path data must begin with M or m')
    index += 1
    const count = PARAMETER_COUNTS[token.value.toUpperCase()]
    if (count === undefined) throw new Error(`SVG path command ${token.value} is not allowed`)
    if (count === 0) {
      commands.push({ command: token.value, values: [] })
      continue
    }

    let group = 0
    while (index < tokens.length && tokens[index].kind === 'number') {
      if (index + count > tokens.length || tokens.slice(index, index + count).some((entry) => entry.kind !== 'number')) {
        throw new Error(`SVG path command ${token.value} has incomplete parameters`)
      }
      const values = tokens.slice(index, index + count).map((entry) => entry.kind === 'number' ? entry.value : 0)
      validateArcFlags(token.value, values)
      const command = group > 0 && token.value.toUpperCase() === 'M'
        ? token.value === token.value.toUpperCase() ? 'L' : 'l'
        : token.value
      commands.push({ command, values })
      hasGeometry = true
      group += 1
      index += count
    }
    if (group === 0) throw new Error(`SVG path command ${token.value} has no parameters`)
  }

  if (!hasGeometry) throw new Error('SVG path data must contain drawable geometry')
  return commands
}

function tokenizePath(pathData: string): readonly ({ readonly kind: 'command'; readonly value: string } | { readonly kind: 'number'; readonly value: number })[] {
  const tokens: ({ readonly kind: 'command'; readonly value: string } | { readonly kind: 'number'; readonly value: number })[] = []
  let index = 0
  while (index < pathData.length) {
    if (isPathSeparator(pathData[index] ?? '')) {
      index += 1
      continue
    }
    const character = pathData[index] ?? ''
    if (COMMANDS.has(character)) {
      tokens.push({ kind: 'command', value: character })
      index += 1
      continue
    }
    const match = pathData.slice(index).match(NUMBER)
    if (match === null) throw new Error('SVG path data contains an unsupported token')
    const value = Number(match[0])
    if (!Number.isFinite(value)) throw new Error('SVG path numbers must be finite')
    tokens.push({ kind: 'number', value })
    index += match[0].length
  }
  return tokens
}

function tokenizeNumbers(value: string): readonly number[] {
  const tokens: number[] = []
  let index = 0
  while (index < value.length) {
    if (isPathSeparator(value[index] ?? '')) {
      index += 1
      continue
    }
    const match = value.slice(index).match(NUMBER)
    if (match === null) throw new Error('viewBox contains an unsupported token')
    const parsed = Number(match[0])
    if (!Number.isFinite(parsed)) throw new Error('viewBox numbers must be finite')
    tokens.push(parsed)
    index += match[0].length
  }
  return tokens
}

function tracePath(commands: readonly PathCommand[]): MutableBounds {
  const bounds: MutableBounds = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity }
  let current: PathPoint = { x: 0, y: 0 }
  let subpathStart = current
  let previousCubicControl: PathPoint | null = null
  let previousQuadraticControl: PathPoint | null = null

  commands.forEach(({ command, values }) => {
    const relative = command === command.toLowerCase()
    const type = command.toUpperCase()
    const point = (x: number, y: number): PathPoint => ({ x: relative ? current.x + x : x, y: relative ? current.y + y : y })
    switch (type) {
      case 'M': current = point(values[0] ?? 0, values[1] ?? 0); subpathStart = current; addPoint(bounds, current); break
      case 'L': { const end = point(values[0] ?? 0, values[1] ?? 0); addLine(bounds, current, end); current = end; break }
      case 'H': { const end = { x: relative ? current.x + (values[0] ?? 0) : values[0] ?? 0, y: current.y }; addLine(bounds, current, end); current = end; break }
      case 'V': { const end = { x: current.x, y: relative ? current.y + (values[0] ?? 0) : values[0] ?? 0 }; addLine(bounds, current, end); current = end; break }
      case 'C': {
        const first = point(values[0] ?? 0, values[1] ?? 0)
        const second = point(values[2] ?? 0, values[3] ?? 0)
        const end = point(values[4] ?? 0, values[5] ?? 0)
        addCubic(bounds, current, first, second, end); current = end; previousCubicControl = second; break
      }
      case 'S': {
        const first = previousCubicControl === null ? current : reflect(previousCubicControl, current)
        const second = point(values[0] ?? 0, values[1] ?? 0)
        const end = point(values[2] ?? 0, values[3] ?? 0)
        addCubic(bounds, current, first, second, end); current = end; previousCubicControl = second; break
      }
      case 'Q': {
        const control = point(values[0] ?? 0, values[1] ?? 0)
        const end = point(values[2] ?? 0, values[3] ?? 0)
        addQuadratic(bounds, current, control, end); current = end; previousQuadraticControl = control; break
      }
      case 'T': {
        const control = previousQuadraticControl === null ? current : reflect(previousQuadraticControl, current)
        const end = point(values[0] ?? 0, values[1] ?? 0)
        addQuadratic(bounds, current, control, end); current = end; previousQuadraticControl = control; break
      }
      case 'A': {
        const end = point(values[5] ?? 0, values[6] ?? 0)
        addPoint(bounds, current); addPoint(bounds, end); current = end; break
      }
      case 'Z': current = subpathStart; addPoint(bounds, current); break
      default: throw new Error(`SVG path command ${command} is not allowed`)
    }
    if (!['C', 'S'].includes(type)) previousCubicControl = null
    if (!['Q', 'T'].includes(type)) previousQuadraticControl = null
  })
  return bounds
}

function rejectArcBounds(commands: readonly PathCommand[]): void {
  if (commands.some(({ command }) => command.toUpperCase() === 'A')) {
    throw new Error('SVG arc paths require an explicit viewBox or bounds; automatic arc bounds are not supported')
  }
}

function addLine(bounds: MutableBounds, start: PathPoint, end: PathPoint): void {
  addPoint(bounds, start)
  addPoint(bounds, end)
}

function addQuadratic(bounds: MutableBounds, start: PathPoint, control: PathPoint, end: PathPoint): void {
  addPoint(bounds, start); addPoint(bounds, end)
  addQuadraticExtremum(bounds, start.x, control.x, end.x, 'x')
  addQuadraticExtremum(bounds, start.y, control.y, end.y, 'y')
}

function addCubic(bounds: MutableBounds, start: PathPoint, first: PathPoint, second: PathPoint, end: PathPoint): void {
  addPoint(bounds, start); addPoint(bounds, end)
  addCubicExtrema(bounds, start.x, first.x, second.x, end.x, 'x')
  addCubicExtrema(bounds, start.y, first.y, second.y, end.y, 'y')
}

function addQuadraticExtremum(bounds: MutableBounds, start: number, control: number, end: number, axis: 'x' | 'y'): void {
  const denominator = start - 2 * control + end
  if (denominator === 0) return
  const t = (start - control) / denominator
  if (t > 0 && t < 1) addScalar(bounds, quadraticAt(start, control, end, t), axis)
}

function addCubicExtrema(bounds: MutableBounds, start: number, first: number, second: number, end: number, axis: 'x' | 'y'): void {
  const a = -start + 3 * first - 3 * second + end
  const b = 2 * (start - 2 * first + second)
  const c = first - start
  const discriminant = b * b - 4 * a * c
  if (Math.abs(a) < Number.EPSILON) {
    if (Math.abs(b) >= Number.EPSILON) addCubicRoot(bounds, start, first, second, end, -c / b, axis)
    return
  }
  if (discriminant < 0) return
  const root = Math.sqrt(discriminant)
  addCubicRoot(bounds, start, first, second, end, (-b + root) / (2 * a), axis)
  addCubicRoot(bounds, start, first, second, end, (-b - root) / (2 * a), axis)
}

function addCubicRoot(bounds: MutableBounds, start: number, first: number, second: number, end: number, t: number, axis: 'x' | 'y'): void {
  if (t > 0 && t < 1) addScalar(bounds, cubicAt(start, first, second, end, t), axis)
}

function quadraticAt(start: number, control: number, end: number, t: number): number {
  const inverse = 1 - t
  return inverse * inverse * start + 2 * inverse * t * control + t * t * end
}

function cubicAt(start: number, first: number, second: number, end: number, t: number): number {
  const inverse = 1 - t
  return inverse ** 3 * start + 3 * inverse ** 2 * t * first + 3 * inverse * t ** 2 * second + t ** 3 * end
}

function reflect(point: PathPoint, around: PathPoint): PathPoint {
  return { x: 2 * around.x - point.x, y: 2 * around.y - point.y }
}

function addScalar(bounds: MutableBounds, value: number, axis: 'x' | 'y'): void {
  if (axis === 'x') {
    bounds.minX = Math.min(bounds.minX, value)
    bounds.maxX = Math.max(bounds.maxX, value)
  } else {
    bounds.minY = Math.min(bounds.minY, value)
    bounds.maxY = Math.max(bounds.maxY, value)
  }
}

function addPoint(bounds: MutableBounds, point: PathPoint): void {
  addScalar(bounds, point.x, 'x')
  addScalar(bounds, point.y, 'y')
}

function toPositiveBounds(bounds: MutableBounds): CircuitPreviewBounds {
  if (![bounds.minX, bounds.minY, bounds.maxX, bounds.maxY].every(Number.isFinite)) throw new Error('SVG path has no finite geometry')
  const width = bounds.maxX - bounds.minX
  const height = bounds.maxY - bounds.minY
  if (!Number.isFinite(width) || !Number.isFinite(height)) throw new Error('SVG path bounds must be finite')
  const horizontalPadding = width > 0 ? 0 : 0.5
  const verticalPadding = height > 0 ? 0 : 0.5
  return Object.freeze({
    minX: bounds.minX - horizontalPadding,
    minY: bounds.minY - verticalPadding,
    width: width > 0 ? width : 1,
    height: height > 0 ? height : 1,
  })
}

function formatViewBox(bounds: CircuitPreviewBounds): string {
  return [bounds.minX, bounds.minY, bounds.width, bounds.height].map(formatNumber).join(' ')
}

function formatNumber(value: number): string {
  if (!Number.isFinite(value)) throw new Error('geometry numbers must be finite')
  return String(Object.is(value, -0) ? 0 : value)
}

function validateArcFlags(command: string, values: readonly number[]): void {
  if (command.toUpperCase() !== 'A') return
  if ((values[0] ?? 0) < 0 || (values[1] ?? 0) < 0) throw new Error('SVG arc radii must not be negative')
  if (![values[3], values[4]].every((value) => value === 0 || value === 1)) throw new Error('SVG arc flags must be 0 or 1')
}

function isPathSeparator(value: string): boolean {
  return /[\s,]/.test(value)
}

function asObject(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be an object`)
  return value as Record<string, unknown>
}

function assertExactKeys(value: Record<string, unknown>, allowed: readonly string[]): void {
  const allowedKeys = new Set(allowed)
  if (Object.keys(value).some((key) => !allowedKeys.has(key))) throw new Error('circuit preview contains an unsupported field')
  if (!('pathData' in value)) throw new Error('circuit preview.pathData is required')
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  return Object.keys(value).length === expected.length && expected.every((key) => key in value)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function describeError(error: unknown): string {
  if (error instanceof Error && error.message) return error.message
  if (typeof error === 'string' && error) return error
  return 'Circuit preview could not be loaded.'
}
