import { memo, useEffect, useMemo, useRef } from 'react'
import type { DriverMetadata, LapSectorSidecar, QualifyingLapStatusSidecar, QualifyingSummary, QualifyingTimeline, SessionMode, TrackAssets, TrackPoint } from '../../../data/replay/types'
import type { ReplayController } from '../../../engine/replay'
import type { ReplaySnapshot } from '../../../engine/replay/types'
import { isQualifyingSessionMode } from '../session-capabilities'
import { selectQualifyingLiveState, type QualifyingLiveState } from '../selectors/qualifying-live-state-selectors'
import { describeTrackStatus } from './track-status'

export interface LiveTrackMapProps {
  readonly trackAssets: TrackAssets
  readonly controller: ReplayController
  readonly drivers: readonly DriverMetadata[]
  readonly selectedDriverId?: string | null
  readonly sessionMode?: SessionMode
  readonly lapSectorSidecar?: LapSectorSidecar | null
  readonly qualifyingSummary?: QualifyingSummary | null
  readonly qualifyingLapStatus?: QualifyingLapStatusSidecar | null
  readonly qualifyingTimeline?: QualifyingTimeline | null
}

export interface TrackMapViewBox {
  readonly minX: number
  readonly minY: number
  readonly width: number
  readonly height: number
}

export interface TrackMapGeometry {
  readonly innerBoundary: string
  readonly outerBoundary: string
  readonly centerLine: string
  readonly centerLineSegments: readonly TrackMapSegmentMetric[]
  readonly centerLineLength: number
  readonly startFinish: readonly [TrackPoint, TrackPoint]
  readonly viewBox: TrackMapViewBox
  readonly markerRadius: number
  readonly markerLabelSize: number
}

interface TrackMapSegmentMetric {
  readonly start: TrackPoint
  readonly end: TrackPoint
  readonly startDistance: number
  readonly length: number
}

export const SAFETY_CAR_AHEAD_DISTANCE_METERS = 100

/** Renders static geometry while updating mounted marker nodes from controller notifications. */
export const LiveTrackMap = memo(function LiveTrackMap({ trackAssets, controller, drivers, selectedDriverId = null, sessionMode = 'race', lapSectorSidecar, qualifyingSummary, qualifyingLapStatus, qualifyingTimeline }: LiveTrackMapProps) {
  const geometry = useMemo(() => createTrackMapGeometry(trackAssets), [trackAssets])
  const markerRefs = useRef(new Map<string, SVGGElement>())
  const boundaryRefs = useRef(new Map<number, SVGPathElement>())
  const safetyCarRef = useRef<SVGGElement | null>(null)
  const statusRef = useRef<HTMLParagraphElement | null>(null)
  const positionNoticeRef = useRef<HTMLParagraphElement | null>(null)
  const initialStatus = describeTrackStatus(controller.getSnapshot().replay?.trackStatusCode ?? null)
  const initialPositionNotice = allActiveDriversUnavailable(controller.getSnapshot().replay, drivers)
  const qualifyingMarkerData = isQualifyingSessionMode(sessionMode)
    ? { qualifyingSummary, lapSectorSidecar, qualifyingLapStatus, qualifyingTimeline }
    : null

  useEffect(() => {
    const update = () => {
      const replay = controller.getSnapshot().replay
      updateTrackMapStatus(statusRef.current, boundaryRefs.current, replay?.trackStatusCode ?? null)
      updateMarkerPositions(markerRefs.current, replay, trackAssets.rotationDegrees, drivers, qualifyingMarkerData)
      updatePositionAvailability(positionNoticeRef.current, replay, drivers)
      const safetyCarPosition = replay?.trackStatusCode === 4
        ? deriveSafetyCarPosition(replay, geometry, trackAssets.circuitLengthMeters, trackAssets.rotationDegrees)
        : null
      updateSafetyCarMarker(safetyCarRef.current, safetyCarPosition)
    }
    update()
    return controller.subscribe(update)
  }, [controller, drivers, geometry, qualifyingMarkerData, trackAssets.circuitLengthMeters, trackAssets.rotationDegrees])

  return (
    <section className="live-track-map" aria-label={`${trackAssets.trackName} track map`}>
      {isQualifyingSessionMode(sessionMode) && (
        <aside className="live-track-map__legend" role="group" aria-label="Qualifying lap state legend">
          <span className="live-track-map__legend-title">Lap state</span>
          <ul className="live-track-map__legend-list">
            <li className="live-track-map__legend-item">
              <span className="live-track-map__legend-swatch live-track-map__legend-swatch--outlap" aria-hidden="true" />
              <span>Outlap</span>
            </li>
            <li className="live-track-map__legend-item">
              <span className="live-track-map__legend-swatch live-track-map__legend-swatch--flying" aria-hidden="true" />
              <span>Flying</span>
            </li>
          </ul>
        </aside>
      )}
      <div className="live-track-map__canvas">
        <p
          ref={statusRef}
          className={`live-track-map__status live-track-map__status--${initialStatus.tone}`}
          role="status"
          aria-live="polite"
          aria-label={`Track status: ${initialStatus.label}`}
          data-track-status={initialStatus.tone}
        >
          {initialStatus.label}
        </p>
        <p ref={positionNoticeRef} className="live-track-map__position-notice" role="status" aria-live="polite" hidden={!initialPositionNotice}>
          Reliable source telemetry is unavailable for this period.
        </p>
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
            <path ref={(element) => setBoundaryRef(boundaryRefs.current, 0, element)} className={`live-track-map__boundary live-track-map__boundary--${initialStatus.tone}`} d={geometry.outerBoundary} data-track-status={initialStatus.tone} />
            <path ref={(element) => setBoundaryRef(boundaryRefs.current, 1, element)} className={`live-track-map__boundary live-track-map__boundary--${initialStatus.tone}`} d={geometry.innerBoundary} data-track-status={initialStatus.tone} />
            <path className="live-track-map__center-line" d={geometry.centerLine} />
            <line
              className="live-track-map__start-finish"
              x1={geometry.startFinish[0].x}
              y1={geometry.startFinish[0].y}
              x2={geometry.startFinish[1].x}
              y2={geometry.startFinish[1].y}
            />
             {orderMarkers(drivers, selectedDriverId).map((driver) => (
               <g key={driver.id} ref={(element) => setMarkerRef(markerRefs.current, driver.id, element)} className={`live-track-map__marker${driver.id === selectedDriverId ? ' live-track-map__marker--selected' : ''}`} color={isColorHex(driver.colorHex) ? driver.colorHex : 'var(--accent)'} role="img" aria-label={`${driver.displayName} (${driver.id})`} transform="translate(0 0)" visibility="hidden">
                <circle className="live-track-map__driver-dot" cx="0" cy="0" r={geometry.markerRadius} fill="currentColor" />
                <text x="0" y="0" fontSize={geometry.markerLabelSize} aria-hidden="true">{driver.id}</text>
              </g>
            ))}
            <g
              ref={safetyCarRef}
              className="live-track-map__safety-car-marker"
              role="img"
              aria-label="Safety Car (SC)"
              data-track-status="hidden"
              transform="translate(0 0)"
              visibility="hidden"
            >
              <circle cx="0" cy="0" r={geometry.markerRadius} />
              <text x="0" y="0" fontSize={geometry.markerLabelSize}>SC</text>
            </g>
          </svg>
        )}
      </div>
    </section>
  )
})

function orderMarkers(drivers: readonly DriverMetadata[], selectedDriverId: string | null): readonly DriverMetadata[] {
  return selectedDriverId === null ? drivers : [...drivers.filter((driver) => driver.id !== selectedDriverId), ...drivers.filter((driver) => driver.id === selectedDriverId)]
}

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
  const centerLineMetrics = createCenterLineMetrics(center)
  if (viewBox === null || centerLineMetrics === null) return null
  const visualScale = Math.max(viewBox.width, viewBox.height)
  return {
    innerBoundary: createPath(inner, true),
    outerBoundary: createPath(outer, true),
    centerLine: createPath(center, true),
    centerLineSegments: centerLineMetrics.segments,
    centerLineLength: centerLineMetrics.totalLength,
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

function setBoundaryRef(boundaries: Map<number, SVGPathElement>, index: number, element: SVGPathElement | null): void {
  if (element === null) boundaries.delete(index)
  else boundaries.set(index, element)
}

function updateTrackMapStatus(statusElement: HTMLParagraphElement | null, boundaries: ReadonlyMap<number, SVGPathElement>, trackStatusCode: number | null): void {
  const status = describeTrackStatus(trackStatusCode)
  if (statusElement !== null) {
    const statusClass = `live-track-map__status live-track-map__status--${status.tone}`
    if (statusElement.textContent !== status.label) statusElement.textContent = status.label
    if (statusElement.className !== statusClass) statusElement.className = statusClass
    setAttributeIfChanged(statusElement, 'aria-label', `Track status: ${status.label}`)
    setAttributeIfChanged(statusElement, 'data-track-status', status.tone)
  }
  boundaries.forEach((boundary) => {
    const boundaryClass = `live-track-map__boundary live-track-map__boundary--${status.tone}`
    setAttributeIfChanged(boundary, 'class', boundaryClass)
    setAttributeIfChanged(boundary, 'data-track-status', status.tone)
  })
}

function setAttributeIfChanged(element: Element, name: string, value: string): void {
  if (element.getAttribute(name) !== value) element.setAttribute(name, value)
}

interface QualifyingMarkerData {
  readonly qualifyingSummary?: QualifyingSummary | null
  readonly lapSectorSidecar?: LapSectorSidecar | null
  readonly qualifyingLapStatus?: QualifyingLapStatusSidecar | null
  readonly qualifyingTimeline?: QualifyingTimeline | null
}

function updateMarkerPositions(
  markers: ReadonlyMap<string, SVGGElement>,
  snapshot: ReplaySnapshot | null,
  rotationDegrees: number,
  drivers: readonly DriverMetadata[],
  qualifyingMarkerData: QualifyingMarkerData | null,
): void {
  if (snapshot === null) return
  markers.forEach((element, id) => {
    const sampled = snapshot.drivers[id]
    const driver = drivers.find((candidate) => candidate.id === id)
    if (driver !== undefined) updateQualifyingMarkerState(element, driver, snapshot, qualifyingMarkerData)
    const incidented = qualifyingMarkerData !== null && hasCausalQualifyingIncident(qualifyingMarkerData.qualifyingTimeline, id, snapshot.sessionTimeMs)
    const point = sampled === undefined || isTerminalStatus(sampled.status) || sampled.x === null || sampled.y === null
      ? null
      : toMapPoint({ x: sampled.x, y: sampled.y }, rotationDegrees)
    if (point === null || incidented) {
      element.setAttribute('visibility', 'hidden')
      return
    }
    element.setAttribute('transform', `translate(${formatCoordinate(point.x)} ${formatCoordinate(point.y)})`)
    element.setAttribute('visibility', 'visible')
  })
}

function hasCausalQualifyingIncident(qualifyingTimeline: QualifyingTimeline | null | undefined, driverId: string, replayTimeMs: number): boolean {
  return qualifyingTimeline?.incidentMarkers.some((marker) => marker.driverId === driverId && marker.timeMs <= replayTimeMs) ?? false
}

function updatePositionAvailability(
  notice: HTMLParagraphElement | null,
  snapshot: ReplaySnapshot | null,
  drivers: readonly DriverMetadata[],
): void {
  if (notice === null) return
  notice.hidden = !allActiveDriversUnavailable(snapshot, drivers)
}

function allActiveDriversUnavailable(snapshot: ReplaySnapshot | null, drivers: readonly DriverMetadata[]): boolean {
  if (snapshot === null) return false
  const active = drivers
    .map(({ id }) => snapshot.drivers[id] ?? null)
    .filter((driver) => driver === null || (!isTerminalStatus(driver.status) && driver.isInPitLane !== true))
  return active.length > 0 && active.every((driver) => driver === null || driver.x === null || driver.y === null || !Number.isFinite(driver.x) || !Number.isFinite(driver.y))
}

function updateQualifyingMarkerState(
  element: SVGGElement,
  driver: DriverMetadata,
  snapshot: ReplaySnapshot,
  qualifyingMarkerData: QualifyingMarkerData | null,
): void {
  const baseLabel = `${driver.displayName} (${driver.id})`
  if (qualifyingMarkerData === null) {
    element.removeAttribute('data-qualifying-lap-state')
    setAttributeIfChanged(element, 'aria-label', baseLabel)
    return
  }

  const state = selectQualifyingLiveState(
    snapshot,
    driver.id,
    qualifyingMarkerData.qualifyingSummary,
    qualifyingMarkerData.lapSectorSidecar,
    qualifyingMarkerData.qualifyingLapStatus,
  )
  setAttributeIfChanged(element, 'data-qualifying-lap-state', state.lapPhase)
  setAttributeIfChanged(element, 'aria-label', qualifyingMarkerAriaLabel(baseLabel, state))
}

function qualifyingMarkerAriaLabel(baseLabel: string, state: QualifyingLiveState): string {
  if (state.lapPhase === 'outlap') return `${baseLabel}, qualifying lap state: Outlap`
  if (state.lapPhase === 'flying') return `${baseLabel}, qualifying lap state: Flying`
  return baseLabel
}

function updateSafetyCarMarker(marker: SVGGElement | null, point: TrackPoint | null): void {
  if (marker === null) return
  if (point === null) {
    setAttributeIfChanged(marker, 'visibility', 'hidden')
    setAttributeIfChanged(marker, 'data-track-status', 'hidden')
    return
  }
  setAttributeIfChanged(marker, 'transform', `translate(${formatCoordinate(point.x)} ${formatCoordinate(point.y)})`)
  setAttributeIfChanged(marker, 'visibility', 'visible')
  setAttributeIfChanged(marker, 'data-track-status', 'safety-car')
}

/** Projects the leader's interpolated telemetry onto the track and places SC ahead. */
export function deriveSafetyCarPosition(snapshot: ReplaySnapshot | null, geometry: TrackMapGeometry | null, circuitLengthMeters: number, rotationDegrees: number): TrackPoint | null {
  if (snapshot === null || geometry === null || !isValidCircuitLength(circuitLengthMeters)) return null
  const leader = findLeader(snapshot)
  const leaderPoint = leader === null || leader.x === null || leader.y === null
    ? null
    : toMapPoint({ x: leader.x, y: leader.y }, rotationDegrees)
  if (leaderPoint === null) return null
  const leaderPathDistance = projectOntoCenterLine(leaderPoint, geometry.centerLineSegments)
  if (leaderPathDistance === null) return null
  const aheadPathDistance = Math.min(SAFETY_CAR_AHEAD_DISTANCE_METERS, circuitLengthMeters * 0.1) / circuitLengthMeters * geometry.centerLineLength
  const target = normalizeDistance(leaderPathDistance + aheadPathDistance, geometry.centerLineLength)
  const segment = geometry.centerLineSegments.find(({ startDistance, length }) => target <= startDistance + length)
    ?? geometry.centerLineSegments.at(-1)
  if (segment === undefined || segment.length <= 0) return null
  const ratio = Math.min(Math.max((target - segment.startDistance) / segment.length, 0), 1)
  return {
    x: segment.start.x + (segment.end.x - segment.start.x) * ratio,
    y: segment.start.y + (segment.end.y - segment.start.y) * ratio,
  }
}

function projectOntoCenterLine(point: TrackPoint, segments: readonly TrackMapSegmentMetric[]): number | null {
  let closestDistanceSquared = Number.POSITIVE_INFINITY
  let closestPathDistance: number | null = null
  segments.forEach((segment) => {
    const deltaX = segment.end.x - segment.start.x
    const deltaY = segment.end.y - segment.start.y
    const ratio = Math.min(Math.max(((point.x - segment.start.x) * deltaX + (point.y - segment.start.y) * deltaY) / (segment.length * segment.length), 0), 1)
    const projectedX = segment.start.x + deltaX * ratio
    const projectedY = segment.start.y + deltaY * ratio
    const distanceSquared = (point.x - projectedX) ** 2 + (point.y - projectedY) ** 2
    if (distanceSquared >= closestDistanceSquared) return
    closestDistanceSquared = distanceSquared
    closestPathDistance = segment.startDistance + segment.length * ratio
  })
  return closestPathDistance
}

function createCenterLineMetrics(points: readonly TrackPoint[]): Readonly<{ readonly segments: readonly TrackMapSegmentMetric[]; readonly totalLength: number }> | null {
  if (points.length < 2) return null
  const segments: TrackMapSegmentMetric[] = []
  let totalLength = 0
  points.forEach((start, index) => {
    const end = points[(index + 1) % points.length]
    const length = Math.hypot(end.x - start.x, end.y - start.y)
    if (length <= 0) return
    segments.push(Object.freeze({ start, end, startDistance: totalLength, length }))
    totalLength += length
  })
  return totalLength > 0 ? Object.freeze({ segments: Object.freeze(segments), totalLength }) : null
}

function findLeader(snapshot: ReplaySnapshot): ReplaySnapshot['drivers'][string] | null {
  const orderedLeaderId = snapshot.leaderboardOrder?.[0]
  const candidates = [orderedLeaderId, ...Object.keys(snapshot.drivers).filter((id) => snapshot.drivers[id]?.position === 1)]
  for (const id of new Set(candidates)) {
    if (id === undefined) continue
    const driver = snapshot.drivers[id]
    if (driver === undefined || isTerminalStatus(driver.status) || driver.isInPitLane === true || driver.x === null || driver.y === null || !Number.isFinite(driver.x) || !Number.isFinite(driver.y)) continue
    return driver
  }
  return null
}

function normalizeDistance(distance: number, circuitLengthMeters: number): number {
  const normalized = distance % circuitLengthMeters
  return normalized < 0 ? normalized + circuitLengthMeters : normalized
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

function isTerminalStatus(status: string | null): boolean {
  return typeof status === 'string' && status.trim().toUpperCase() === 'OUT'
}

function isColorHex(color: string | undefined): color is string {
  return color !== undefined && /^#[0-9a-f]{6}$/i.test(color)
}

function isValidCircuitLength(value: number): boolean {
  return Number.isFinite(value) && value > 0
}
