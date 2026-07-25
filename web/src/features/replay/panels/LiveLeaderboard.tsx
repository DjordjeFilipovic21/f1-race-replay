import { memo, useState, type CSSProperties, type ReactNode } from 'react'
import type { DriverMetadata } from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'
import hardTyreImage from '../../../assets/tyres/hard.png'
import intermediateTyreImage from '../../../assets/tyres/intermediate.png'
import mediumTyreImage from '../../../assets/tyres/medium.png'
import softTyreImage from '../../../assets/tyres/soft.png'
import wetTyreImage from '../../../assets/tyres/wet.png'

type MetricMode = 'leader' | 'interval' | 'tyres'
type TyreCompound = 'SOFT' | 'MEDIUM' | 'HARD' | 'INTERMEDIATE' | 'WET'

export interface LiveLeaderboardProps {
  readonly snapshot: ReplaySnapshot | null
  readonly drivers: readonly DriverMetadata[]
  readonly selectedDriverId?: string | null
  readonly onDriverSelect?: (driverId: string) => void
}

interface LeaderboardRow {
  readonly id: string
  readonly metadata: DriverMetadata | null
  readonly position: number | null
  readonly gapToLeaderMs: number | null
  readonly tyreCompound: string | null
  readonly tyreAge: number | null
  readonly status: string | null
  readonly isInPitLane: boolean | null
  readonly isFinished: boolean
}

/** Renders sampled leaderboard data without subscribing to replay state. */
export const LiveLeaderboard = memo(function LiveLeaderboard({ snapshot, drivers, selectedDriverId = null, onDriverSelect }: LiveLeaderboardProps) {
  const [metricMode, setMetricMode] = useState<MetricMode>('leader')
  const rows = createLeaderboardRows(snapshot, drivers)

  return (
    <section className="live-leaderboard" aria-label="Leaderboard">
      <header className="live-leaderboard__header">
        <div className="live-leaderboard__gap-toggle" role="group" aria-label="Gap display">
          <button type="button" aria-pressed={metricMode === 'leader'} onClick={() => setMetricMode('leader')}>Leader</button>
          <button type="button" aria-pressed={metricMode === 'interval'} onClick={() => setMetricMode('interval')}>Interval</button>
          <button type="button" aria-pressed={metricMode === 'tyres'} onClick={() => setMetricMode('tyres')}>Tyres</button>
        </div>
      </header>
      {snapshot === null ? (
        <p className="live-leaderboard__empty" role="status">Live positions are unavailable while replay samples load.</p>
      ) : rows.length === 0 ? (
        <p className="live-leaderboard__empty" role="status">No driver metadata is available for this replay.</p>
      ) : (
        <table className="live-leaderboard__table" aria-live="polite" aria-relevant="all">
          <caption>Live race leaderboard</caption>
          <colgroup>
            <col className="live-leaderboard__column--position" />
            <col className="live-leaderboard__column--team-accent" />
            <col className="live-leaderboard__column--driver" />
            <col className="live-leaderboard__column--metric" />
          </colgroup>
          <thead>
            <tr><th scope="col">Position</th><th scope="col">Team colour</th><th scope="col">Driver</th><th scope="col">{metricMode === 'leader' ? 'Leader gap' : metricMode === 'interval' ? 'Interval' : 'Tyres'}</th></tr>
          </thead>
          <tbody>
            {rows.map((row, index) => <LeaderboardTableRow key={row.id} row={row} ahead={rows[index - 1] ?? null} metricMode={metricMode} isSelected={row.id === selectedDriverId} onDriverSelect={onDriverSelect} />)}
          </tbody>
        </table>
      )}
    </section>
  )
})

function LeaderboardTableRow({ row, ahead, metricMode, isSelected, onDriverSelect }: { readonly row: LeaderboardRow; readonly ahead: LeaderboardRow | null; readonly metricMode: MetricMode; readonly isSelected: boolean; readonly onDriverSelect: ((driverId: string) => void) | undefined }) {
  const identity = row.metadata?.displayName ?? row.id
  const code = row.metadata?.id ?? row.id
  const terminal = isTerminalRow(row)
  return (
    <tr className={[terminal ? 'live-leaderboard__row--terminal' : '', isSelected ? 'live-leaderboard__row--selected' : ''].filter(Boolean).join(' ') || undefined} style={teamAccentStyle(row.metadata?.colorHex)}>
      <td className="live-leaderboard__position">{formatPosition(row.position, row.status, row.isFinished)}</td>
      <td className="live-leaderboard__team-accent" aria-label={`Team colour for ${identity}`} />
      <th className="live-leaderboard__driver" scope="row" aria-label={identity} title={identity}><button type="button" aria-label={`Select ${identity}`} aria-pressed={isSelected} title={identity} onClick={() => onDriverSelect?.(row.id)}>{code}</button></th>
      <td className={`live-leaderboard__gap${row.isFinished ? ' live-leaderboard__gap--finished' : ''}`}>{formatMetric(row, ahead, metricMode)}</td>
    </tr>
  )
}

const TEAM_ACCENT_FALLBACK = '#7a8794'
const HEX_COLOR = /^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/i

function teamAccentStyle(colorHex: string | undefined): CSSProperties {
  return { '--live-leaderboard-team-color': HEX_COLOR.test(colorHex ?? '') ? colorHex : TEAM_ACCENT_FALLBACK } as CSSProperties
}

export function createLeaderboardRows(snapshot: ReplaySnapshot | null, drivers: readonly DriverMetadata[]): readonly LeaderboardRow[] {
  if (snapshot === null) return []
  const metadataById = new Map(drivers.map((driver) => [driver.id, driver]))
  const participatingIds = snapshot.leaderboardOrder ?? []
  const participatingRows = participatingIds.map((id) => createRow(id, metadataById.get(id) ?? null, snapshot))
  const remainingRows = drivers
    .filter((driver) => !participatingIds.includes(driver.id))
    .map((driver) => createRow(driver.id, driver, snapshot))
  const rows = [...participatingRows, ...remainingRows]
  return [
    ...rows.filter((row) => !isTerminalRow(row)),
    ...rows.filter((row) => isTerminalRow(row)),
  ]
}

function createRow(id: string, metadata: DriverMetadata | null, snapshot: ReplaySnapshot): LeaderboardRow {
  const sampled = snapshot.drivers[id]
  return {
    id,
    metadata,
    position: sampled?.position ?? null,
    gapToLeaderMs: sampled?.gapToLeaderMs ?? null,
    tyreCompound: sampled?.tyreCompound ?? null,
    tyreAge: sampled?.tyreAge ?? null,
    status: sampled?.status ?? null,
    isInPitLane: sampled?.isInPitLane ?? null,
    isFinished: sampled?.isFinished === true,
  }
}

function formatMetric(row: LeaderboardRow, ahead: LeaderboardRow | null, metricMode: MetricMode): ReactNode {
  if (row.isFinished) return <FinishFlag />
  if (metricMode === 'tyres') return formatTyreMetric(row.tyreCompound, row.tyreAge)
  const status = formatMetricStatus(row.status, row.isInPitLane)
  if (status !== null) return status
  return metricMode === 'leader' ? formatGap(row.position, row.gapToLeaderMs) : formatIntervalGap(row, ahead)
}

function FinishFlag() {
  return <span className="live-leaderboard__finish-flag" role="img" aria-label="Finished" />
}

const TYRE_IMAGES: Readonly<Record<TyreCompound, string>> = {
  SOFT: softTyreImage,
  MEDIUM: mediumTyreImage,
  HARD: hardTyreImage,
  INTERMEDIATE: intermediateTyreImage,
  WET: wetTyreImage,
}
const TYRE_UNAVAILABLE = 'Unavailable'

function formatTyreMetric(tyreCompound: string | null, tyreAge: number | null): ReactNode {
  const compound = tyreCompound?.trim().toUpperCase() as TyreCompound | undefined
  const image = compound === undefined ? undefined : TYRE_IMAGES[compound]
  if (compound === undefined || image === undefined || typeof tyreAge !== 'number' || !Number.isSafeInteger(tyreAge) || tyreAge < 0) {
    return <span className="live-leaderboard__tyre-unavailable" aria-label="Tyres unavailable">{TYRE_UNAVAILABLE}</span>
  }
  const label = `${compound.charAt(0)}${compound.slice(1).toLowerCase()} tyre`
  return (
    <span className="live-leaderboard__tyre" aria-label={`${label}, ${formatTyreAge(tyreAge)}`}>
      <img className="live-leaderboard__tyre-image" src={image} alt={label} />
      <span className="live-leaderboard__tyre-age">{formatTyreAge(tyreAge)}</span>
    </span>
  )
}

function formatTyreAge(age: number): string {
  return `${age} lap${age === 1 ? '' : 's'}`
}

function formatMetricStatus(status: string | null, isInPitLane: boolean | null): string | null {
  if (isTerminalStatus(status)) return 'OUT'
  if (isInPitLane === true) return 'PIT'
  const rawStatus = status?.trim()
  return rawStatus !== undefined && rawStatus !== '' && !isOnTrackStatus(rawStatus) ? rawStatus : null
}

export function formatGap(position: number | null, gapToLeaderMs: number | null, status: string | null = null): string {
  if (isTerminalStatus(status)) return '—'
  if (position === 1) return 'Leader'
  if (gapToLeaderMs === null || !Number.isFinite(gapToLeaderMs)) return '—'
  return formatGapMilliseconds(gapToLeaderMs)
}

function formatIntervalGap(row: LeaderboardRow, ahead: LeaderboardRow | null): string {
  if (isTerminalRow(row) || (ahead !== null && isTerminalRow(ahead))) return '—'
  if (row.position === 1) return 'Interval'
  if (
    row.position === null
    || row.gapToLeaderMs === null
    || ahead?.position !== row.position - 1
    || ahead.gapToLeaderMs === null
  ) return '—'
  const intervalMs = row.gapToLeaderMs - ahead.gapToLeaderMs
  return Number.isFinite(intervalMs) && intervalMs >= 0 ? formatGapMilliseconds(intervalMs) : '—'
}

function formatGapMilliseconds(gapMs: number): string { return `+${(gapMs / 1000).toFixed(3)}` }

function formatPosition(position: number | null, status: string | null, isFinished = false): string {
  if (!isFinished && isTerminalStatus(status)) return 'OUT'
  return position === null || !Number.isFinite(position) ? '—' : String(position)
}

function isTerminalRow(row: LeaderboardRow): boolean {
  return !row.isFinished && isTerminalStatus(row.status)
}

function isTerminalStatus(status: string | null): boolean {
  return normalizeStatus(status) === 'OUT'
}

function isOnTrackStatus(status: string): boolean {
  const normalized = normalizeStatus(status)
  return normalized === 'ONTRACK' || normalized === 'RUNNING'
}

function normalizeStatus(status: string | null): string {
  return status?.replace(/[\s_-]/g, '').toUpperCase() ?? ''
}
