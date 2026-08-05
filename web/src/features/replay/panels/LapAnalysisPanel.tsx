import { memo, useCallback, useMemo, useState } from 'react'
import type { DriverMetadata } from '../../../data/replay/types'
import type { LapSectorSelection, VisibleLap } from '../selectors/lap-sector-selectors'
import type { ColouredSector, SectorColour, SectorColourSelection } from '../selectors/sector-colour-selectors'

export interface LapAnalysisPanelProps {
  readonly drivers: readonly DriverMetadata[]
  readonly selectedDriverId: string | null
  readonly lapSector: LapSectorSelection
  readonly sectorColours: SectorColourSelection
}

const VB_W = 400
const VB_H = 160
const PAD = { top: 12, right: 8, bottom: 24, left: 44 }
const PLOT_W = VB_W - PAD.left - PAD.right
const PLOT_H = VB_H - PAD.top - PAD.bottom
const SECTOR_NUMBERS = [1, 2, 3] as const

const LAP_HISTORY_REGION_ID = 'lap-analysis-history'

/** Displays a causal lap-time chart and sector-by-sector breakdown for the selected driver. */
export const LapAnalysisPanel = memo(function LapAnalysisPanel({
  drivers, selectedDriverId, lapSector, sectorColours,
}: LapAnalysisPanelProps) {
  const [historyExpanded, setHistoryExpanded] = useState(false)

  const driver = selectedDriverId === null ? null : drivers.find(({ id }) => id === selectedDriverId) ?? null
  const completedLaps = useMemo(() => lapSector.laps.filter(isCompletedLap), [lapSector.laps])
  const hasCompletedLaps = completedLaps.length > 0

  const sectorMap = useMemo(() => groupSectorsByLap(sectorColours.sectors), [sectorColours.sectors])
  const bestLap = useMemo(() => findBestLap(completedLaps), [completedLaps])
  const latestLap = hasCompletedLaps ? completedLaps[completedLaps.length - 1] : null
  const toggleHistory = useCallback(() => {
    setHistoryExpanded((prev) => !prev)
  }, [])

  if (driver === null) {
    return (
      <section className="lap-analysis-panel" aria-label="Lap analysis">
        <p className="lap-analysis-panel__empty" role="status">Lap analysis is unavailable. Select a driver to view it.</p>
      </section>
    )
  }

  if (!hasCompletedLaps) {
    return (
      <article className="lap-analysis-panel" aria-labelledby="lap-analysis-title">
        <PanelHeader />
        <p className="lap-analysis-panel__empty" role="status">No completed laps yet.</p>
      </article>
    )
  }

  return (
    <article className="lap-analysis-panel" aria-labelledby="lap-analysis-title">
      <PanelHeader />
      <div className="lap-analysis-panel__layout">
        <LapTimeChart laps={completedLaps} />
        <LapSummary latestLap={latestLap!} bestLap={bestLap} />
        <button
          type="button"
          className="lap-analysis-panel__history-toggle"
          aria-expanded={historyExpanded}
          aria-controls={LAP_HISTORY_REGION_ID}
          onClick={toggleHistory}
        >
          {historyExpanded ? 'Hide lap history' : 'Show lap history'}
        </button>
        {historyExpanded && (
          <div id={LAP_HISTORY_REGION_ID} className="lap-analysis-panel__history">
            <SectorBreakdown laps={completedLaps} sectorMap={sectorMap} />
          </div>
        )}
      </div>
    </article>
  )
})

function PanelHeader() {
  return (
    <header className="lap-analysis-panel__header">
      <h2 id="lap-analysis-title">Lap analysis</h2>
    </header>
  )
}

function LapTimeChart({ laps }: { readonly laps: readonly VisibleLap[] }) {
  const chartLaps = useMemo(() => laps.filter((lap) => lap.lapDurationMs !== null), [laps])
  const geometry = useMemo(() => computeChartGeometry(chartLaps), [chartLaps])
  const bestLapNumber = useMemo(() => findBestLap(chartLaps)?.lapNumber ?? null, [chartLaps])
  if (chartLaps.length === 0) {
    return <div className="lap-analysis-panel__chart"><p className="lap-analysis-panel__chart-empty" role="status">No lap times to display.</p></div>
  }
  const latestLapNumber = chartLaps[chartLaps.length - 1].lapNumber
  return (
    <div className="lap-analysis-panel__chart">
      <svg
        className="lap-analysis-panel__chart-svg"
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        role="img"
        aria-label={`Lap time chart for ${chartLaps.length} completed laps`}
        tabIndex={0}
      >
        {geometry.yTicks.map((tick) => (
          <g key={tick.ms}>
            <line x1={PAD.left} y1={tick.y} x2={VB_W - PAD.right} y2={tick.y} className="lap-analysis-panel__chart-gridline" />
            <text x={PAD.left - 4} y={tick.y + 3} className="lap-analysis-panel__chart-label" textAnchor="end">{formatLapDuration(tick.ms)}</text>
          </g>
        ))}
        <polyline className="lap-analysis-panel__chart-line" points={geometry.points.map((p) => `${p.x},${p.y}`).join(' ')} fill="none" />
        {geometry.points.map((point) => {
          const isLatest = point.lapNumber === latestLapNumber
          const isBest = point.lapNumber === bestLapNumber
          return (
            <circle
              key={point.lapNumber}
              cx={point.x}
              cy={point.y}
              r={isLatest || isBest ? 5 : 3}
              className={['lap-analysis-panel__chart-point', isLatest ? 'lap-analysis-panel__chart-point--latest' : '', isBest ? 'lap-analysis-panel__chart-point--best' : ''].filter(Boolean).join(' ')}
            >
              <title>{`Lap ${point.lapNumber}: ${formatLapDuration(point.durationMs)}`}</title>
            </circle>
          )
        })}
        {geometry.points.filter((_, i) => shouldShowXLabel(i, geometry.points.length)).map((point) => (
          <text key={`x-${point.lapNumber}`} x={point.x} y={VB_H - 4} className="lap-analysis-panel__chart-label" textAnchor="middle">L{point.lapNumber}</text>
        ))}
      </svg>
    </div>
  )
}

function LapSummary({ latestLap, bestLap }: { readonly latestLap: VisibleLap; readonly bestLap: VisibleLap | null }) {
  return (
    <dl className="lap-analysis-panel__summary" aria-label="Lap summary">
      <div className="lap-analysis-panel__summary-item">
        <dt>Latest</dt>
        <dd className="lap-analysis-panel__summary-value">{formatLapDuration(latestLap.lapDurationMs)}</dd>
        <dd className="lap-analysis-panel__summary-label">Lap {latestLap.lapNumber}</dd>
      </div>
      {bestLap !== null && (
        <div className="lap-analysis-panel__summary-item">
          <dt>Best</dt>
          <dd className="lap-analysis-panel__summary-value lap-analysis-panel__summary-value--best">{formatLapDuration(bestLap.lapDurationMs)}</dd>
          <dd className="lap-analysis-panel__summary-label">Lap {bestLap.lapNumber}</dd>
        </div>
      )}
    </dl>
  )
}

function SectorBreakdown({ laps, sectorMap }: { readonly laps: readonly VisibleLap[]; readonly sectorMap: ReadonlyMap<number, readonly ColouredSector[]> }) {
  return (
    <table className="lap-analysis-panel__sectors" aria-label="Sector breakdown">
      <thead>
        <tr>
          <th className="lap-analysis-panel__sectors-head" scope="col">Lap</th>
          {SECTOR_NUMBERS.map((sn) => <th key={sn} className="lap-analysis-panel__sectors-head" scope="col">S{sn}</th>)}
          <th className="lap-analysis-panel__sectors-head" scope="col">Time</th>
        </tr>
      </thead>
      <tbody>
        {laps.map((lap) => {
          const sectors = sectorMap.get(lap.lapNumber) ?? []
          return (
            <tr key={lap.lapNumber} className="lap-analysis-panel__sector-row">
              <td className="lap-analysis-panel__sector-cell">{lap.lapNumber}</td>
              {SECTOR_NUMBERS.map((sn) => {
                const sector = sectors.find((s) => s.sectorNumber === sn)
                return (
                  <td key={sn} className={`lap-analysis-panel__sector-cell lap-analysis-panel__sector--${sector?.colour ?? 'unavailable'}`}>
                    {formatSectorDuration(sector?.durationMs ?? null)}
                  </td>
                )
              })}
              <td className="lap-analysis-panel__sector-cell lap-analysis-panel__sector-cell--total">{formatLapDuration(lap.lapDurationMs)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// --- Chart geometry ---

interface ChartPoint { readonly lapNumber: number; readonly durationMs: number; readonly x: number; readonly y: number }
interface ChartTick { readonly ms: number; readonly y: number }
interface ChartGeometry { readonly points: readonly ChartPoint[]; readonly yTicks: readonly ChartTick[] }

function computeChartGeometry(laps: readonly VisibleLap[]): ChartGeometry {
  if (laps.length === 0) return { points: [], yTicks: [] }
  const durations = laps.map((lap) => lap.lapDurationMs as number)
  const minMs = Math.min(...durations)
  const maxMs = Math.max(...durations)
  const range = maxMs - minMs
  const padRange = range === 0 ? 2000 : range * 1.2
  const floor = minMs - padRange * 0.1
  const ceil = maxMs + padRange * 0.1
  const points = laps.map((lap, i): ChartPoint => ({
    lapNumber: lap.lapNumber,
    durationMs: lap.lapDurationMs as number,
    x: lapToX(i, laps.length),
    y: durationToY(lap.lapDurationMs as number, floor, ceil),
  }))
  const yTicks = [floor, (floor + ceil) / 2, ceil].map((ms): ChartTick => ({ ms, y: durationToY(ms, floor, ceil) }))
  return { points, yTicks }
}

function lapToX(index: number, total: number): number {
  if (total <= 1) return PAD.left + PLOT_W / 2
  return PAD.left + (index / (total - 1)) * PLOT_W
}

function durationToY(durationMs: number, floor: number, ceil: number): number {
  if (ceil === floor) return PAD.top + PLOT_H / 2
  return PAD.top + ((durationMs - floor) / (ceil - floor)) * PLOT_H
}

function shouldShowXLabel(index: number, total: number): boolean {
  if (total <= 10) return true
  return index === 0 || index === total - 1 || (index + 1) % 5 === 0
}

// --- Pure helpers ---

function findBestLap(laps: readonly VisibleLap[]): VisibleLap | null {
  let best: VisibleLap | null = null
  for (const lap of laps) {
    if (lap.lapDurationMs === null) continue
    if (best === null || lap.lapDurationMs < (best.lapDurationMs ?? Infinity)) best = lap
  }
  return best
}

function isCompletedLap(lap: VisibleLap): boolean {
  return lap.lapDurationMs !== null && Number.isFinite(lap.lapDurationMs) && lap.lapDurationMs > 0
}

function groupSectorsByLap(sectors: readonly ColouredSector[]): ReadonlyMap<number, readonly ColouredSector[]> {
  const map = new Map<number, readonly ColouredSector[]>()
  for (const sector of sectors) {
    const existing = map.get(sector.lapNumber) ?? []
    map.set(sector.lapNumber, [...existing, sector])
  }
  return map
}

/** Formats milliseconds as M:SS.mmm, e.g. 92456 → "1:32.456". */
export function formatLapDuration(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms) || ms <= 0) return '—'
  const totalSeconds = ms / 1000
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds - minutes * 60
  return `${minutes}:${seconds.toFixed(3).padStart(6, '0')}`
}

function formatSectorDuration(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms) || ms <= 0) return '—'
  return (ms / 1000).toFixed(3)
}

/** Maps a sector colour to its semantic class suffix — exported for snapshot testing. */
export function sectorColourClass(colour: SectorColour): string {
  return `lap-analysis-panel__sector--${colour}`
}
