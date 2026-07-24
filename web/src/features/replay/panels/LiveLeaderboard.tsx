import { memo, useState, type CSSProperties } from 'react'
import type { DriverMetadata } from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'

type GapMode = 'leader' | 'interval'

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
  readonly status: string | null
  readonly isInPitLane: boolean | null
}

/** Renders sampled leaderboard data without subscribing to replay state. */
export const LiveLeaderboard = memo(function LiveLeaderboard({ snapshot, drivers, selectedDriverId = null, onDriverSelect }: LiveLeaderboardProps) {
  const [gapMode, setGapMode] = useState<GapMode>('leader')
  const rows = createLeaderboardRows(snapshot, drivers)

  return (
    <section className="live-leaderboard" aria-label="Leaderboard">
      <header className="live-leaderboard__header">
        <div className="live-leaderboard__gap-toggle" role="group" aria-label="Gap display">
          <button type="button" aria-pressed={gapMode === 'leader'} onClick={() => setGapMode('leader')}>Leader</button>
          <button type="button" aria-pressed={gapMode === 'interval'} onClick={() => setGapMode('interval')}>Interval</button>
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
            <tr><th scope="col">Position</th><th scope="col">Team colour</th><th scope="col">Driver</th><th scope="col">{gapMode === 'leader' ? 'Leader gap' : 'Interval'}</th></tr>
          </thead>
          <tbody>
            {rows.map((row, index) => <LeaderboardTableRow key={row.id} row={row} ahead={rows[index - 1] ?? null} gapMode={gapMode} isSelected={row.id === selectedDriverId} onDriverSelect={onDriverSelect} />)}
          </tbody>
        </table>
      )}
    </section>
  )
})

function LeaderboardTableRow({ row, ahead, gapMode, isSelected, onDriverSelect }: { readonly row: LeaderboardRow; readonly ahead: LeaderboardRow | null; readonly gapMode: GapMode; readonly isSelected: boolean; readonly onDriverSelect: ((driverId: string) => void) | undefined }) {
  const identity = row.metadata?.displayName ?? row.id
  const code = row.metadata?.id ?? row.id
  const terminal = isTerminalStatus(row.status)
  return (
    <tr className={[terminal ? 'live-leaderboard__row--terminal' : '', isSelected ? 'live-leaderboard__row--selected' : ''].filter(Boolean).join(' ') || undefined} style={teamAccentStyle(row.metadata?.colorHex)}>
      <td className="live-leaderboard__position">{formatPosition(row.position, row.status)}</td>
      <td className="live-leaderboard__team-accent" aria-label={`Team colour for ${identity}`} />
      <th className="live-leaderboard__driver" scope="row" aria-label={identity} title={identity}><button type="button" aria-label={`Select ${identity}`} aria-pressed={isSelected} title={identity} onClick={() => onDriverSelect?.(row.id)}>{code}</button></th>
      <td className="live-leaderboard__gap">{formatMetric(row, ahead, gapMode)}</td>
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
    ...rows.filter((row) => !isTerminalStatus(row.status)),
    ...rows.filter((row) => isTerminalStatus(row.status)),
  ]
}

function createRow(id: string, metadata: DriverMetadata | null, snapshot: ReplaySnapshot): LeaderboardRow {
  const sampled = snapshot.drivers[id]
  return {
    id,
    metadata,
    position: sampled?.position ?? null,
    gapToLeaderMs: sampled?.gapToLeaderMs ?? null,
    status: sampled?.status ?? null,
    isInPitLane: sampled?.isInPitLane ?? null,
  }
}

function formatMetric(row: LeaderboardRow, ahead: LeaderboardRow | null, gapMode: GapMode): string {
  const status = formatMetricStatus(row.status, row.isInPitLane)
  if (status !== null) return status
  return gapMode === 'leader' ? formatGap(row.position, row.gapToLeaderMs) : formatIntervalGap(row, ahead)
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
  if (isTerminalStatus(row.status) || isTerminalStatus(ahead?.status ?? null)) return '—'
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

function formatPosition(position: number | null, status: string | null): string {
  if (isTerminalStatus(status)) return 'OUT'
  return position === null || !Number.isFinite(position) ? '—' : String(position)
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
