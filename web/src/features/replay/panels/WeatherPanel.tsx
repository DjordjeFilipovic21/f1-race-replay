import { memo } from 'react'
import type { WeatherSidecar } from '../../../data/replay/types'
import type { ReplayControllerSnapshot } from '../../../engine/replay'
import { causalWeatherSelector, type WeatherSelection } from '../selectors/weather-selectors'

const DISPLAY_PLACEHOLDER = '—'
const COMPASS_POINTS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'] as const

export interface WeatherPanelProps {
  readonly snapshot: ReplayControllerSnapshot
  readonly weatherSidecar?: WeatherSidecar | null
}

/** Presents the latest causal weather observation without interpolating between rows. */
export const WeatherPanel = memo(function WeatherPanel({ snapshot, weatherSidecar }: WeatherPanelProps) {
  const selection = causalWeatherSelector(weatherSidecar, snapshot.replay ?? snapshot.timeMs)
  // Stale rows remain available to the selector for age/reason metadata, but
  // must not be presented as current conditions in the fail-closed panel.
  const measurements = selection.status === 'stale' ? null : selection
  const statusLabel = formatStatusLabel(selection.status)
  const statusDescription = formatStatusDescription(selection)

  return (
    <article className={`weather-panel weather-panel--${selection.status}`} data-state={selection.status} aria-labelledby="weather-panel-title">
      <header className="weather-panel__header">
        <div>
          <h2 id="weather-panel-title">Weather</h2>
        </div>
        <span className={`weather-panel__status weather-panel__status--${selection.status}`} aria-label={`Weather observations ${statusDescription}`}>
          {statusLabel}
        </span>
      </header>

      {selection.status !== 'fresh' && (
        <p className="weather-panel__notice" role="status">
          {selection.status === 'stale'
            ? 'Weather observations are stale and unavailable at this replay time.'
            : 'Weather observations are unavailable at this replay time.'}
        </p>
      )}

      <div className="weather-panel__layout">
        <section className="weather-panel__wind" aria-labelledby="weather-wind-title">
          <div className="weather-panel__section-heading">
            <h3 id="weather-wind-title">Wind from</h3>
          </div>
          <div className="weather-panel__wind-summary">
            <WindDirection directionDeg={measurements?.windDirectionDeg ?? null} />
            <MetricValue label="Wind speed" value={formatWindSpeed(measurements?.windSpeedMps ?? null)} />
          </div>
        </section>

        <dl className="weather-panel__metrics" aria-label="Weather observations">
          <WeatherMetric label="Air temp" value={formatTemperature(measurements?.airTempC ?? null)} />
          <WeatherMetric label="Track temp" value={formatTemperature(measurements?.trackTempC ?? null)} />
          <WeatherMetric label="Pressure" value={formatPressure(measurements?.pressureMbar ?? null)} />
          <WeatherMetric label="Humidity" value={formatPercentage(measurements?.humidityPct ?? null)} />
        </dl>
      </div>

      <footer className={`weather-panel__update weather-panel__update--${selection.status}`}>
        <span className="weather-panel__update-label">Last observation</span>
        <span className="weather-panel__update-value">{formatObservationAge(selection.ageMs)}</span>
        <span className="weather-panel__update-detail">{statusDescription}</span>
      </footer>
    </article>
  )
})

function WindDirection({ directionDeg }: { readonly directionDeg: number | null }): React.JSX.Element {
  const direction = normalizeDirection(directionDeg)
  const compass = direction === null ? DISPLAY_PLACEHOLDER : compassPoint(direction)
  const degrees = direction === null ? DISPLAY_PLACEHOLDER : `${direction}°`
  const accessibleLabel = direction === null
    ? 'Wind direction unavailable'
    : `Wind from ${degrees}, ${compass}`

  return (
    <div className="weather-panel__wind-direction" role="img" aria-label={accessibleLabel}>
      <svg className="weather-panel__wind-arrow" viewBox="0 0 48 48" aria-hidden="true">
        {direction === null ? null : (
          <g transform={`rotate(${direction} 24 24)`}>
            <path d="M24 6v30" />
            <path d="m17 13 7-7 7 7" />
          </g>
        )}
        {direction === null ? <path className="weather-panel__wind-arrow-placeholder" d="M15 24h18M24 15v18" /> : null}
      </svg>
      <div className="weather-panel__wind-reading">
        <strong>{degrees}</strong>
        <span>{compass} · from</span>
      </div>
    </div>
  )
}

function WeatherMetric({ label, value }: { readonly label: string; readonly value: string }): React.JSX.Element {
  return (
    <div className="weather-panel__metric">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

function MetricValue({ label, value }: { readonly label: string; readonly value: string }): React.JSX.Element {
  return (
    <div className="weather-panel__wind-speed">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function normalizeDirection(value: number | null): number | null {
  if (value === null || !Number.isFinite(value)) return null
  return ((Math.round(value) % 360) + 360) % 360
}

export function compassPoint(directionDeg: number): string {
  const direction = normalizeDirection(directionDeg)
  return direction === null ? DISPLAY_PLACEHOLDER : COMPASS_POINTS[Math.round(direction / 22.5) % COMPASS_POINTS.length]
}

function formatTemperature(value: number | null): string {
  return formatNumber(value, 1, '°C')
}

function formatWindSpeed(value: number | null): string {
  return formatNumber(value, 1, 'm/s')
}

function formatPercentage(value: number | null): string {
  return formatNumber(value, 0, '%')
}

function formatPressure(value: number | null): string {
  return formatNumber(value === null ? null : value / 1_000, 3, 'bar')
}

function formatNumber(value: number | null, decimals: number, unit: string): string {
  return value === null || !Number.isFinite(value) ? DISPLAY_PLACEHOLDER : `${value.toFixed(decimals)} ${unit}`
}

function formatObservationAge(ageMs: number | null): string {
  if (ageMs === null || !Number.isFinite(ageMs) || ageMs < 0) return DISPLAY_PLACEHOLDER
  if (ageMs < 1_000) return 'Just now'
  const totalSeconds = Math.floor(ageMs / 1_000)
  if (totalSeconds < 60) return `${totalSeconds}s ago`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return seconds === 0 ? `${minutes}m ago` : `${minutes}m ${seconds}s ago`
}

function formatStatusLabel(status: WeatherSelection['status']): string {
  return status.charAt(0).toUpperCase() + status.slice(1)
}

function formatStatusDescription(selection: WeatherSelection): string {
  if (selection.status === 'unavailable') return 'unavailable'
  const age = formatObservationAge(selection.ageMs)
  return selection.status === 'stale' ? `stale, ${age}` : `fresh, ${age}`
}
