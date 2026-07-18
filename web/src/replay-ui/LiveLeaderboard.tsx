import { memo, useState } from 'react'
import type { DriverMetadata } from '../replay-data/types'
import type { ReplaySnapshot } from '../replay-engine/types'

type GapMode = 'leader' | 'interval'

export interface LiveLeaderboardProps {
  readonly snapshot: ReplaySnapshot | null
  readonly drivers: readonly DriverMetadata[]
}

interface LeaderboardRow {
  readonly id: string
  readonly metadata: DriverMetadata | null
  readonly position: number | null
  readonly gapToLeaderMs: number | null
  readonly status: string | null
  readonly isInPitLane: boolean | null
  readonly tyreCompound: string | null
}

/** Renders sampled leaderboard data without subscribing to replay state. */
export const LiveLeaderboard = memo(function LiveLeaderboard({ snapshot, drivers }: LiveLeaderboardProps) {
  const [gapMode, setGapMode] = useState<GapMode>('leader')
  const rows = createLeaderboardRows(snapshot, drivers)

  return (
    <section className="live-leaderboard" aria-labelledby="live-leaderboard-title">
      <header className="live-leaderboard__header">
        <div>
          <p className="eyebrow">Live classification</p>
          <h2 id="live-leaderboard-title">Leaderboard</h2>
        </div>
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
          <thead>
            <tr><th scope="col">Pos</th><th scope="col">Driver</th><th className="live-leaderboard__team" scope="col">Team</th><th scope="col">Status</th><th scope="col">Tyre</th><th scope="col">{gapMode === 'leader' ? 'Leader gap' : 'Interval'}</th></tr>
          </thead>
          <tbody>
            {rows.map((row, index) => <LeaderboardTableRow key={row.id} row={row} ahead={rows[index - 1] ?? null} gapMode={gapMode} />)}
          </tbody>
        </table>
      )}
    </section>
  )
})

function LeaderboardTableRow({ row, ahead, gapMode }: { readonly row: LeaderboardRow; readonly ahead: LeaderboardRow | null; readonly gapMode: GapMode }) {
  const identity = row.metadata?.displayName ?? row.id
  const code = row.metadata?.id ?? row.id
  return (
    <tr>
      <td className="live-leaderboard__position">{formatPosition(row.position, row.status)}</td>
      <th scope="row"><span className="live-leaderboard__driver-name">{identity}</span><span className="live-leaderboard__driver-code">{code}</span></th>
      <td className="live-leaderboard__team">{row.metadata?.teamName || '—'}</td>
      <td>{formatStatus(row.status, row.isInPitLane)}</td>
      <td>{row.tyreCompound ?? '—'}</td>
      <td className="live-leaderboard__gap">{gapMode === 'leader' ? formatGap(row.position, row.gapToLeaderMs, row.status) : formatIntervalGap(row, ahead)}</td>
    </tr>
  )
}

export function createLeaderboardRows(snapshot: ReplaySnapshot | null, drivers: readonly DriverMetadata[]): readonly LeaderboardRow[] {
  if (snapshot === null) return []
  const metadataById = new Map(drivers.map((driver) => [driver.id, driver]))
  const participatingIds = snapshot.leaderboardOrder ?? []
  const participatingRows = participatingIds.map((id) => createRow(id, metadataById.get(id) ?? null, snapshot))
  const remainingRows = drivers
    .filter((driver) => !participatingIds.includes(driver.id))
    .map((driver) => createRow(driver.id, driver, snapshot))
  return [...participatingRows, ...remainingRows]
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
    tyreCompound: sampled?.tyreCompound ?? null,
  }
}

export function formatGap(position: number | null, gapToLeaderMs: number | null, status: string | null = null): string {
  if (isTerminalStatus(status)) return '—'
  if (position === 1) return 'Leader'
  if (gapToLeaderMs === null || !Number.isFinite(gapToLeaderMs)) return '—'
  return formatGapMilliseconds(gapToLeaderMs)
}

function formatIntervalGap(row: LeaderboardRow, ahead: LeaderboardRow | null): string {
  if (isTerminalStatus(row.status) || isTerminalStatus(ahead?.status ?? null)) return '—'
  if (row.position === 1) return 'Leader'
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

export function formatStatus(status: string | null, isInPitLane: boolean | null): string {
  return isTerminalStatus(status) ? 'OUT' : (isInPitLane === true ? 'PIT' : (status ?? '—'))
}

function isTerminalStatus(status: string | null): boolean {
  return typeof status === 'string' && status.trim().toUpperCase() === 'OUT'
}
