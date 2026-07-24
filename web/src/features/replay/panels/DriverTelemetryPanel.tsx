import { memo, type CSSProperties } from 'react'
import type { DriverMetadata } from '../../../data/replay/types'
import type { ReplayDriverSnapshot, ReplaySnapshot } from '../../../engine/replay/types'

export interface DriverTelemetryPanelProps {
  readonly drivers: readonly DriverMetadata[]
  readonly selectedDriverId: string | null
  readonly snapshot: ReplaySnapshot | null
}

interface TelemetryReading {
  readonly label: string
  readonly value: string
}

const UNAVAILABLE = 'Unavailable'
const TEAM_ACCENT_FALLBACK = '#7a8794'
const HEX_COLOR = /^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/i

/** Renders the selected driver's sampled car telemetry without inventing missing values. */
export const DriverTelemetryPanel = memo(function DriverTelemetryPanel({ drivers, selectedDriverId, snapshot }: DriverTelemetryPanelProps) {
  const driver = selectedDriverId === null ? null : drivers.find(({ id }) => id === selectedDriverId) ?? null
  const sampled = driver === null || snapshot === null ? null : snapshot.drivers[driver.id] ?? null

  if (driver === null) {
    return <section className="driver-telemetry-panel" aria-label="Driver telemetry"><p className="driver-telemetry-panel__empty" role="status">Driver telemetry is unavailable. Select a driver to view it.</p></section>
  }

  const readings = createReadings(sampled)
  return (
    <article className="driver-telemetry-panel" aria-labelledby="driver-telemetry-title" style={teamAccentStyle(driver.colorHex)}>
      <header className="driver-telemetry-panel__header">
        <span className="driver-telemetry-panel__accent" aria-hidden="true" />
        <div><p className="driver-telemetry-panel__eyebrow">Live telemetry</p><h2 id="driver-telemetry-title">{driver.displayName} <span>#{driver.carNumber}</span></h2></div>
      </header>
      <div className="driver-telemetry-panel__layout">
        <ThrottleGauge value={sampled?.throttle ?? null} displayValue={readings[2].value} />
        <dl className="driver-telemetry-panel__readings">
          {readings.map((reading) => <div className="driver-telemetry-panel__reading" key={reading.label}><dt>{reading.label}</dt><dd>{reading.value}</dd></div>)}
        </dl>
      </div>
    </article>
  )
})

function ThrottleGauge({ value, displayValue }: { readonly value: number | null; readonly displayValue: string }) {
  const normalized = normalizePercent(value)
  return (
    <div className="driver-telemetry-panel__gauge">
      <svg aria-hidden="true" className="driver-telemetry-panel__gauge-svg" viewBox="0 0 120 120">
        <path className="driver-telemetry-panel__gauge-track" d={describeArc(60, 60, 45, -135, 135)} pathLength="100" />
        {normalized !== null && normalized > 0 ? <path className="driver-telemetry-panel__gauge-value" d={describeArc(60, 60, 45, -135, -135 + normalized * 2.7)} pathLength="100" /> : null}
      </svg>
      <div className="driver-telemetry-panel__gauge-copy"><span>Throttle</span><strong>{displayValue}</strong></div>
    </div>
  )
}

function createReadings(sampled: ReplayDriverSnapshot | null): readonly TelemetryReading[] {
  return [
    { label: 'Speed', value: formatInteger(sampled?.speed ?? null, 'km/h') },
    { label: 'RPM', value: formatInteger(sampled?.rpm ?? null, 'RPM') },
    { label: 'Throttle', value: formatPercent(sampled?.throttle ?? null) },
    { label: 'Brake', value: formatBrake(sampled?.brake ?? null) },
    { label: 'Gear', value: formatInteger(sampled?.gear ?? null) },
    { label: 'DRS', value: formatDrs(sampled?.drs ?? null) },
    { label: 'Lap', value: formatInteger(sampled?.lap ?? null) },
    { label: 'Gap', value: formatGap(sampled?.gapToLeaderMs ?? null) },
  ]
}

export function formatDrs(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return UNAVAILABLE
  if (value === 0 || value === 1) return 'Off'
  if (value === 8) return 'Eligible'
  if (value === 10 || value === 12 || value === 14) return 'Active'
  return 'Unknown'
}

function formatBrake(value: number | null): string {
  if (value === 0) return 'Released'
  if (value === 1) return 'Applied'
  return UNAVAILABLE
}

function formatInteger(value: number | null, suffix = ''): string {
  if (value === null || !Number.isFinite(value)) return UNAVAILABLE
  return `${Math.round(value).toLocaleString('en-US')}${suffix === '' ? '' : ` ${suffix}`}`
}

function formatPercent(value: number | null): string {
  const normalized = normalizePercent(value)
  return normalized === null ? UNAVAILABLE : `${Math.round(normalized)}%`
}

function normalizePercent(value: number | null): number | null {
  return value === null || !Number.isFinite(value) || value < 0 || value > 100 ? null : value
}

function formatGap(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return UNAVAILABLE
  if (value === 0) return 'Leader'
  return `+${(value / 1_000).toFixed(3)} s`
}

function teamAccentStyle(colorHex: string): CSSProperties {
  return { '--driver-telemetry-team-color': HEX_COLOR.test(colorHex) ? colorHex : TEAM_ACCENT_FALLBACK } as CSSProperties
}

/** Creates an SVG arc between angles expressed in degrees. */
export function describeArc(centerX: number, centerY: number, radius: number, startAngle: number, endAngle: number): string {
  const start = polarToCartesian(centerX, centerY, radius, endAngle)
  const end = polarToCartesian(centerX, centerY, radius, startAngle)
  const largeArc = endAngle - startAngle <= 180 ? '0' : '1'
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} 0 ${end.x} ${end.y}`
}

function polarToCartesian(centerX: number, centerY: number, radius: number, angle: number): Readonly<{ x: number; y: number }> {
  const radians = (angle - 90) * Math.PI / 180
  return { x: centerX + radius * Math.cos(radians), y: centerY + radius * Math.sin(radians) }
}
