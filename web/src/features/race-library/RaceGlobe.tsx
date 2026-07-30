import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { geoInterpolate, geoPath } from 'd3-geo'
import type { GeoProjection } from 'd3-geo'
import type { CatalogV2Race } from '../../data/catalog/types'
import {
  buildGraticulePath,
  computeRotation,
  createOrthographicProjection,
  worldCountries,
  type GlobeRotation,
} from '../../geo/globe-projection'

const DEFAULT_GLOBE_SIZE = 400
const ANIMATION_DURATION_MS = 400
const DEFAULT_ROTATION: GlobeRotation = [0, 0, 0]
const GRATICULE_STEP_DEGREES = 15

const HAS_WORLD_GEOMETRY = worldCountries.features.length > 0
const JOURNEY_SAMPLE_COUNT = 32

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
 * - Rotation is animated with requestAnimationFrame (~400ms, ease-out) along the
 *   shortest longitude path when the selected race changes.
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

  const [displayRotation, setDisplayRotation] = useState<GlobeRotation>(initialRotation)
  const [journeySource, setJourneySource] = useState<GlobeCoordinate | null>(null)
  const currentRotationRef = useRef<GlobeRotation>(initialRotation)
  const previousVisualRef = useRef(visual)
  const animationFrameRef = useRef<number | null>(null)
  const reducedMotionRef = useRef(false)

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
      currentRotationRef.current = DEFAULT_ROTATION
      setDisplayRotation(DEFAULT_ROTATION)
      return
    }

    const targetRotation = computeRotation(
      visual.latitude,
      visual.longitude,
      currentRotationRef.current,
    )

    if (previousVisual === visual) {
      return
    }

    setJourneySource(previousVisual !== null
      && isValidCoordinate(previousVisual.latitude, previousVisual.longitude)
      && (previousVisual.latitude !== visual.latitude || previousVisual.longitude !== visual.longitude)
      ? { latitude: previousVisual.latitude, longitude: previousVisual.longitude }
      : null)

    if (rotationEquals(currentRotationRef.current, targetRotation)) {
      return
    }

    if (reducedMotionRef.current) {
      currentRotationRef.current = targetRotation
      setDisplayRotation(targetRotation)
      return
    }

    const startRotation: GlobeRotation = [
      currentRotationRef.current[0],
      currentRotationRef.current[1],
      currentRotationRef.current[2],
    ]
    const startTime = performance.now()

    const animate = (timestamp: number): void => {
      const elapsed = timestamp - startTime
      const linearProgress = Math.min(elapsed / ANIMATION_DURATION_MS, 1)
      const easedProgress = easeOutCubic(linearProgress)

      const interpolated: GlobeRotation = [
        startRotation[0] + (targetRotation[0] - startRotation[0]) * easedProgress,
        startRotation[1] + (targetRotation[1] - startRotation[1]) * easedProgress,
        startRotation[2] + (targetRotation[2] - startRotation[2]) * easedProgress,
      ]

      currentRotationRef.current = interpolated
      setDisplayRotation(interpolated)

      if (linearProgress < 1) {
        animationFrameRef.current = requestAnimationFrame(animate)
      } else {
        currentRotationRef.current = targetRotation
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

  const projection = useMemo(
    () => createProjectionAtRotation(displayRotation, DEFAULT_GLOBE_SIZE),
    [displayRotation],
  )

  const pathGenerator = useMemo(() => geoPath(projection), [projection])

  const graticulePath = useMemo(
    () => buildGraticulePath(projection, GRATICULE_STEP_DEGREES),
    [projection],
  )

  const countryPaths = useMemo(() => {
    return worldCountries.features.map((feature) => ({
      key: feature.id != null ? String(feature.id) : feature.properties?.name ?? '',
      d: pathGenerator(feature) ?? '',
    }))
  }, [pathGenerator])

  const journeyPath = useMemo(() => {
    if (!hasValidVisual || visual === null || journeySource === null) return ''
    const interpolate = geoInterpolate(
      [journeySource.longitude, journeySource.latitude],
      [visual.longitude, visual.latitude],
    )
    const coordinates = Array.from(
      { length: JOURNEY_SAMPLE_COUNT + 1 },
      (_, index) => interpolate(index / JOURNEY_SAMPLE_COUNT),
    )
    return pathGenerator({ type: 'LineString', coordinates }) ?? ''
  }, [hasValidVisual, journeySource, pathGenerator, visual])

  const markerPosition = useMemo(() => {
    if (!hasValidVisual || visual === null) return null
    const projected = projection([visual.longitude, visual.latitude])
    if (projected === null) return null
    return { x: projected[0], y: projected[1] }
  }, [projection, hasValidVisual, visual])

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
    <div className={`race-globe${hasValidVisual ? '' : ' race-globe--placeholder'}`}>
      <svg
        className="race-globe__canvas"
        role="img"
        aria-label={ariaLabel}
        viewBox={`0 0 ${DEFAULT_GLOBE_SIZE} ${DEFAULT_GLOBE_SIZE}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <title>{ariaLabel}</title>
        <desc>{description}</desc>
        <circle
          className="race-globe__sphere"
          cx={DEFAULT_GLOBE_SIZE / 2}
          cy={DEFAULT_GLOBE_SIZE / 2}
          r={DEFAULT_GLOBE_SIZE * 0.45}
        />
        <path className="race-globe__graticule" d={graticulePath} />
        <g className="race-globe__countries">
          {countryPaths.map(({ key, d }) => (
            <path key={key} className="race-globe__country" d={d} />
          ))}
        </g>
        {journeyPath !== '' && (
          <path
            key={`${journeySource?.longitude ?? 0}-${visual?.longitude ?? 0}`}
            className="race-globe__journey"
            d={journeyPath}
            pathLength={1}
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

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3)
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
