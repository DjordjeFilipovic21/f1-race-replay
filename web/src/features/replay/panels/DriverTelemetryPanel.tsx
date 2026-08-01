import { memo, useId, useState, type CSSProperties } from 'react'
import type { DriverMetadata, SeasonMetadata, TelemetryCapabilities } from '../../../data/replay/types'
import type { ReplayDriverSnapshot, ReplaySnapshot } from '../../../engine/replay/types'

export interface DriverTelemetryPanelProps {
  readonly drivers: readonly DriverMetadata[]
  readonly selectedDriverId: string | null
  readonly seasonMetadata?: SeasonMetadata
  readonly telemetryCapabilities?: TelemetryCapabilities
  readonly snapshot: ReplaySnapshot | null
}

const DISPLAY_PLACEHOLDER = '—'
const UNAVAILABLE_LABEL = 'Unavailable'
const DRS_NOT_PUBLISHED = 'Not published'
const DRS_UNAVAILABLE_MESSAGE = 'DRS / Overtake Mode telemetry is unavailable (not published). Public telemetry does not contain that signal.'
const TEAM_ACCENT_FALLBACK = '#7a8794'
const HEX_COLOR = /^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/i
const SPEED_MAX_KMH = 360
const GAUGE_CENTER = 100
const GAUGE_START_ANGLE = -144
const GAUGE_END_ANGLE = 144
const THROTTLE_END_ANGLE = 42
const BRAKE_START_ANGLE = 50
const SPEED_TICKS = [0, 60, 120, 180, 240, 300, 360] as const

/** Renders the selected driver's sampled car telemetry without inventing missing values. */
export const DriverTelemetryPanel = memo(function DriverTelemetryPanel({ drivers, selectedDriverId, seasonMetadata, telemetryCapabilities, snapshot }: DriverTelemetryPanelProps) {
  const driver = selectedDriverId === null ? null : drivers.find(({ id }) => id === selectedDriverId) ?? null
  const sampled = driver === null || snapshot === null ? null : snapshot.drivers[driver.id] ?? null

  if (driver === null) {
    return <section className="driver-telemetry-panel" aria-label="Driver telemetry"><p className="driver-telemetry-panel__empty" role="status">Driver telemetry is unavailable. Select a driver to view it.</p></section>
  }

  const drsNotPublished = isDrsNotPublished(seasonMetadata, telemetryCapabilities)
  return (
    <article className="driver-telemetry-panel" aria-labelledby="driver-telemetry-title" style={teamAccentStyle(driver.colorHex)}>
      <header className="driver-telemetry-panel__header">
        <span className="driver-telemetry-panel__accent" aria-hidden="true" />
        <div><h2 id="driver-telemetry-title">Live telemetry</h2></div>
      </header>
      <TelemetryGauge sampled={sampled} drsNotPublished={drsNotPublished} />
    </article>
  )
})

function TelemetryGauge({ sampled, drsNotPublished }: { readonly sampled: ReplayDriverSnapshot | null; readonly drsNotPublished: boolean }) {
  const labelPathId = useId()
  const tooltipId = `${labelPathId}-drs-tooltip`
  const [isTooltipVisible, setTooltipVisible] = useState(false)
  const speed = sampled?.speed ?? null
  const rpm = sampled?.rpm ?? null
  const throttle = sampled?.throttle ?? null
  const brake = sampled?.brake ?? null
  const gear = sampled?.gear ?? null
  const drs = sampled?.drs ?? null
  const normalizedSpeed = normalizeSpeed(speed)
  const normalizedThrottle = normalizePercent(throttle)
  const speedValue = formatInteger(speed)
  const rpmValue = formatInteger(rpm)
  const throttleValue = formatPercent(throttle)
  const brakeValue = formatBrake(brake)
  const gearValue = formatInteger(gear)
  const drsValue = drsNotPublished ? DRS_NOT_PUBLISHED : formatDrs(drs)
  const drsLabel = drsNotPublished ? 'DRS / Overtake Mode' : 'DRS'
  const drsDisplay = 'DRS'
  const accessibleLabel = [
    `Speed ${speedValue === DISPLAY_PLACEHOLDER ? UNAVAILABLE_LABEL : `${speedValue} kilometers per hour`}`,
    `RPM ${spokenValue(rpmValue)}`,
    `Throttle ${spokenValue(throttleValue)}`,
    `Brake ${spokenValue(brakeValue)}`,
    `Gear ${spokenValue(gearValue)}`,
    `${drsLabel} ${spokenValue(drsValue)}`,
  ].join(', ')

  return (
    <div className="driver-telemetry-panel__gauge">
      <svg aria-label={accessibleLabel} className="driver-telemetry-panel__gauge-svg" role="img" viewBox="0 0 200 200">
        <defs>
          <path id={`${labelPathId}-throttle`} d={describeClockwiseArc(GAUGE_CENTER, GAUGE_CENTER, 63, GAUGE_START_ANGLE, THROTTLE_END_ANGLE)} />
          <path id={`${labelPathId}-brake`} d={describeClockwiseArc(GAUGE_CENTER, GAUGE_CENTER, 63, BRAKE_START_ANGLE, GAUGE_END_ANGLE)} />
        </defs>
        <path className="driver-telemetry-panel__speed-track" d={describeArc(GAUGE_CENTER, GAUGE_CENTER, 82, GAUGE_START_ANGLE, GAUGE_END_ANGLE)} />
        {normalizedSpeed !== null && normalizedSpeed > 0
          ? <path className="driver-telemetry-panel__speed-value" d={describeArc(GAUGE_CENTER, GAUGE_CENTER, 82, GAUGE_START_ANGLE, speedAngle(normalizedSpeed))} />
          : null}
        {SPEED_TICKS.map((tick) => {
          const point = polarToCartesian(GAUGE_CENTER, GAUGE_CENTER, 82, speedAngle(tick))
          return <text className="driver-telemetry-panel__speed-tick" key={tick} x={point.x} y={point.y}>{tick}</text>
        })}

        <path className="driver-telemetry-panel__input-track" d={describeArc(GAUGE_CENTER, GAUGE_CENTER, 63, GAUGE_START_ANGLE, THROTTLE_END_ANGLE)} />
        {normalizedThrottle !== null && normalizedThrottle > 0
          ? <path className="driver-telemetry-panel__throttle-value" d={describeArc(GAUGE_CENTER, GAUGE_CENTER, 63, GAUGE_START_ANGLE, throttleAngle(normalizedThrottle))} />
          : null}
        <path className="driver-telemetry-panel__input-track" d={describeArc(GAUGE_CENTER, GAUGE_CENTER, 63, BRAKE_START_ANGLE, GAUGE_END_ANGLE)} />
        {brake === 1 ? <path className="driver-telemetry-panel__brake-value" d={describeArc(GAUGE_CENTER, GAUGE_CENTER, 63, BRAKE_START_ANGLE, GAUGE_END_ANGLE)} /> : null}

        <text className="driver-telemetry-panel__input-label">
          <textPath href={`#${labelPathId}-throttle`} startOffset="13%">Throttle</textPath>
        </text>
        <text className="driver-telemetry-panel__input-label">
          <textPath href={`#${labelPathId}-brake`} startOffset="24%">Brake</textPath>
        </text>

        <text className="driver-telemetry-panel__speed" x="100" y="87">{speedValue}</text>
        <text className="driver-telemetry-panel__unit" x="100" y="100">km/h</text>
        <text className="driver-telemetry-panel__rpm" x="100" y="124">{rpmValue}</text>
        <text className="driver-telemetry-panel__unit" x="100" y="136">RPM</text>
        <g className={`driver-telemetry-panel__drs${drsValue === 'Active' ? ' driver-telemetry-panel__drs--active' : ''}${drsNotPublished ? ' driver-telemetry-panel__drs--unavailable' : ''}`}>
          <rect height="20" rx="4" width="52" x="74" y="145" />
          <text x="100" y="159">{drsDisplay}</text>
          {drsNotPublished ? <line className="driver-telemetry-panel__drs-strike" x1="94" x2="106" y1="156" y2="156" /> : null}
        </g>
        {drsNotPublished ? (
          <g
            aria-label="Why is DRS telemetry unavailable?"
            aria-describedby={tooltipId}
            className="driver-telemetry-panel__drs-tooltip-trigger"
            onBlur={() => setTooltipVisible(false)}
            onFocus={() => setTooltipVisible(true)}
            onMouseEnter={() => setTooltipVisible(true)}
            onMouseLeave={() => setTooltipVisible(false)}
            role="button"
            tabIndex={0}
            transform="translate(112 156)"
          >
            <circle className="driver-telemetry-panel__drs-tooltip-circle" cx="0" cy="0" r="3.5" />
            <text className="driver-telemetry-panel__drs-tooltip-icon" x="0" y="1.8">i</text>
          </g>
        ) : null}
        <text className="driver-telemetry-panel__gear" x="100" y="190">
          Gear <tspan>{gearValue}</tspan>
        </text>
      </svg>
      {drsNotPublished && isTooltipVisible ? (
        <span id={tooltipId} className="driver-telemetry-panel__drs-tooltip-content" role="tooltip">
          {DRS_UNAVAILABLE_MESSAGE}
        </span>
      ) : null}
    </div>
  )
}

function speedAngle(speed: number): number {
  return GAUGE_START_ANGLE + speed / SPEED_MAX_KMH * (GAUGE_END_ANGLE - GAUGE_START_ANGLE)
}

function throttleAngle(throttle: number): number {
  return GAUGE_START_ANGLE + throttle / 100 * (THROTTLE_END_ANGLE - GAUGE_START_ANGLE)
}

function normalizeSpeed(value: number | null): number | null {
  return value === null || !Number.isFinite(value) || value < 0 ? null : Math.min(value, SPEED_MAX_KMH)
}

function isDrsNotPublished(seasonMetadata?: SeasonMetadata, telemetryCapabilities?: TelemetryCapabilities): boolean {
  if (telemetryCapabilities?.drs === 'not-published') return true
  if (telemetryCapabilities?.drs === 'available') return false
  return seasonMetadata?.year !== undefined && seasonMetadata.year >= 2026
}

export function formatDrs(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return DISPLAY_PLACEHOLDER
  if (value === 0 || value === 1) return 'Off'
  if (value === 8) return 'Eligible'
  if (value === 10 || value === 12 || value === 14) return 'Active'
  return 'Unknown'
}

function formatBrake(value: number | null): string {
  if (value === 0) return 'Released'
  if (value === 1) return 'Applied'
  return DISPLAY_PLACEHOLDER
}

function formatInteger(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return DISPLAY_PLACEHOLDER
  return Math.round(value).toLocaleString('en-US')
}

function formatPercent(value: number | null): string {
  const normalized = normalizePercent(value)
  return normalized === null ? DISPLAY_PLACEHOLDER : `${Math.round(normalized)}%`
}

function normalizePercent(value: number | null): number | null {
  return value === null || !Number.isFinite(value) || value < 0 || value > 100 ? null : value
}

function spokenValue(value: string): string {
  return value === DISPLAY_PLACEHOLDER ? UNAVAILABLE_LABEL : value
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

function describeClockwiseArc(centerX: number, centerY: number, radius: number, startAngle: number, endAngle: number): string {
  const start = polarToCartesian(centerX, centerY, radius, startAngle)
  const end = polarToCartesian(centerX, centerY, radius, endAngle)
  const largeArc = endAngle - startAngle <= 180 ? '0' : '1'
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} 1 ${end.x} ${end.y}`
}

function polarToCartesian(centerX: number, centerY: number, radius: number, angle: number): Readonly<{ x: number; y: number }> {
  const radians = (angle - 90) * Math.PI / 180
  return { x: centerX + radius * Math.cos(radians), y: centerY + radius * Math.sin(radians) }
}
