import { memo, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import type { DriverMetadata, LapSectorSidecar, QualifyingLapStatusSidecar, QualifyingSummary, SessionMode } from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'
import { getSessionLabel } from '../session-capabilities'
import { selectQualifyingLiveState, selectQualifyingLiveStates, type QualifyingLiveState } from '../selectors/qualifying-live-state-selectors'
import { selectSectorColours, type ColouredSector } from '../selectors/sector-colour-selectors'
import { formatTyreMetric } from './tyre-metric'

type QualifyingMetric = 'leader' | 'lap-time' | 'tyres' | 'sectors'

export interface QualifyingClassificationPanelProps {
  readonly snapshot: ReplaySnapshot | null
  readonly drivers: readonly DriverMetadata[]
  readonly sessionMode: SessionMode
  readonly qualifyingSummary?: QualifyingSummary | null
  readonly qualifyingLapStatus?: QualifyingLapStatusSidecar | null
  readonly lapSectorSidecar?: LapSectorSidecar | null
  readonly replayEndMs?: number | null
  readonly selectedDriverId?: string | null
  readonly onDriverSelect?: (driverId: string) => void
}

export interface QualifyingRow {
  readonly id: string
  readonly metadata: DriverMetadata
  readonly liveState: QualifyingLiveState
}

/** Presents sampled qualifying state without turning historical results into live timing. */
export const QualifyingClassificationPanel = memo(function QualifyingClassificationPanel({
  snapshot,
  drivers,
  sessionMode,
  qualifyingSummary,
  qualifyingLapStatus,
  lapSectorSidecar,
  replayEndMs,
  selectedDriverId = null,
  onDriverSelect,
}: QualifyingClassificationPanelProps) {
  const [metric, setMetric] = useState<QualifyingMetric>('leader')
  const rows = useMemo(() => createQualifyingRows(drivers, snapshot, qualifyingSummary, lapSectorSidecar, qualifyingLapStatus, replayEndMs), [drivers, lapSectorSidecar, qualifyingLapStatus, qualifyingSummary, replayEndMs, snapshot])
  const leaderState = rows.find(({ liveState }) => liveState.qualifyingPosition === 1)?.liveState ?? rows[0]?.liveState ?? null
  const sectorSelections = useMemo(() => {
    if (metric !== 'sectors' || snapshot === null) return new Map<string, readonly ColouredSector[]>()
    return new Map(rows.map((row) => [
      row.id,
       selectCausalQualifyingSectors(selectSectorColours(lapSectorSidecar, snapshot, row.id, qualifyingLapStatus).sectors, row.liveState),
    ]))
  }, [lapSectorSidecar, metric, qualifyingLapStatus, rows, snapshot])
  const label = getSessionLabel(sessionMode)

  return (
    <div className="live-leaderboard">
      <header className="live-leaderboard__header">
        <div className="live-leaderboard__gap-toggle" role="group" aria-label="Qualifying metric" style={{ gridTemplateColumns: 'repeat(4, minmax(max-content, 1fr))' }}>
          {(['leader', 'lap-time', 'tyres', 'sectors'] as const).map((value) => (
            <button key={value} type="button" aria-pressed={metric === value} onClick={() => setMetric(value)}>{qualifyingMetricLabel(value)}</button>
          ))}
        </div>
      </header>
      {snapshot === null ? (
        <p className="live-leaderboard__empty" role="status">{label} classification is unavailable while replay samples load.</p>
      ) : qualifyingSummary == null ? (
        <p className="live-leaderboard__empty" role="status">{label} classification data is unavailable.</p>
      ) : rows.length === 0 ? (
        <p className="live-leaderboard__empty" role="status">No driver metadata is available for this replay.</p>
      ) : (
        <table className="live-leaderboard__table" aria-live="polite" aria-relevant="all">
          <caption>{label} classification</caption>
          <colgroup>
            <col className="live-leaderboard__column--position" />
            <col className="live-leaderboard__column--team-accent" />
            <col className="live-leaderboard__column--driver" />
            <col className="live-leaderboard__column--metric" />
          </colgroup>
          <thead>
            <tr><th scope="col">Classification</th><th scope="col">Team colour</th><th scope="col">Driver</th><th scope="col">{qualifyingMetricLabel(metric)}</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <QualifyingRowView
                key={row.id}
                row={row}
                metric={metric}
                leaderState={leaderState}
                sectors={sectorSelections.get(row.id)}
                driverCount={drivers.length}
                isParked={snapshot?.drivers[row.id]?.isInPitLane === true}
                isSelected={row.id === selectedDriverId}
                onDriverSelect={onDriverSelect}
              />
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
})

export function createQualifyingRows(
  drivers: readonly DriverMetadata[],
  snapshot: ReplaySnapshot | null,
  summary: QualifyingSummary | null | undefined,
  lapSectorSidecar: LapSectorSidecar | null | undefined,
  qualifyingLapStatus: QualifyingLapStatusSidecar | null | undefined,
  replayEndMs?: number | null,
): readonly QualifyingRow[] {
  const states = selectQualifyingLiveStates(snapshot, drivers.map(({ id }) => id), summary, lapSectorSidecar, qualifyingLapStatus, replayEndMs)
  const rows = drivers.map((metadata, index) => {
    return {
      id: metadata.id,
      metadata,
      liveState: states[index] ?? selectQualifyingLiveState(snapshot, metadata.id, summary, lapSectorSidecar, qualifyingLapStatus, replayEndMs, drivers.map(({ id }) => id)),
    }
  })
  return Object.freeze(rows.filter(({ liveState }) => liveState.isQualifyingComplete || !liveState.isOut).sort((left, right) => {
    if (left.liveState.isOut !== right.liveState.isOut) return left.liveState.isOut ? 1 : -1
    const leftPosition = left.liveState.qualifyingPosition
    const rightPosition = right.liveState.qualifyingPosition
    if (leftPosition === null && rightPosition === null) return 0
    if (leftPosition === null) return 1
    if (rightPosition === null) return -1
    return leftPosition - rightPosition
  }))
}

function QualifyingRowView({ row, metric, leaderState, sectors, driverCount, isParked, isSelected, onDriverSelect }: {
  readonly row: QualifyingRow
  readonly metric: QualifyingMetric
  readonly leaderState: QualifyingLiveState | null
  readonly sectors: readonly ColouredSector[] | undefined
  readonly driverCount: number
  readonly isParked: boolean
  readonly isSelected: boolean
  readonly onDriverSelect: ((driverId: string) => void) | undefined
}) {
  const identity = row.metadata.displayName
  const terminal = row.liveState.isOut
  const cutoffPosition = qualifyingCutoffPosition(row.liveState.activeQualifyingPhase, driverCount)
  const isCutoff = cutoffPosition !== null && row.liveState.qualifyingPosition === cutoffPosition
  const rowClassName = [
    terminal ? 'live-leaderboard__row--terminal' : '',
    isCutoff ? 'live-leaderboard__row--qualifying-cutoff' : '',
    isSelected ? 'live-leaderboard__row--selected' : '',
  ].filter(Boolean).join(' ') || undefined
  return (
    <tr className={rowClassName} data-qualifying-lap-state={row.liveState.lapPhase} style={{ '--live-leaderboard-team-color': row.metadata.colorHex } as CSSProperties}>
      <td className="live-leaderboard__position">{formatPosition(row.liveState)}</td>
      <td className="live-leaderboard__team-accent" aria-label={`Team colour for ${identity}`} />
      <th className="live-leaderboard__driver" scope="row" aria-label={identity} title={identity}>
        <button type="button" aria-label={`Select ${identity}`} aria-pressed={isSelected} title={identity} onClick={() => onDriverSelect?.(row.id)}>{row.metadata.id}</button>
        {isParked ? <ParkedIndicator /> : null}
      </th>
      <td className={`live-leaderboard__gap${metric === 'sectors' && !terminal ? ' live-leaderboard__gap--sectors' : ''}${row.liveState.isFinished ? ' live-leaderboard__gap--finished' : ''}`}>{formatQualifyingMetric(row.liveState, metric, sectors, leaderState)}</td>
    </tr>
  )
}

function qualifyingCutoffPosition(activePhase: QualifyingLiveState['activeQualifyingPhase'], driverCount: number): number | null {
  if (activePhase === 'Q1') {
    const cutoff = Math.floor(driverCount / 2) + 5
    return cutoff < driverCount ? cutoff : null
  }
  if (activePhase === 'Q2') return driverCount > 10 ? 10 : null
  return null
}

function formatQualifyingMetric(state: QualifyingLiveState, metric: QualifyingMetric, sectors: readonly ColouredSector[] | undefined, leaderState: QualifyingLiveState | null): ReactNode {
  if (state.classification === 'unavailable') return unavailableMetric('Classification unavailable')
  if (metric === 'leader') return formatLeaderMetric(state, leaderState)
  if (metric === 'lap-time') return formatQualifyingLapTime(state)
  if (state.isOut && !state.isFinished) return 'OUT'
  if (metric === 'sectors') return formatSectorCells(sectors)
  if (metric === 'tyres') return formatTyres(state)
  return formatQualifyingLapTime(state)
}

function formatLeaderMetric(state: QualifyingLiveState, leaderState: QualifyingLiveState | null): ReactNode {
  if (state.isOut && !state.isFinished) return formatQualifyingLapTime(state)
  if (state === leaderState) return formatQualifyingLapTime(state)

  const driverDurationMs = qualifyingLapDuration(state)
  const leaderDurationMs = leaderState === null ? null : qualifyingLapDuration(leaderState)
  if (driverDurationMs === null || leaderDurationMs === null) {
    return state.isFinished ? <span className="live-leaderboard__finish-text">—</span> : '—'
  }

  const gapMs = driverDurationMs - leaderDurationMs
  const gapText = Number.isFinite(gapMs) && gapMs >= 0 ? `+${(gapMs / 1000).toFixed(3)}s` : '—'
  return state.isFinished
    ? <span className="live-leaderboard__finish-text">{gapText}</span>
    : gapText
}

function formatSectorCells(sectors: readonly ColouredSector[] | undefined): ReactNode {
  const latestLap = sectors === undefined || sectors.length === 0 ? null : Math.max(...sectors.map(({ lapNumber }) => lapNumber))
  return (
    <span className="live-leaderboard__sectors" aria-label="Sector times">
      {[1, 2, 3].map((sectorNumber) => {
        const sector = latestLap === null ? null : sectors?.find((candidate) => candidate.lapNumber === latestLap && candidate.sectorNumber === sectorNumber)
        const text = sector?.durationMs == null ? '—' : (sector.durationMs / 1000).toFixed(3)
        return <span key={sectorNumber} className={`live-leaderboard__sector live-leaderboard__sector--${sector?.colour ?? 'unavailable'}`} aria-label={`S${sectorNumber} ${sector?.durationMs == null ? 'unavailable' : text}`}><span className="live-leaderboard__sector-label">S{sectorNumber}</span><span className="live-leaderboard__sector-time">{text}</span></span>
      })}
    </span>
  )
}

function selectCausalQualifyingSectors(sectors: readonly ColouredSector[], state: QualifyingLiveState): readonly ColouredSector[] {
  const causalLapNumbers = new Set(
    state.causalLapEvidence
      .filter((lap) => lap.sectors.length > 0)
      .map((lap) => lap.lapNumber),
  )
  return sectors.filter((sector) => causalLapNumbers.has(sector.lapNumber))
}

function unavailableMetric(label: string): ReactNode {
  return <span className="live-leaderboard__tyre-unavailable" aria-label={label}>Unavailable</span>
}

function ParkedIndicator() {
  return <span className="live-leaderboard__penalty-indicator live-leaderboard__parked-indicator" role="img" aria-label="Parked" title="Parked">P</span>
}

function formatPosition(state: QualifyingLiveState): string {
  const position = state.qualifyingPosition
  if (position !== null && Number.isFinite(position)) return String(position)
  if (state.isOut) return 'OUT'
  return '—'
}

function formatQualifyingLapTime(state: QualifyingLiveState): ReactNode {
  const durationMs = qualifyingLapDuration(state)
  if (durationMs === null) return <span className="live-leaderboard__qualifying-lap-time">{state.isOut ? 'OUT' : 'No Time'}</span>
  return <span className={`live-leaderboard__qualifying-lap-time${state.isFinished ? ' live-leaderboard__qualifying-lap-time--finished' : ''}`}>{formatLapDuration(durationMs)}</span>
}

function qualifyingLapDuration(state: QualifyingLiveState): number | null {
  return state.isFinished
    ? state.finishedLapDurationMs
    : state.isOut
      ? state.terminalLapDurationMs
      : state.fastestCausalLapDurationMs
}

function formatTyres(state: QualifyingLiveState): ReactNode {
  return (
    <span className="live-leaderboard__tyre-value">
      {formatTyreMetric(state.tyreCompound, state.tyreAge)}
    </span>
  )
}

function qualifyingMetricLabel(metric: QualifyingMetric): string {
  return ({ leader: 'Leader', 'lap-time': 'Lap time', tyres: 'Tyres', sectors: 'Sectors' })[metric]
}

function formatLapDuration(ms: number): string {
  const totalSeconds = ms / 1000
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = (totalSeconds - minutes * 60).toFixed(3)
  return `${minutes}:${seconds.padStart(6, '0')}`
}
