import { memo, useState, type CSSProperties, type ReactNode } from 'react'
import type { DriverMetadata, LapSectorSidecar, PenaltySidecar } from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'
import { selectDriverPenaltyStatus } from '../selectors/penalty-selectors'
import { selectSectorColours, type ColouredSector, type SectorColour } from '../selectors/sector-colour-selectors'
import hardTyreImage from '../../../assets/tyres/hard.png'
import intermediateTyreImage from '../../../assets/tyres/intermediate.png'
import mediumTyreImage from '../../../assets/tyres/medium.png'
import softTyreImage from '../../../assets/tyres/soft.png'
import wetTyreImage from '../../../assets/tyres/wet.png'

type MetricMode = 'leader' | 'interval' | 'tyres' | 'sectors'
type TyreCompound = 'SOFT' | 'MEDIUM' | 'HARD' | 'INTERMEDIATE' | 'WET'

export interface LiveLeaderboardProps {
  readonly snapshot: ReplaySnapshot | null
  readonly drivers: readonly DriverMetadata[]
  readonly selectedDriverId?: string | null
  readonly onDriverSelect?: (driverId: string) => void
  readonly lapSectorSidecar?: LapSectorSidecar | null
  readonly penaltySidecar?: PenaltySidecar
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
export const LiveLeaderboard = memo(function LiveLeaderboard({ snapshot, drivers, selectedDriverId = null, onDriverSelect, lapSectorSidecar, penaltySidecar }: LiveLeaderboardProps) {
  const [metricMode, setMetricMode] = useState<MetricMode>('leader')
  const rows = createLeaderboardRows(snapshot, drivers)
  const sectorSelections = metricMode === 'sectors' && lapSectorSidecar && snapshot
    ? createSectorSelections(rows, lapSectorSidecar, snapshot)
    : null

  return (
    <section className="live-leaderboard" aria-label="Leaderboard">
      <header className="live-leaderboard__header">
        <div className="live-leaderboard__gap-toggle" role="group" aria-label="Gap display" style={{ gridTemplateColumns: 'repeat(4, minmax(max-content, 1fr))' }}>
          <button type="button" aria-pressed={metricMode === 'leader'} onClick={() => setMetricMode('leader')}>Leader</button>
          <button type="button" aria-pressed={metricMode === 'interval'} onClick={() => setMetricMode('interval')}>Interval</button>
          <button type="button" aria-pressed={metricMode === 'tyres'} onClick={() => setMetricMode('tyres')}>Tyres</button>
          <button type="button" aria-pressed={metricMode === 'sectors'} onClick={() => setMetricMode('sectors')}>Sectors</button>
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
            <tr><th scope="col">Position</th><th scope="col">Team colour</th><th scope="col">Driver</th><th scope="col">{formatMetricHeader(metricMode)}</th></tr>
          </thead>
          <tbody>
            {rows.map((row, index) => <LeaderboardTableRow key={row.id} row={row} ahead={rows[index - 1] ?? null} metricMode={metricMode} isSelected={row.id === selectedDriverId} onDriverSelect={onDriverSelect} sectorColours={sectorSelections?.get(row.id)} snapshot={snapshot} penaltySidecar={penaltySidecar} />)}
          </tbody>
        </table>
      )}
    </section>
  )
})

function LeaderboardTableRow({ row, ahead, metricMode, isSelected, onDriverSelect, sectorColours, snapshot, penaltySidecar }: { readonly row: LeaderboardRow; readonly ahead: LeaderboardRow | null; readonly metricMode: MetricMode; readonly isSelected: boolean; readonly onDriverSelect: ((driverId: string) => void) | undefined; readonly sectorColours: readonly ColouredSector[] | undefined; readonly snapshot: ReplaySnapshot | null; readonly penaltySidecar: PenaltySidecar | undefined }) {
  const identity = row.metadata?.displayName ?? row.id
  const code = row.metadata?.id ?? row.id
  const terminal = isTerminalRow(row)
  const sectorCellsVisible = metricMode === 'sectors' && !terminal && !row.isFinished
  const hasPenalty = snapshot !== null && selectDriverPenaltyStatus(snapshot, penaltySidecar, row.id)
  return (
    <tr className={[terminal ? 'live-leaderboard__row--terminal' : '', isSelected ? 'live-leaderboard__row--selected' : ''].filter(Boolean).join(' ') || undefined} style={teamAccentStyle(row.metadata?.colorHex)}>
      <td className="live-leaderboard__position">{formatPosition(row.position, row.status, row.isFinished)}</td>
      <td className="live-leaderboard__team-accent" aria-label={`Team colour for ${identity}`} />
      <th className="live-leaderboard__driver" scope="row" aria-label={identity} title={identity}>
        <button type="button" aria-label={`Select ${identity}`} aria-pressed={isSelected} title={identity} onClick={() => onDriverSelect?.(row.id)}>{code}</button>
        {hasPenalty ? <PenaltyIndicator /> : null}
      </th>
      <td className={`live-leaderboard__gap${sectorCellsVisible ? ' live-leaderboard__gap--sectors' : ''}${row.isFinished ? ' live-leaderboard__gap--finished' : ''}`}>{formatMetric(row, ahead, metricMode, sectorColours)}</td>
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

function formatMetric(row: LeaderboardRow, ahead: LeaderboardRow | null, metricMode: MetricMode, sectorColours: readonly ColouredSector[] | undefined): ReactNode {
  if (row.isFinished) return <FinishFlag />
  if (isTerminalRow(row)) return 'OUT'
  if (metricMode === 'tyres') return formatTyreMetric(row.tyreCompound, row.tyreAge)
  if (metricMode === 'sectors') return formatSectorCells(sectorColours)
  const status = formatMetricStatus(row.status, row.isInPitLane)
  if (status !== null) return status
  return metricMode === 'leader' ? formatGap(row.position, row.gapToLeaderMs) : formatIntervalGap(row, ahead)
}

function FinishFlag() {
  return <span className="live-leaderboard__finish-flag" role="img" aria-label="Finished" />
}

function PenaltyIndicator() {
  return <span className="live-leaderboard__penalty-indicator" role="img" aria-label="Penalty issued" title="Penalty issued">!</span>
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

function formatMetricHeader(metricMode: MetricMode): string {
  if (metricMode === 'leader') return 'Leader gap'
  if (metricMode === 'interval') return 'Interval'
  if (metricMode === 'tyres') return 'Tyres'
  return 'Sectors'
}

function createSectorSelections(
  rows: readonly LeaderboardRow[],
  sidecar: LapSectorSidecar,
  snapshot: ReplaySnapshot,
): Map<string, readonly ColouredSector[]> {
  const selections = new Map<string, readonly ColouredSector[]>()
  for (const row of rows) {
    selections.set(row.id, selectSectorColours(sidecar, snapshot, row.id).sectors)
  }
  return selections
}

interface LatestLapSectors {
  readonly s1: ColouredSector | null
  readonly s2: ColouredSector | null
  readonly s3: ColouredSector | null
}

function getLatestLapSectors(sectorColours: readonly ColouredSector[] | undefined): LatestLapSectors {
  if (!sectorColours || sectorColours.length === 0) return { s1: null, s2: null, s3: null }
  const latestLap = Math.max(...sectorColours.map((sector) => sector.lapNumber))
  const lapSectors = sectorColours.filter((sector) => sector.lapNumber === latestLap)
  return {
    s1: lapSectors.find((sector) => sector.sectorNumber === 1) ?? null,
    s2: lapSectors.find((sector) => sector.sectorNumber === 2) ?? null,
    s3: lapSectors.find((sector) => sector.sectorNumber === 3) ?? null,
  }
}

function formatSectorCells(sectorColours: readonly ColouredSector[] | undefined): ReactNode {
  const latest = getLatestLapSectors(sectorColours)
  return (
    <span className="live-leaderboard__sectors" aria-label="Sector times">
      <SectorSubCell label="S1" sector={latest.s1} />
      <SectorSubCell label="S2" sector={latest.s2} />
      <SectorSubCell label="S3" sector={latest.s3} />
    </span>
  )
}

function SectorSubCell({ label, sector }: { readonly label: string; readonly sector: ColouredSector | null }) {
  const colour: SectorColour = sector?.colour ?? 'unavailable'
  const timeText = sector?.durationMs != null ? formatSectorTime(sector.durationMs) : '—'
  const ariaText = colour === 'unavailable' ? `${label} unavailable` : `${label} ${timeText}`
  return (
    <span
      className={`live-leaderboard__sector live-leaderboard__sector--${colour}`}
      aria-label={ariaText}
      title={ariaText}
    >
      <span className="live-leaderboard__sector-label">{label}</span>
      <span className="live-leaderboard__sector-time">{timeText}</span>
    </span>
  )
}

function formatSectorTime(durationMs: number): string {
  const totalSeconds = durationMs / 1000
  if (totalSeconds < 60) return totalSeconds.toFixed(3)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = (totalSeconds % 60).toFixed(3)
  return `${minutes}:${seconds.padStart(6, '0')}`
}
