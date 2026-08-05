import { memo, useMemo, type CSSProperties } from 'react'
import type { DriverMetadata, PitLossEstimateSidecar, PitLossModel } from '../../../data/replay/types'
import type { ReplaySnapshot } from '../../../engine/replay/types'
import { selectPitLossEstimate } from '../selectors/pit-loss-selectors'
import { selectPitRejoinProjection } from '../selectors/pit-rejoin-selectors'
import { AfterPitComparison } from './AfterPitComparison'

export interface PitLossPositionPanelProps {
  readonly drivers: readonly DriverMetadata[]
  readonly selectedDriverId: string | null
  readonly snapshot: ReplaySnapshot | null
  readonly pitLossModel: PitLossModel | null | undefined
  readonly pitLossEstimateSidecar?: PitLossEstimateSidecar | null
}

const PANEL_BACKGROUND = '#101316'
const BORDER_COLOR = 'var(--border, #35404a)'
const TEXT_MUTED = 'var(--text-muted, #aeb9c2)'
const TEAM_ACCENT_FALLBACK = '#7a8794'
const HEX_COLOR = /^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/i

/** Renders the pit-loss estimate and after-pit comparison graph for the selected driver. */
export const PitLossPositionPanel = memo(function PitLossPositionPanel({
  drivers,
  selectedDriverId,
  snapshot,
  pitLossModel,
  pitLossEstimateSidecar,
}: PitLossPositionPanelProps) {
  const pitLoss = useMemo(
    () => snapshot === null
      ? null
      : selectPitLossEstimate(pitLossModel, snapshot, pitLossEstimateSidecar),
    [pitLossModel, pitLossEstimateSidecar, snapshot],
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
        className="pit-loss-position-panel"
        style={panelStyle()}
        aria-label="Pit loss position"
      >
        <p className="pit-loss-position-panel__empty" style={emptyStyle()} role="status">
          Pit loss position is unavailable. Select a driver to view it.
        </p>
      </section>
    )
  }

  return (
    <article
      className="pit-loss-position-panel"
      style={panelStyle()}
      aria-labelledby="pit-loss-position-title"
    >
      {renderHeader(driver)}
      <div className="pit-loss-position-panel__content" style={contentStyle()}>
        <div className="pit-loss-position-panel__summary" style={summaryRowStyle()}>
          <PitLossSummaryCell estimate={pitLoss} sidecar={pitLossEstimateSidecar} />
          <AfterPitSummaryCell projection={rejoinProjection} />
        </div>
        <div className="pit-loss-position-panel__graph" style={graphAreaStyle()}>
          <AfterPitComparison
            projection={rejoinProjection}
            drivers={drivers}
            graphOnly
          />
        </div>
      </div>
    </article>
  )
})

function renderHeader(driver: DriverMetadata): React.JSX.Element {
  return (
    <header className="pit-loss-position-panel__header" style={headerStyle(teamAccentValue(driver.colorHex))}>
      <span className="pit-loss-position-panel__accent" style={accentBarStyle(teamAccentValue(driver.colorHex))} aria-hidden="true" />
      <div>
        <h2 id="pit-loss-position-title" style={titleStyle()}>Pit loss position</h2>
      </div>
    </header>
  )
}

function teamAccentValue(colorHex: string): string {
  return HEX_COLOR.test(colorHex) ? colorHex : TEAM_ACCENT_FALLBACK
}

// --- Summary cells ---

interface PitLossSummaryCellProps {
  readonly estimate: ReturnType<typeof selectPitLossEstimate>
  readonly sidecar?: PitLossEstimateSidecar | null
}

function PitLossSummaryCell({ estimate, sidecar }: PitLossSummaryCellProps) {
  const curated = isCuratedSidecar(sidecar)
  const hasEstimate = estimate !== null
    && Number.isFinite(estimate.estimatedLossMs)
    && (curated || estimate.observedSampleCount > 0)
  const lossLabel = formatPitLossMs(hasEstimate ? estimate?.estimatedLossMs ?? null : null)
  const sampleLabel = estimate !== null && !curated && estimate.observedSampleCount > 0
    ? `${estimate.observedSampleCount} sample${estimate.observedSampleCount === 1 ? '' : 's'}`
    : 'Unavailable'

  return (
    <div className="pit-loss-position-panel__summary-cell" style={summaryCellStyle()}>
      <p style={cardEyebrowStyle()}>Pit-loss estimate</p>
      {curated ? (
        <>
          <p style={cardValueStyle()} aria-live="polite">
            {lossLabel}
          </p>
          <p style={cardMetaStyle()} aria-label={`Pit-loss status: ${curatedStatusLabel(estimate?.source)}`}>
            {curatedStatusLabel(estimate?.source)}
          </p>
        </>
      ) : (
        <>
          <p style={cardValueStyle()} aria-live="polite">
            {lossLabel}
          </p>
          <p style={cardMetaStyle()} aria-label={`Pit-loss source: ${estimate?.sourceLabel ?? 'Unavailable'}`}>
            {estimate?.sourceLabel ?? 'Unavailable'}
          </p>
          <LegacyEstimateDetails estimate={estimate} hasEstimate={hasEstimate} sampleLabel={sampleLabel} />
        </>
      )}
    </div>
  )
}

function curatedStatusLabel(source: NonNullable<ReturnType<typeof selectPitLossEstimate>>['source'] | undefined): string {
  switch (source) {
    case 'safety-car':
      return 'SC value'
    case 'virtual-safety-car':
      return 'VSC value'
    case 'race':
      return 'Green Flag value'
    default:
      return 'Unavailable'
  }
}

interface LegacyEstimateDetailsProps {
  readonly estimate: ReturnType<typeof selectPitLossEstimate>
  readonly hasEstimate: boolean
  readonly sampleLabel: string
}

function LegacyEstimateDetails({ estimate, hasEstimate, sampleLabel }: LegacyEstimateDetailsProps) {
  if (!hasEstimate) {
    return (
      <p style={cardMetaStyle()} aria-label={`Pit-loss calibration: ${sampleLabel}`}>
        {sampleLabel}
      </p>
    )
  }

  const sourceDescription = estimate?.source === 'race-fallback'
    ? 'Legacy race-derived fallback'
    : 'Legacy race-derived estimate'

  return (
    <>
      <p style={cardMetaStyle()} aria-label={`Pit-loss calibration: ${sampleLabel}`}>
        {sampleLabel}
      </p>
      <p style={cardMetaStyle()}>{sourceDescription}</p>
    </>
  )
}

function isCuratedSidecar(
  sidecar: PitLossEstimateSidecar | null | undefined,
): sidecar is Extract<PitLossEstimateSidecar, { readonly method: 'curated-track-baseline-v1' }> {
  return sidecar !== null
    && sidecar !== undefined
    && typeof sidecar === 'object'
    && sidecar.method === 'curated-track-baseline-v1'
}

interface AfterPitSummaryCellProps {
  readonly projection: ReturnType<typeof selectPitRejoinProjection>
}

function AfterPitSummaryCell({ projection }: AfterPitSummaryCellProps) {
  const positionLabel = projection === null ? '—' : `P${projection.projectedPosition}`
  const positionsLost = projection !== null && projection.currentPosition !== null
    ? projection.projectedPosition - projection.currentPosition
    : null
  const showLoss = positionsLost !== null && positionsLost > 0

  const accessibleLabel = projection === null
    ? 'After pit comparison unavailable'
    : showLoss
      ? `Projected position ${projection.projectedPosition}, loses ${positionsLost} position${positionsLost === 1 ? '' : 's'}`
      : `Projected position ${projection.projectedPosition}`

  return (
    <div className="pit-loss-position-panel__summary-cell" style={summaryCellStyle()}>
      <p style={cardEyebrowStyle()}>After pit comparison</p>
      <p style={cardValueStyle()} aria-live="polite" aria-label={accessibleLabel}>
        {positionLabel}
        {showLoss && (
          <span className="pit-loss-position-panel__loss" style={lossIndicatorStyle()} aria-hidden="true">
            {' '}↓{positionsLost}
          </span>
        )}
      </p>
      <p style={cardMetaStyle()}>
        {projection === null ? 'Unavailable' : 'Projected position'}
      </p>
    </div>
  )
}

export function formatPitLossMs(estimatedLossMs: number | null): string {
  if (estimatedLossMs === null || !Number.isFinite(estimatedLossMs)) return '—'
  const seconds = estimatedLossMs / 1000
  return `+${seconds.toFixed(3)}s`
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
    ['--pit-loss-position-team-color' as string]: teamColor,
  }
}

function accentBarStyle(teamColor: string): CSSProperties {
  return { background: teamColor }
}

function titleStyle(): CSSProperties {
  return { fontSize: '1rem', lineHeight: 1.2, margin: 0 }
}

function contentStyle(): CSSProperties {
  return {
    display: 'flex',
    flexDirection: 'column',
    gap: '.5rem',
    padding: '.75rem',
  }
}

function summaryRowStyle(): CSSProperties {
  return {
    display: 'grid',
    gap: '.5rem',
    gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
  }
}

function summaryCellStyle(): CSSProperties {
  return {
    background: '#1a2025',
    borderLeft: '2px solid #3a434c',
    padding: '.5rem .55rem',
    minWidth: 0,
  }
}

function graphAreaStyle(): CSSProperties {
  return {
    display: 'flex',
    justifyContent: 'center',
    minWidth: 0,
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

function lossIndicatorStyle(): CSSProperties {
  return {
    color: '#ff5158',
    fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, Monaco, Consolas, monospace',
    fontSize: '.85rem',
    fontWeight: 800,
    marginLeft: '.25rem',
  }
}
