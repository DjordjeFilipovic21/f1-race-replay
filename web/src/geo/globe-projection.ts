import { geoGraticule, geoOrthographic, geoPath } from 'd3-geo'
import type { GeoProjection } from 'd3-geo'
import type { FeatureCollection, GeometryObject as GeoJsonGeometryObject } from 'geojson'
import { feature } from 'topojson-client'
import type { GeometryCollection, Topology } from 'topojson-specification'
import landData from 'world-atlas/land-110m.json'

const DEFAULT_GLOBE_SIZE = 600
const DEFAULT_GRATICULE_STEP = 15
const DEFAULT_SCALE_RATIO = 0.45

export interface OrthographicProjectionOptions {
  readonly width?: number
  readonly height?: number
  readonly scale?: number
  readonly translate?: readonly [number, number]
}

export type GlobeRotation = readonly [number, number, number]

const landTopology = landData as unknown as Topology
const worldLandObject = landTopology.objects.land as GeometryCollection

/** Lightweight bundled land geometry for animated globe rendering. */
export const worldLand: FeatureCollection<GeoJsonGeometryObject> = feature(landTopology, worldLandObject)

/**
 * Returns the d3 rotation that puts a race coordinate at the centre of a globe.
 * d3 rotations use the inverse of the location being viewed, hence the signs.
 */
export function computeRotation(
  latitude: number,
  longitude: number,
  fromRotation: GlobeRotation = [0, 0, 0],
): GlobeRotation {
  assertCoordinate(latitude, longitude)
  assertFiniteRotation(fromRotation)
  const targetLongitude = -normalizeLongitude(longitude)
  return [shortestLongitude(targetLongitude, fromRotation[0]), -latitude, 0]
}

/** Creates a deterministic orthographic projection centred on a race location. */
export function createOrthographicProjection(
  latitude: number,
  longitude: number,
  optionsOrWidth: OrthographicProjectionOptions | number = {},
  legacyHeight?: number,
): GeoProjection {
  assertCoordinate(latitude, longitude)
  const options = typeof optionsOrWidth === 'number'
    ? { width: optionsOrWidth, height: legacyHeight ?? optionsOrWidth }
    : optionsOrWidth
  const width = options.width ?? DEFAULT_GLOBE_SIZE
  const resolvedHeight = options.height ?? DEFAULT_GLOBE_SIZE
  assertPositiveFinite(width, 'width')
  assertPositiveFinite(resolvedHeight, 'height')

  const translate = options.translate ?? [width / 2, resolvedHeight / 2] as const
  assertFinitePair(translate, 'translate')
  const scale = options.scale ?? Math.min(width, resolvedHeight) * DEFAULT_SCALE_RATIO
  assertPositiveFinite(scale, 'scale')

  const [rotationLongitude, rotationLatitude, rotationRoll] = computeRotation(latitude, longitude)
  return geoOrthographic()
    .scale(scale)
    .translate([translate[0], translate[1]])
    .rotate([rotationLongitude, rotationLatitude, rotationRoll])
    .clipAngle(90)
}

/** Returns SVG path data for a regular latitude/longitude graticule. */
export function buildGraticulePath(projection: GeoProjection, step = DEFAULT_GRATICULE_STEP): string {
  if (!isProjection(projection)) throw new TypeError('projection must be a d3-geo projection')
  assertPositiveFinite(step, 'step')
  const graticule = geoGraticule().step([step, step])()
  return geoPath(projection)(graticule) ?? ''
}

function assertCoordinate(latitude: number, longitude: number): void {
  if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90) {
    throw new RangeError('latitude must be a finite number between -90 and 90')
  }
  if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180) {
    throw new RangeError('longitude must be a finite number between -180 and 180')
  }
}

function assertPositiveFinite(value: number, name: string): void {
  if (!Number.isFinite(value) || value <= 0) throw new RangeError(`${name} must be a positive finite number`)
}

function assertFinitePair(pair: readonly [number, number], name: string): void {
  if (!Number.isFinite(pair[0]) || !Number.isFinite(pair[1])) throw new RangeError(`${name} must contain finite numbers`)
}

function assertFiniteRotation(rotation: GlobeRotation): void {
  if (!rotation.every(Number.isFinite)) throw new RangeError('fromRotation must contain finite numbers')
}

function normalizeLongitude(longitude: number): number {
  const normalized = ((longitude + 180) % 360 + 360) % 360 - 180
  return normalized === -180 && longitude > 0 ? 180 : normalized
}

function shortestLongitude(target: number, from: number): number {
  const delta = ((target - from + 180) % 360 + 360) % 360 - 180
  return from + delta
}

function isProjection(value: GeoProjection): value is GeoProjection {
  return typeof value === 'function'
    && typeof value.rotate === 'function'
    && typeof value.scale === 'function'
    && typeof value.translate === 'function'
}
