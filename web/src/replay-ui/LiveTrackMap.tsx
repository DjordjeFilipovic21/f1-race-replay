import { memo, useEffect, useMemo, useRef } from 'react'
import type { DriverMetadata, TrackAssets, TrackPoint } from '../replay-data/types'
import type { ReplayController } from '../replay-engine'
import type { ReplaySnapshot } from '../replay-engine/types'

export interface LiveTrackMapProps {
  readonly trackAssets: TrackAssets
  readonly controller: ReplayController
  readonly drivers: readonly DriverMetadata[]
}

export interface TrackMapViewBox {
  readonly minX: number
  readonly minY: number
  readonly width: number
  readonly height: number
}

interface TrackMapGeometry {
  readonly innerBoundary: string
  readonly outerBoundary: string
  readonly centerLine: string
  readonly startFinish: readonly [TrackPoint, TrackPoint]
  readonly viewBox: TrackMapViewBox
  readonly markerRadius: number
  readonly markerLabelSize: number
}

/** Renders static geometry while updating mounted marker nodes from controller notifications. */
export const LiveTrackMap = memo(function LiveTrackMap({ trackAssets, controller, drivers }: LiveTrackMapProps) {
  const geometry = useMemo(() => createTrackMapGeometry(trackAssets), [trackAssets])
  const markerRefs = useRef(new Map<string, SVGGElement>())

  useEffect(() => {
    if (geometry === null) return
    const update = () => updateMarkerPositions(markerRefs.current, controller.getSnapshot().replay, trackAssets.rotationDegrees)
    update()
    return controller.subscribe(update)
  }, [controller, geometry, trackAssets.rotationDegrees])

  return (
    <section className="live-track-map" aria-labelledby="live-track-map-title">
      <header className="live-track-map__header">
        <div>
          <p className="eyebrow">Live circuit position</p>
          <h2 id="live-track-map-title">{trackAssets.trackName} track map</h2>
        </div>
      </header>
      {geometry === null ? (
        <p className="live-track-map__empty" role="status">Track geometry is unavailable for this replay.</p>
      ) : (
        <svg
          className="live-track-map__svg"
          role="group"
          aria-label={`${trackAssets.trackName} live track map`}
          viewBox={formatViewBox(geometry.viewBox)}
          preserveAspectRatio="xMidYMid meet"
        >
          <path className="live-track-map__boundary" d={geometry.outerBoundary} />
          <path className="live-track-map__boundary" d={geometry.innerBoundary} />
          <path className="live-track-map__center-line" d={geometry.centerLine} />
          <line
            className="live-track-map__start-finish"
            x1={geometry.startFinish[0].x}
            y1={geometry.startFinish[0].y}
            x2={geometry.startFinish[1].x}
            y2={geometry.startFinish[1].y}
          />
          {drivers.map((driver) => (
            <g key={driver.id} ref={(element) => setMarkerRef(markerRefs.current, driver.id, element)} className="live-track-map__marker" role="img" aria-label={`${driver.displayName} (${driver.id})`} transform="translate(0 0)" visibility="hidden">
              <circle cx="0" cy="0" r={geometry.markerRadius} fill={isColorHex(driver.colorHex) ? driver.colorHex : 'var(--accent)'} />
              <text x="0" y="0" fontSize={geometry.markerLabelSize} aria-hidden="true">{driver.id}</text>
            </g>
          ))}
        </svg>
      )}
    </section>
  )
})

/** Converts telemetry's Y-up coordinates to SVG's Y-down space, then applies display rotation. */
export function toMapPoint(point: TrackPoint, rotationDegrees: number): TrackPoint | null {
  if (!isFinitePoint(point) || !Number.isFinite(rotationDegrees)) return null
  const radians = rotationDegrees * (Math.PI / 180)
  const cosine = Math.cos(radians)
  const sine = Math.sin(radians)
  const mapY = -point.y
  return { x: point.x * cosine - mapY * sine, y: point.x * sine + mapY * cosine }
}

/** Builds the immutable, rotated SVG geometry once for a track asset. */
export function createTrackMapGeometry(trackAssets: TrackAssets): TrackMapGeometry | null {
  const inner = rotatePoints(trackAssets.innerBoundary, trackAssets.rotationDegrees)
  const outer = rotatePoints(trackAssets.outerBoundary, trackAssets.rotationDegrees)
  const center = rotatePoints(trackAssets.centerLine, trackAssets.rotationDegrees)
  const startFinishInner = toMapPoint(trackAssets.startFinish.inner, trackAssets.rotationDegrees)
  const startFinishOuter = toMapPoint(trackAssets.startFinish.outer, trackAssets.rotationDegrees)
  if (inner === null || outer === null || center === null || startFinishInner === null || startFinishOuter === null) return null

  const startFinish: readonly [TrackPoint, TrackPoint] = [startFinishInner, startFinishOuter]
  const viewBox = createPaddedViewBox([...inner, ...outer, ...center, ...startFinish])
  if (viewBox === null) return null
  const visualScale = Math.max(viewBox.width, viewBox.height)
  return {
    innerBoundary: createPath(inner, true),
    outerBoundary: createPath(outer, true),
    centerLine: createPath(center, true),
    startFinish,
    viewBox,
    markerRadius: visualScale * 0.03,
    markerLabelSize: visualScale * 0.021,
  }
}

export function createPaddedViewBox(points: readonly TrackPoint[]): TrackMapViewBox | null {
  if (points.length === 0 || !points.every(isFinitePoint)) return null
  const xValues = points.map((point) => point.x)
  const yValues = points.map((point) => point.y)
  const minX = Math.min(...xValues)
  const maxX = Math.max(...xValues)
  const minY = Math.min(...yValues)
  const maxY = Math.max(...yValues)
  const largestDimension = Math.max(maxX - minX, maxY - minY, 1)
  const padding = largestDimension * 0.08
  return {
    minX: minX - padding,
    minY: minY - padding,
    width: Math.max(maxX - minX + padding * 2, 1),
    height: Math.max(maxY - minY + padding * 2, 1),
  }
}

function setMarkerRef(markers: Map<string, SVGGElement>, id: string, element: SVGGElement | null): void {
  if (element === null) markers.delete(id)
  else markers.set(id, element)
}

function updateMarkerPositions(markers: ReadonlyMap<string, SVGGElement>, snapshot: ReplaySnapshot | null, rotationDegrees: number): void {
  if (snapshot === null) return
  markers.forEach((element, id) => {
    const sampled = snapshot.drivers[id]
    const point = sampled === undefined || sampled.x === null || sampled.y === null
      ? null
      : toMapPoint({ x: sampled.x, y: sampled.y }, rotationDegrees)
    if (point === null) {
      element.setAttribute('visibility', 'hidden')
      return
    }
    element.setAttribute('transform', `translate(${formatCoordinate(point.x)} ${formatCoordinate(point.y)})`)
    element.setAttribute('visibility', 'visible')
  })
}

function rotatePoints(points: readonly TrackPoint[], rotationDegrees: number): readonly TrackPoint[] | null {
  const rotated = points.map((point) => toMapPoint(point, rotationDegrees))
  return rotated.every((point): point is TrackPoint => point !== null) ? rotated : null
}

function createPath(points: readonly TrackPoint[], closePath: boolean): string {
  return `${points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ')}${closePath ? ' Z' : ''}`
}

function formatViewBox(viewBox: TrackMapViewBox): string {
  return `${viewBox.minX} ${viewBox.minY} ${viewBox.width} ${viewBox.height}`
}

function formatCoordinate(value: number): string {
  return String(Math.round(value * 1_000_000) / 1_000_000)
}

function isFinitePoint(point: TrackPoint): boolean {
  return Number.isFinite(point.x) && Number.isFinite(point.y)
}

function isColorHex(color: string | undefined): color is string {
  return color !== undefined && /^#[0-9a-f]{6}$/i.test(color)
}
