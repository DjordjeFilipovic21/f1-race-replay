import { memo, useMemo, type CSSProperties } from 'react'
import type { DriverMetadata, PitLossModel, StintSummary } from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'
import { selectPitLossEstimate } from '../selectors/pit-loss-selectors'
import { selectPitRejoinProjection } from '../selectors/pit-rejoin-selectors'
import { selectStintData, type VisibleStint } from '../selectors/stint-selectors'
import hardTyreImage from '../../../assets/tyres/hard.png'
import intermediateTyreImage from '../../../assets/tyres/intermediate.png'
import mediumTyreImage from '../../../assets/tyres/medium.png'
import softTyreImage from '../../../assets/tyres/soft.png'
import wetTyreImage from '../../../assets/tyres/wet.png'

export interface TyreStrategyPanelProps {
  readonly drivers: readonly DriverMetadata[]
  readonly selectedDriverId: string | null
  readonly snapshot: ReplaySnapshot | null
  readonly stintSummary: StintSummary | null | undefined
  readonly pitLossModel: PitLossModel | null | undefined
  readonly totalLaps?: number | null
}

const COMPOUND_COLORS: Readonly<Record<string, string>> = Object.freeze({
  SOFT: '#ff3138',
  MEDIUM: '#f0bc53',
  HARD: '#f4f5f6',
  INTERMEDIATE: '#3dcc6b',
  WET: '#4da6e8',
})

const TYRE_IMAGES: Readonly<Record<string, string>> = Object.freeze({
  SOFT: softTyreImage,
  MEDIUM: mediumTyreImage,
  HARD: hardTyreImage,
  INTERMEDIATE: intermediateTyreImage,
  WET: wetTyreImage,
})

const COMPOUND_FALLBACK = '#7a8794'
const TEAM_ACCENT_FALLBACK = '#7a8794'
const HEX_COLOR = /^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/i
const PANEL_BACKGROUND = '#101316'
const SURFACE_MUTED = '#1a2025'
const BORDER_COLOR = 'var(--border, #35404a)'
const TEXT_MUTED = 'var(--text-muted, #aeb9c2)'

/** Renders the selected driver's stint timeline, pit-loss estimate, and projected rejoin. */
export const TyreStrategyPanel = memo(function TyreStrategyPanel({
  drivers,
  selectedDriverId,
  snapshot,
  stintSummary,
  pitLossModel,
  totalLaps,
}: TyreStrategyPanelProps) {
  // All hooks are called unconditionally before any early return so hook order
  // cannot change between renders — null-safe inputs keep selectors cheap.
  const stintSelection = useMemo(
    () => selectStintData(stintSummary, snapshot ?? 0, selectedDriverId ?? ''),
    [stintSummary, snapshot, selectedDriverId],
  )
  const pitLoss = useMemo(
    () => selectPitLossEstimate(pitLossModel, snapshot ?? 0),
    [pitLossModel, snapshot],
  )
  const rejoinProjection = useMemo(
    () => selectPitRejoinProjection(snapshot, selectedDriverId, pitLoss),
    [snapshot, selectedDriverId, pitLoss],
  )

  const driver = selectedDriverId === null
    ? null
    : drivers.find(({ id }) => id === selectedDriverId) ?? null

  if (driver === null || snapshot === null) {
    return (
      <section
        className="tyre-strategy-panel"
        style={panelStyle()}
        aria-label="Tyre strategy"
      >
        <p className="tyre-strategy-panel__empty" style={emptyStyle()} role="status">
          Tyre strategy is unavailable. Select a driver to view it.
        </p>
      </section>
    )
  }

  const resolvedTotalLaps = normalizeTotalLaps(totalLaps)
  const currentDriverLap = snapshot.drivers[driver.id]?.lap ?? null

  if (stintSelection.stints.length === 0) {
    return (
      <article
        className="tyre-strategy-panel"
        style={panelStyle()}
        aria-labelledby="tyre-strategy-title"
      >
        {renderHeader(driver)}
        <p className="tyre-strategy-panel__empty" style={emptyStyle()} role="status">
          No stint data is available yet.
        </p>
      </article>
    )
  }

  return (
    <article
      className="tyre-strategy-panel"
      style={panelStyle()}
      aria-labelledby="tyre-strategy-title"
    >
      {renderHeader(driver)}
      <div className="tyre-strategy-panel__layout" style={layoutStyle()}>
        <RaceDistanceTimeline
          stints={stintSelection.stints}
          sessionTimeMs={snapshot.sessionTimeMs}
          totalLaps={resolvedTotalLaps}
          currentLap={currentDriverLap}
        />
        <aside
          className="tyre-strategy-panel__details"
          style={detailsStyle()}
          aria-label="Pit-loss estimate and projected rejoin"
        >
          <PitLossCard estimate={pitLoss} />
          <ProjectedRejoinCard
            projection={rejoinProjection}
            drivers={drivers}
          />
        </aside>
      </div>
    </article>
  )
})

function normalizeTotalLaps(totalLaps: number | null | undefined): number | null {
  if (totalLaps === null || totalLaps === undefined) return null
  if (!Number.isInteger(totalLaps) || totalLaps < 1) return null
  return totalLaps
}

function renderHeader(driver: DriverMetadata): React.JSX.Element {
  return (
    <header className="tyre-strategy-panel__header" style={headerStyle(teamAccentValue(driver.colorHex))}>
      <span className="tyre-strategy-panel__accent" style={accentBarStyle(teamAccentValue(driver.colorHex))} aria-hidden="true" />
      <div>
        <h2 id="tyre-strategy-title" style={titleStyle()}>Strategy</h2>
      </div>
    </header>
  )
}

// --- Race-distance timeline ---

interface RaceDistanceTimelineProps {
  readonly stints: readonly VisibleStint[]
  readonly sessionTimeMs: number
  readonly totalLaps: number | null
  readonly currentLap: number | null
}

function RaceDistanceTimeline({ stints, sessionTimeMs, totalLaps, currentLap }: RaceDistanceTimelineProps) {
  const segments = buildTimelineSegments(stints, sessionTimeMs, totalLaps, currentLap)

  if (segments.length === 0) {
    return (
      <div className="tyre-strategy-panel__timeline" style={timelineStyle()} role="status" aria-label="Stint timeline">
        <p style={timelineEmptyStyle()}>No visible stints.</p>
      </div>
    )
  }

  return (
    <div
      className="tyre-strategy-panel__timeline"
      style={timelineStyle()}
      role="list"
      aria-label="Race distance timeline"
    >
      <div style={timelineBarStyle()}>
        {segments.map((segment, index) => {
          if (segment.kind === 'pit-marker') {
            return (
              <div
                key={`pit-${index}`}
                className="tyre-strategy-panel__pit-marker"
                style={inlinePitMarkerStyle()}
                role="listitem"
                aria-label={`Pit stop before stint ${segment.afterStintNumber}`}
              >
                <span style={pitMarkerIconStyle()} aria-hidden="true">▼</span>
              </div>
            )
          }
          if (segment.kind === 'empty') {
            return (
              <div
                key="remaining"
                className="tyre-strategy-panel__race-segment tyre-strategy-panel__race-segment--empty"
                style={raceSegmentEmptyStyle(segment.widthPercent)}
                role="listitem"
                aria-label={`Remaining race distance${segment.lapSpan !== null ? `, ${segment.lapSpan} laps` : ''}`}
              />
            )
          }
          const stint = segment.stint
          const color = compoundColor(stint.compound)
          const compoundLabel = formatCompound(stint.compound)
          const lapRange = formatLapRange(stint.startLap, stint.endLap)
          const freshLabel = stint.isFreshTyre === null
            ? 'Unknown freshness'
            : stint.isFreshTyre
              ? 'Fresh'
              : 'Used'
          const tyreImage = tyreImageFor(stint.compound)

          return (
            <div
              key={`stint-${stint.stintNumber}`}
              className="tyre-strategy-panel__race-segment"
              style={raceSegmentStyle(color, segment.widthPercent)}
              role="listitem"
              aria-label={`Stint ${stint.stintNumber}: ${compoundLabel}, ${lapRange}, ${freshLabel}`}
            >
              {tyreImage !== null && (
                <img
                  className="tyre-strategy-panel__tyre-image"
                  style={tyreImageStyle()}
                  src={tyreImage}
                  alt=""
                  aria-hidden="true"
                />
              )}
              <span className="tyre-strategy-panel__segment-label" style={segmentLabelStyle(color)}>
                {compoundLabel}
              </span>
              <span className="tyre-strategy-panel__segment-range" style={segmentRangeStyle()}>
                {lapRange}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

type TimelineSegment =
  | { readonly kind: 'stint'; readonly stint: VisibleStint; readonly widthPercent: number }
  | { readonly kind: 'pit-marker'; readonly afterStintNumber: number }
  | { readonly kind: 'empty'; readonly widthPercent: number; readonly lapSpan: number | null }

function buildTimelineSegments(
  stints: readonly VisibleStint[],
  sessionTimeMs: number,
  totalLaps: number | null,
  currentLap: number | null,
): readonly TimelineSegment[] {
  if (stints.length === 0) return []

  const segments: TimelineSegment[] = []
  let lastEndLap = 0

  for (let i = 0; i < stints.length; i++) {
    const stint = stints[i]
    const previousStint = i > 0 ? stints[i - 1] : null

    // Emit pit marker before this stint if the PREVIOUS stint had a causal pit transition
    if (previousStint !== null && hasCausalPitMarker(previousStint, sessionTimeMs)) {
      segments.push({ kind: 'pit-marker', afterStintNumber: stint.stintNumber })
    }

    const effectiveEndLap = stint.endLap ?? (currentLap !== null ? Math.max(stint.startLap, currentLap) : stint.startLap)
    segments.push({ kind: 'stint', stint, widthPercent: 0 })
    lastEndLap = effectiveEndLap
  }

  // Compute width percentages
  const totalStintLaps = stints.reduce((sum, stint) => {
    const effectiveEndLap = stint.endLap ?? (currentLap !== null ? Math.max(stint.startLap, currentLap) : stint.startLap)
    return sum + Math.max(1, effectiveEndLap - stint.startLap + 1)
  }, 0)

  let hasEmptySegment = false
  let emptyLapSpan: number | null = null

  if (totalLaps !== null && lastEndLap < totalLaps) {
    emptyLapSpan = totalLaps - lastEndLap
    hasEmptySegment = true
  }

  const denominator = totalLaps !== null ? totalLaps : totalStintLaps
  if (denominator <= 0) return segments

  // Assign proportional widths
  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i]
    if (segment.kind === 'stint') {
      const stint = segment.stint
      const effectiveEndLap = stint.endLap ?? (currentLap !== null ? Math.max(stint.startLap, currentLap) : stint.startLap)
      const lapSpan = Math.max(1, effectiveEndLap - stint.startLap + 1)
      segments[i] = { kind: 'stint', stint, widthPercent: (lapSpan / denominator) * 100 }
    }
  }

  if (hasEmptySegment && emptyLapSpan !== null) {
    segments.push({ kind: 'empty', widthPercent: (emptyLapSpan / denominator) * 100, lapSpan: emptyLapSpan })
  }

  return segments
}

function hasCausalPitMarker(stint: VisibleStint, sessionTimeMs: number): boolean {
  return (
    stint.pitInTimeMs !== null &&
    stint.pitInTimeMs <= sessionTimeMs &&
    stint.pitOutTimeMs !== null &&
    stint.pitOutTimeMs <= sessionTimeMs
  )
}

function tyreImageFor(compound: string | null): string | null {
  if (compound === null) return null
  const upper = compound.trim().toUpperCase()
  return TYRE_IMAGES[upper] ?? null
}

// --- Cards ---

interface PitLossCardProps {
  readonly estimate: ReturnType<typeof selectPitLossEstimate>
}

function PitLossCard({ estimate }: PitLossCardProps) {
  const lossLabel = formatPitLossMs(estimate?.estimatedLossMs ?? null)
  const sourceLabel = estimate === null
    ? 'Unavailable'
    : estimate.isBaseline
      ? 'Baseline'
      : `${estimate.observedSampleCount} sample${estimate.observedSampleCount === 1 ? '' : 's'}`

  return (
    <div className="tyre-strategy-panel__card" style={cardStyle()}>
      <p style={cardEyebrowStyle()}>Pit-loss estimate</p>
      <p style={cardValueStyle()} aria-live="polite">
        {lossLabel}
      </p>
      <p style={cardMetaStyle()} aria-label={`Pit-loss source: ${sourceLabel}`}>
        {sourceLabel}
      </p>
    </div>
  )
}

interface ProjectedRejoinCardProps {
  readonly projection: ReturnType<typeof selectPitRejoinProjection>
  readonly drivers: readonly DriverMetadata[]
}

function ProjectedRejoinCard({ projection, drivers }: ProjectedRejoinCardProps) {
  if (projection === null) {
    return (
      <div className="tyre-strategy-panel__card" style={cardStyle()}>
        <p style={cardEyebrowStyle()}>Projected rejoin</p>
        <p style={cardValueStyle()}>—</p>
        <p style={cardMetaStyle()}>Unavailable</p>
      </div>
    )
  }

  const nearestDriver = drivers.find(({ id }) => id === projection.nearestDriverId) ?? null
  const nearestLabel = nearestDriver?.id ?? projection.nearestDriverId
  const signedLabel = formatSignedGapMs(projection.signedGapVsNearestMs)

  return (
    <div className="tyre-strategy-panel__card" style={cardStyle()}>
      <p style={cardEyebrowStyle()}>Projected rejoin</p>
      <p style={cardValueStyle()} aria-live="polite">
        P{projection.projectedPosition}
      </p>
      <p style={cardMetaStyle()}>
        <span style={signedGapColorStyle(projection.signedGapVsNearestMs)}>{signedLabel}</span>
        {' vs '}
        <strong>{nearestLabel}</strong>
      </p>
      <p style={cardFootnoteStyle()}>Based on current gaps</p>
    </div>
  )
}

// --- Pure helpers ---

export function compoundColor(compound: string | null): string {
  if (compound === null) return COMPOUND_FALLBACK
  return COMPOUND_COLORS[compound.trim().toUpperCase()] ?? COMPOUND_FALLBACK
}

export function formatCompound(compound: string | null): string {
  if (compound === null) return 'Unknown'
  const upper = compound.trim().toUpperCase()
  if (upper.length === 0) return 'Unknown'
  return upper.charAt(0) + upper.slice(1).toLowerCase()
}

export function formatLapRange(startLap: number, endLap: number | null): string {
  if (endLap === null) return `Lap ${startLap}–ongoing`
  return `Lap ${startLap}–${endLap}`
}

export function formatPitLossMs(estimatedLossMs: number | null): string {
  if (estimatedLossMs === null || !Number.isFinite(estimatedLossMs)) return '—'
  const seconds = estimatedLossMs / 1000
  return `+${seconds.toFixed(3)}s`
}

export function formatSignedGapMs(gapMs: number): string {
  if (!Number.isFinite(gapMs)) return '—'
  const seconds = gapMs / 1000
  const sign = seconds > 0 ? '+' : ''
  return `${sign}${seconds.toFixed(3)}s`
}

function teamAccentValue(colorHex: string): string {
  return HEX_COLOR.test(colorHex) ? colorHex : TEAM_ACCENT_FALLBACK
}

// --- Inline style factories ---

function panelStyle(): CSSProperties {
  return {
    background: PANEL_BACKGROUND,
    border: `1px solid ${BORDER_COLOR}`,
    color: '#f4f5f6',
    minWidth: 0,
    overflow: 'hidden',
  }
}

function emptyStyle(): CSSProperties {
  return {
    color: TEXT_MUTED,
    fontSize: '.9rem',
    padding: '.75rem',
  }
}

function headerStyle(teamColor: string): CSSProperties {
  return {
    alignItems: 'stretch',
    background: 'linear-gradient(105deg, #171c20, #101316)',
    gridTemplateColumns: '5px minmax(0, 1fr)',
    gap: '.65rem',
    padding: '.7rem',
    ['--tyre-strategy-team-color' as string]: teamColor,
  }
}

function accentBarStyle(teamColor: string): CSSProperties {
  return { background: teamColor }
}

function titleStyle(): CSSProperties {
  return { fontSize: '1rem', lineHeight: 1.2, margin: 0 }
}

function layoutStyle(): CSSProperties {
  return {
    display: 'grid',
    gap: '.75rem',
    gridTemplateColumns: '1fr',
    padding: '.75rem',
  }
}

function timelineStyle(): CSSProperties {
  return {
    display: 'grid',
    gap: '.35rem',
  }
}

function timelineEmptyStyle(): CSSProperties {
  return {
    color: TEXT_MUTED,
    fontSize: '.78rem',
    padding: '.5rem',
  }
}

function timelineBarStyle(): CSSProperties {
  return {
    alignItems: 'stretch',
    display: 'flex',
    flexDirection: 'row',
    gap: '2px',
    minHeight: '4rem',
    minWidth: 0,
    borderRadius: '.2rem',
  }
}

function detailsStyle(): CSSProperties {
  return {
    display: 'grid',
    gap: '.5rem',
  }
}

function raceSegmentStyle(color: string, widthPercent: number): CSSProperties {
  return {
    alignItems: 'center',
    background: `linear-gradient(135deg, ${color}33, ${color}18)`,
    borderLeft: `3px solid ${color}`,
    display: 'flex',
    flexDirection: 'column',
    gap: '.1rem',
    justifyContent: 'center',
    minWidth: 0,
    padding: '.2rem .25rem',
    position: 'relative',
    flex: `0 1 ${widthPercent}%`,
    width: `${widthPercent}%`,
    maxWidth: `${widthPercent}%`,
  }
}

function raceSegmentEmptyStyle(widthPercent: number): CSSProperties {
  return {
    alignItems: 'center',
    background: 'repeating-linear-gradient(135deg, #1a2025, #1a2025 4px, #222930 4px, #222930 8px)',
    borderLeft: '2px dashed #3a434c',
    display: 'flex',
    justifyContent: 'center',
    minWidth: 0,
    padding: '.2rem .25rem',
    flex: `0 1 ${widthPercent}%`,
    width: `${widthPercent}%`,
    maxWidth: `${widthPercent}%`,
  }
}

function tyreImageStyle(): CSSProperties {
  return {
    display: 'block',
    height: '1rem',
    width: '1rem',
    objectFit: 'contain',
    flexShrink: 0,
  }
}

function segmentLabelStyle(color: string): CSSProperties {
  return {
    color,
    fontSize: '.6rem',
    fontWeight: 800,
    lineHeight: 1,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    maxWidth: '100%',
  }
}

function segmentRangeStyle(): CSSProperties {
  return {
    color: TEXT_MUTED,
    fontSize: '.52rem',
    fontWeight: 600,
    fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, Monaco, Consolas, monospace',
    fontVariantNumeric: 'tabular-nums',
    lineHeight: 1,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    maxWidth: '100%',
  }
}

function inlinePitMarkerStyle(): CSSProperties {
  return {
    alignItems: 'center',
    color: '#f0bc53',
    display: 'flex',
    flexShrink: 0,
    justifyContent: 'center',
    padding: '0 .15rem',
  }
}

function pitMarkerIconStyle(): CSSProperties {
  return { fontSize: '.65rem' }
}

function cardStyle(): CSSProperties {
  return {
    background: SURFACE_MUTED,
    borderLeft: '2px solid #3a434c',
    padding: '.5rem .55rem',
  }
}

function cardEyebrowStyle(): CSSProperties {
  return {
    color: TEXT_MUTED,
    fontSize: '.58rem',
    fontWeight: 800,
    letterSpacing: '.06em',
    margin: 0,
    textTransform: 'uppercase' as const,
  }
}

function cardValueStyle(): CSSProperties {
  return {
    fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, Monaco, Consolas, monospace',
    fontSize: '.95rem',
    fontVariantNumeric: 'tabular-nums',
    fontWeight: 800,
    margin: '.15rem 0 0',
  }
}

function cardMetaStyle(): CSSProperties {
  return {
    color: TEXT_MUTED,
    fontSize: '.62rem',
    fontWeight: 700,
    margin: '.2rem 0 0',
  }
}

function cardFootnoteStyle(): CSSProperties {
  return {
    color: TEXT_MUTED,
    fontSize: '.52rem',
    fontWeight: 600,
    margin: '.15rem 0 0',
    fontStyle: 'italic',
  }
}

function signedGapColorStyle(gapMs: number): CSSProperties {
  const color = gapMs > 0 ? '#ff3138' : gapMs < 0 ? '#3dcc6b' : TEXT_MUTED
  return {
    color,
    fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, Monaco, Consolas, monospace',
    fontVariantNumeric: 'tabular-nums',
    fontWeight: 800,
  }
}
