import { memo, type CSSProperties } from 'react'
import type { DriverMetadata } from '../replay-data/types'
import type { ReplaySnapshot } from '../replay-engine/types'

export interface DriverInfoPanelProps {
  readonly drivers: readonly DriverMetadata[]
  readonly selectedDriverId: string | null
  readonly snapshot: ReplaySnapshot | null
}

/** Displays concise metadata and sampled race state for the shared selection. */
export const DriverInfoPanel = memo(function DriverInfoPanel({ drivers, selectedDriverId, snapshot }: DriverInfoPanelProps) {
  const driver = selectedDriverId === null ? null : drivers.find(({ id }) => id === selectedDriverId) ?? null
  const sampled = driver === null || snapshot === null ? null : snapshot.drivers[driver.id] ?? null

  if (driver === null) {
    return <div className="driver-info-panel"><p className="driver-info-panel__empty" role="status">Driver information is unavailable.</p></div>
  }

  return (
    <article className="driver-info-panel" style={teamAccentStyle(driver.colorHex)}>
      <header className="driver-info-panel__top">
        <span className="driver-info-panel__position" aria-label={`Current position ${formatPosition(sampled?.position ?? null, sampled?.status ?? null)}`}>{formatPosition(sampled?.position ?? null, sampled?.status ?? null)}</span>
        <span className="driver-info-panel__accent" aria-hidden="true" />
        <h2 className="driver-info-panel__name">{driver.displayName}</h2>
        <span className="driver-info-panel__number">#{driver.carNumber}</span>
      </header>
      <dl className="driver-info-panel__bottom">
        <div className="driver-info-panel__segment"><dt>Team</dt><dd>{driver.teamName}</dd></div>
        <div className="driver-info-panel__segment"><dt>Status</dt><dd>{formatStatus(sampled?.status ?? null, sampled?.isInPitLane ?? null)}</dd></div>
        <div className="driver-info-panel__segment"><dt>Tyre</dt><dd>{formatTyre(sampled?.tyreCompound ?? null)}</dd></div>
      </dl>
    </article>
  )
})

const TEAM_ACCENT_FALLBACK = '#7a8794'
const HEX_COLOR = /^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/i

function teamAccentStyle(colorHex: string): CSSProperties {
  return { '--driver-info-team-color': HEX_COLOR.test(colorHex) ? colorHex : TEAM_ACCENT_FALLBACK } as CSSProperties
}

function formatPosition(position: number | null, status: string | null): string {
  return normalizeStatus(status) === 'OUT' ? 'OUT' : position === null || !Number.isFinite(position) ? '—' : String(position)
}

function formatStatus(status: string | null, isInPitLane: boolean | null): string {
  if (normalizeStatus(status) === 'OUT') return 'OUT'
  if (isInPitLane === true) return 'PIT'
  return status?.trim() || '—'
}

function formatTyre(tyreCompound: string | null): string {
  return tyreCompound?.trim().toUpperCase() || '—'
}

function normalizeStatus(status: string | null): string {
  return status?.replace(/[\s_-]/g, '').toUpperCase() ?? ''
}
