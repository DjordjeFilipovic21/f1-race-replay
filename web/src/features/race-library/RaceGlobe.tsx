import { memo, useEffect, useRef, useState } from 'react'
import { geoGraticule, geoInterpolate, geoPath } from 'd3-geo'
import type { GeoProjection } from 'd3-geo'
import type { CatalogV2Race } from '../../data/catalog/types'
import {
  computeRotation,
  createOrthographicProjection,
  worldLand,
  type GlobeRotation,
} from '../../geo/globe-projection'

const DEFAULT_GLOBE_SIZE = 400
const CANVAS_PIXEL_RATIO = 2
const MARKER_EXIT_DURATION_MS = 300
const TRAVEL_DURATION_MS = 900
const DEFAULT_ROTATION: GlobeRotation = [0, 0, 0]
const GRATICULE_STEP_DEGREES = 15

const HAS_WORLD_GEOMETRY = worldLand.features.length > 0

interface GlobeCoordinate {
  readonly latitude: number
  readonly longitude: number
}

export interface RaceGlobeProps {
  readonly race: CatalogV2Race | null
}

/**
 * Accessible orthographic globe that rotates toward the selected race location.
 *
 * - Rotation is animated with requestAnimationFrame along a spherical path
 *   between race coordinates.
 * - No continuous autoplay; the globe only rotates on selection changes.
 * - Honours prefers-reduced-motion by snapping to the target rotation.
 * - Renders a static placeholder when visual metadata is absent or invalid.
 * - Does not add a keyboard tab stop; semantic SVG labelling is used instead.
 */
export const RaceGlobe = memo(function RaceGlobe({ race }: RaceGlobeProps) {
  const visual = race?.visual ?? null
  const eventName = race?.event_name ?? 'selected race'
  const hasValidVisual = visual !== null && isValidCoordinate(visual.latitude, visual.longitude)

  const initialRotation = hasValidVisual
    ? computeRotation(visual.latitude, visual.longitude)
    : DEFAULT_ROTATION

  const [, requestGeometryRender] = useState(0)
  const [journeySource, setJourneySource] = useState<GlobeCoordinate | null>(null)
  const [isAnimating, setIsAnimating] = useState(false)
  const currentRotationRef = useRef<GlobeRotation>(initialRotation)
  const currentCoordinateRef = useRef<GlobeCoordinate>(
    hasValidVisual && visual !== null
      ? { latitude: visual.latitude, longitude: visual.longitude }
      : { latitude: 0, longitude: 0 },
  )
  const previousVisualRef = useRef(visual)
  const animationFrameRef = useRef<number | null>(null)
  const reducedMotionRef = useRef(false)
  const mapCanvasRef = useRef<HTMLCanvasElement>(null)
  const journeyRef = useRef<SVGPathElement>(null)

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return
    }

    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    reducedMotionRef.current = mediaQuery.matches

    const handleChange = (event: MediaQueryListEvent): void => {
      reducedMotionRef.current = event.matches
    }

    mediaQuery.addEventListener('change', handleChange)
    return () => {
      mediaQuery.removeEventListener('change', handleChange)
    }
  }, [])

  useEffect(() => {
    const previousVisual = previousVisualRef.current
    previousVisualRef.current = visual

    if (animationFrameRef.current !== null) {
      cancelAnimationFrame(animationFrameRef.current)
      animationFrameRef.current = null
    }

    if (!HAS_WORLD_GEOMETRY) {
      return
    }

    if (!hasValidVisual) {
      setJourneySource(null)
      setIsAnimating(false)
      currentRotationRef.current = DEFAULT_ROTATION
      currentCoordinateRef.current = { latitude: 0, longitude: 0 }
      requestGeometryRender((version) => version + 1)
      return
    }

    const targetCoordinate: GlobeCoordinate = {
      latitude: visual.latitude,
      longitude: visual.longitude,
    }
    const targetRotation = computeRotation(
      targetCoordinate.latitude,
      targetCoordinate.longitude,
      currentRotationRef.current,
    )

    if (previousVisual === visual) {
      return
    }

    const startCoordinate = currentCoordinateRef.current
    setJourneySource(previousVisual !== null
      && isValidCoordinate(previousVisual.latitude, previousVisual.longitude)
      && (previousVisual.latitude !== visual.latitude || previousVisual.longitude !== visual.longitude)
      ? startCoordinate
      : null)

    if (rotationEquals(currentRotationRef.current, targetRotation)) {
      currentCoordinateRef.current = targetCoordinate
      setJourneySource(null)
      setIsAnimating(false)
      return
    }

    if (reducedMotionRef.current) {
      currentRotationRef.current = targetRotation
      currentCoordinateRef.current = targetCoordinate
      requestGeometryRender((version) => version + 1)
      setJourneySource(null)
      setIsAnimating(false)
      return
    }

    setIsAnimating(true)
    const interpolateCoordinate = geoInterpolate(
      [startCoordinate.longitude, startCoordinate.latitude],
      [targetCoordinate.longitude, targetCoordinate.latitude],
    )
    const startTime = performance.now()

    const animate = (timestamp: number): void => {
      const elapsed = timestamp - startTime
      if (elapsed < MARKER_EXIT_DURATION_MS) {
        animationFrameRef.current = requestAnimationFrame(animate)
        return
      }

      const linearProgress = Math.min(
        (elapsed - MARKER_EXIT_DURATION_MS) / TRAVEL_DURATION_MS,
        1,
      )
      const easedProgress = easeInOutCubic(linearProgress)
      const [longitude, latitude] = interpolateCoordinate(easedProgress)
      const interpolatedCoordinate = { latitude, longitude }
      const interpolated = computeRotation(latitude, longitude, currentRotationRef.current)

      currentCoordinateRef.current = interpolatedCoordinate
      currentRotationRef.current = interpolated
      updateGlobeGeometry(
        interpolated,
        startCoordinate,
        targetCoordinate,
        mapCanvasRef.current,
        journeyRef.current,
        easedProgress,
      )

      if (linearProgress < 1) {
        animationFrameRef.current = requestAnimationFrame(animate)
      } else {
        currentCoordinateRef.current = targetCoordinate
        currentRotationRef.current = targetRotation
        requestGeometryRender((version) => version + 1)
        setIsAnimating(false)
        setJourneySource(null)
        animationFrameRef.current = null
      }
    }

    animationFrameRef.current = requestAnimationFrame(animate)

    return () => {
      if (animationFrameRef.current !== null) {
        cancelAnimationFrame(animationFrameRef.current)
        animationFrameRef.current = null
      }
    }
  }, [visual, hasValidVisual])

  useEffect(() => {
    drawGlobeMap(currentRotationRef.current, mapCanvasRef.current)
  }, [visual, hasValidVisual, isAnimating])

  const projection = createProjectionAtRotation(currentRotationRef.current, DEFAULT_GLOBE_SIZE)
  const projectedMarker = hasValidVisual && visual !== null
    ? projection([visual.longitude, visual.latitude])
    : null
  const previousVisual = previousVisualRef.current
  const selectionWillAnimate = previousVisual !== visual
    && previousVisual !== null
    && visual !== null
    && isValidCoordinate(previousVisual.latitude, previousVisual.longitude)
    && (previousVisual.latitude !== visual.latitude || previousVisual.longitude !== visual.longitude)
    && !reducedMotionRef.current
  const showTransition = isAnimating || selectionWillAnimate
  const transitionMarker = showTransition
    ? projection([
        currentCoordinateRef.current.longitude,
        currentCoordinateRef.current.latitude,
      ])
    : projectedMarker
  const markerPosition = transitionMarker === null
    ? null
    : { x: transitionMarker[0], y: transitionMarker[1] }

  if (!HAS_WORLD_GEOMETRY) {
    return (
      <div className="race-globe race-globe--error">
        <p className="race-globe__error-message" role="alert">
          Globe geometry could not be loaded.
        </p>
      </div>
    )
  }

  const ariaLabel = hasValidVisual
    ? `Globe centred on ${eventName}`
    : `Globe for ${eventName}`

  const description = hasValidVisual && visual !== null
    ? `Orthographic globe showing the location of ${eventName} at ${formatCoordinate(visual.latitude)}°, ${formatCoordinate(visual.longitude)}°.`
    : `Static globe displayed for ${eventName}. Location coordinates are not available.`

  return (
    <div className={`race-globe${hasValidVisual ? '' : ' race-globe--placeholder'}${showTransition ? ' race-globe--animating' : ''}`}>
      <canvas
        ref={mapCanvasRef}
        className="race-globe__map"
        width={DEFAULT_GLOBE_SIZE * CANVAS_PIXEL_RATIO}
        height={DEFAULT_GLOBE_SIZE * CANVAS_PIXEL_RATIO}
        aria-hidden="true"
      />
      <svg
        className="race-globe__canvas"
        role="img"
        aria-label={ariaLabel}
        viewBox={`0 0 ${DEFAULT_GLOBE_SIZE} ${DEFAULT_GLOBE_SIZE}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <title>{ariaLabel}</title>
        <desc>{description}</desc>
        <defs>
          <clipPath id="race-globe-sphere-clip">
            <circle
              cx={DEFAULT_GLOBE_SIZE / 2}
              cy={DEFAULT_GLOBE_SIZE / 2}
              r={DEFAULT_GLOBE_SIZE * 0.45}
            />
          </clipPath>
        </defs>
        <circle
          className="race-globe__sphere"
          cx={DEFAULT_GLOBE_SIZE / 2}
          cy={DEFAULT_GLOBE_SIZE / 2}
          r={DEFAULT_GLOBE_SIZE * 0.45}
        />
        {isAnimating && journeySource !== null && visual !== null && (
          <path
            ref={journeyRef}
            className="race-globe__journey"
            d={createJourneyPath(projection, journeySource, {
              latitude: visual.latitude,
              longitude: visual.longitude,
            })}
            pathLength={1}
            clipPath="url(#race-globe-sphere-clip)"
            style={{ strokeDashoffset: 1 }}
          />
        )}
        {markerPosition !== null && (
          <circle
            className="race-globe__marker"
            cx={markerPosition.x}
            cy={markerPosition.y}
            r={5}
          />
        )}
      </svg>
      {!hasValidVisual && (
        <p className="race-globe__placeholder-message">
          {race === null
            ? 'Select a race to see its location on the globe.'
            : 'Location data is not available for this race.'}
        </p>
      )}
    </div>
  )
})

function createProjectionAtRotation(rotation: GlobeRotation, size: number): GeoProjection {
  const projection = createOrthographicProjection(0, 0, { width: size, height: size })
  return projection.rotate([rotation[0], rotation[1], rotation[2]])
}

function updateGlobeGeometry(
  rotation: GlobeRotation,
  source: GlobeCoordinate,
  target: GlobeCoordinate,
  canvas: HTMLCanvasElement | null,
  journey: SVGPathElement | null,
  progress: number,
): void {
  drawGlobeMap(rotation, canvas)
  const projection = createProjectionAtRotation(rotation, DEFAULT_GLOBE_SIZE)
  if (journey === null) return
  journey.setAttribute('d', createJourneyPath(projection, source, target))
  journey.style.strokeDashoffset = String(1 - (2 * progress))
}

function drawGlobeMap(rotation: GlobeRotation, canvas: HTMLCanvasElement | null): void {
  const context = canvas?.getContext('2d')
  if (context === null || context === undefined) return
  const projection = createProjectionAtRotation(rotation, DEFAULT_GLOBE_SIZE)
  const pathGenerator = geoPath(projection, context)

  context.setTransform(1, 0, 0, 1, 0, 0)
  context.clearRect(0, 0, DEFAULT_GLOBE_SIZE * CANVAS_PIXEL_RATIO, DEFAULT_GLOBE_SIZE * CANVAS_PIXEL_RATIO)
  context.setTransform(CANVAS_PIXEL_RATIO, 0, 0, CANVAS_PIXEL_RATIO, 0, 0)

  context.beginPath()
  pathGenerator({ type: 'Sphere' })
  context.fillStyle = '#0f1519'
  context.fill()

  context.beginPath()
  pathGenerator(geoGraticule().step([GRATICULE_STEP_DEGREES, GRATICULE_STEP_DEGREES])())
  context.strokeStyle = 'rgb(214 255 0 / 14%)'
  context.lineWidth = 0.5
  context.stroke()

  context.beginPath()
  pathGenerator(worldLand)
  context.fillStyle = '#182027'
  context.strokeStyle = '#44505a'
  context.lineJoin = 'round'
  context.lineWidth = 0.75
  context.fill()
  context.stroke()
}

function createJourneyPath(
  projection: GeoProjection,
  source: GlobeCoordinate,
  target: GlobeCoordinate,
): string {
  const start = projection([source.longitude, source.latitude])
  const end = projection([target.longitude, target.latitude])
  if (start === null || end === null) return ''

  const deltaX = end[0] - start[0]
  const deltaY = end[1] - start[1]
  const distance = Math.hypot(deltaX, deltaY)
  if (distance === 0) return `M ${start[0]} ${start[1]} L ${end[0]} ${end[1]}`

  const midpointX = (start[0] + end[0]) / 2
  const midpointY = (start[1] + end[1]) / 2
  const curveDistance = Math.min((distance / DEFAULT_GLOBE_SIZE) * 100, 100)
  const normalX = -deltaY / distance
  const normalY = deltaX / distance
  const controlX = midpointX + (normalX * curveDistance)
  const controlY = midpointY + (normalY * curveDistance)

  return `M ${start[0]} ${start[1]} Q ${controlX} ${controlY} ${end[0]} ${end[1]}`
}

function easeInOutCubic(t: number): number {
  return t < 0.5
    ? 4 * Math.pow(t, 3)
    : 1 - Math.pow(-2 * t + 2, 3) / 2
}

function isValidCoordinate(latitude: number, longitude: number): boolean {
  return (
    Number.isFinite(latitude) &&
    Number.isFinite(longitude) &&
    latitude >= -90 &&
    latitude <= 90 &&
    longitude >= -180 &&
    longitude <= 180
  )
}

function rotationEquals(a: GlobeRotation, b: GlobeRotation): boolean {
  return a[0] === b[0] && a[1] === b[1] && a[2] === b[2]
}

function formatCoordinate(value: number): string {
  return String(Math.round(value * 10_000) / 10_000)
}
