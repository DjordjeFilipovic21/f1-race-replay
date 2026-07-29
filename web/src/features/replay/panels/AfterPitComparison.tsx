/**
 * After Pit Comparison — vertical live-gap visualization with top-down F1 cars.
 *
 * The car SVG symbol is adapted from Gabriele Corti's "F1 Live Gap" CodePen
 * (https://codepen.io/borntofrappe/pen/dBbYwz), used under the MIT License.
 *
 * MIT License
 *
 * Copyright (c) 2026 Gabriele Corti (https://codepen.io/borntofrappe/pen/dBbYwz)
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
import { type ReactElement } from 'react'
import type { DriverMetadata } from '../../../data/replay/types'
import type { PitRejoinProjection } from '../selectors/pit-rejoin-selectors'

export const CLOSE_REJOIN_THRESHOLD_MS = 1_000
export const GAP_THRESHOLD_MS = 3_000
const SCALE_HEIGHT = 160
const SCALE_TOP = 40
const LEFT_LANE_X = 25
const RIGHT_LANE_X = 130
const RIGHT_LABEL_X = 145
const STATUS_LABEL_X = RIGHT_LANE_X
const CENTER_X = 100
const SCALE_LABEL_X = CENTER_X - 12
const GAP_MARKER_Y = SCALE_TOP
const DEFAULT_TEAM_COLOR = '#7a8794'
const CAR_HEIGHT = 34
const GRAPH_VIEW_BOX = '0 0 200 260'

export type AheadState = 'ahead-close' | 'ahead-gap' | 'ahead-clear-air'

/**
 * Classifies the ahead-comparator state into one of three focused categories:
 * - `ahead-close`: ahead comparator within 1000 ms → show car + signed label
 * - `ahead-gap`: ahead comparator >1000 ms and <=3000 ms → red GAP marker, no CLEAR AIR, no wind
 * - `ahead-clear-air`: no ahead comparator or >3000 ms → CLEAR AIR + wind (+ delta if ahead exists)
 */
export function classifyAheadState(projection: PitRejoinProjection): AheadState {
  if (projection.aheadComparator === null) return 'ahead-clear-air'
  const absGap = Math.abs(projection.aheadComparator.signedGapMs)
  if (absGap <= CLOSE_REJOIN_THRESHOLD_MS) return 'ahead-close'
  if (absGap <= GAP_THRESHOLD_MS) return 'ahead-gap'
  return 'ahead-clear-air'
}

export function formatDistanceLabel(signedGapMs: number): string {
  if (!Number.isFinite(signedGapMs)) return '—'
  return `${Math.abs(signedGapMs)} ms`
}

export function formatNaturalLanguageGap(absGapMs: number, direction: 'ahead' | 'behind'): string {
  if (!Number.isFinite(absGapMs)) return '—'
  const seconds = absGapMs / 1000
  return `${seconds.toFixed(3)}s ${direction}`
}

export function formatSignedGapLabel(signedGapMs: number): string {
  if (!Number.isFinite(signedGapMs)) return '—'
  const seconds = signedGapMs / 1000
  const sign = seconds > 0 ? '+' : ''
  return `${sign}${seconds.toFixed(3)}s`
}

function resolveDriver(
  drivers: readonly DriverMetadata[],
  driverId: string,
): DriverMetadata | null {
  return drivers.find(({ id }) => id === driverId) ?? null
}

interface AfterPitComparisonProps {
  readonly projection: PitRejoinProjection | null
  readonly drivers: readonly DriverMetadata[]
  /** When true, renders only the SVG graph without the surrounding card, eyebrow, value, or footnote — for embedding under a shared summary row. */
  readonly graphOnly?: boolean
}

export function AfterPitComparison({ projection, drivers, graphOnly = false }: AfterPitComparisonProps): ReactElement | null {
  if (projection === null) {
    if (graphOnly) return <EmptyAfterPitComparisonGraph />
    return (
      <div className="tyre-strategy-panel__card after-pit-comparison">
        <p className="tyre-strategy-panel__eyebrow">After pit comparison</p>
        <p className="after-pit-comparison__value">—</p>
        <p className="after-pit-comparison__meta">Unavailable</p>
      </div>
    )
  }

  const aheadState = classifyAheadState(projection)
  const selected = resolveDriver(drivers, projection.selectedDriverId)
  const selectedLabel = selected?.id ?? projection.selectedDriverId
  const selectedColor = selected?.colorHex ?? DEFAULT_TEAM_COLOR

  // Ahead comparator: show car only in ahead-close state
  const showAheadCar = aheadState === 'ahead-close' && projection.aheadComparator !== null
  const ahead = showAheadCar ? projection.aheadComparator : null

  // Behind comparator: independently visible within 1000 ms in ALL ahead states
  const showBehind = projection.behindComparator !== null && Math.abs(projection.behindComparator.signedGapMs) <= CLOSE_REJOIN_THRESHOLD_MS
  const behind = showBehind ? projection.behindComparator : null

  const aheadDriver = ahead !== null ? resolveDriver(drivers, ahead.driverId) : null
  const aheadLabel = aheadDriver?.id ?? ahead?.driverId ?? null
  const aheadColor = aheadDriver?.colorHex ?? DEFAULT_TEAM_COLOR
  const statusAheadDriver = projection.aheadComparator !== null
    ? resolveDriver(drivers, projection.aheadComparator.driverId)
    : null
  const statusAheadLabel = statusAheadDriver?.id ?? projection.aheadComparator?.driverId ?? null

  const behindDriver = behind !== null ? resolveDriver(drivers, behind.driverId) : null
  const behindLabel = behindDriver?.id ?? behind?.driverId ?? null
  const behindColor = behindDriver?.colorHex ?? DEFAULT_TEAM_COLOR
  const showBehindGap = projection.behindComparator !== null && Math.abs(projection.behindComparator.signedGapMs) > CLOSE_REJOIN_THRESHOLD_MS
  const behindGapComparator = showBehindGap ? projection.behindComparator : null
  const behindGapDriver = behindGapComparator !== null ? resolveDriver(drivers, behindGapComparator.driverId) : null
  const behindGapLabel = behindGapDriver?.id ?? behindGapComparator?.driverId ?? null

  const selectedCenterY = SCALE_TOP + SCALE_HEIGHT / 2
  const selectedCarY = selectedCenterY - CAR_HEIGHT / 2

  const aheadCenterY = ahead !== null ? selectedCenterY - (Math.abs(ahead.signedGapMs) / CLOSE_REJOIN_THRESHOLD_MS) * (SCALE_HEIGHT / 2) : null
  const aheadCarY = aheadCenterY !== null ? aheadCenterY - CAR_HEIGHT / 2 : null

  const behindCenterY = behind !== null ? selectedCenterY + (Math.abs(behind.signedGapMs) / CLOSE_REJOIN_THRESHOLD_MS) * (SCALE_HEIGHT / 2) : null
  const behindCarY = behindCenterY !== null ? behindCenterY - CAR_HEIGHT / 2 : null

  // Gap marker state: red GAP label in ahead-gap
  const showGapMarker = aheadState === 'ahead-gap' && projection.aheadComparator !== null
  const gapLabel = showGapMarker ? formatSignedGapLabel(projection.aheadComparator!.signedGapMs) : null

  // Clear-air state: includes ahead-clear-air with optional delta
  const isClearAir = aheadState === 'ahead-clear-air'
  const clearAirDelta = isClearAir && projection.aheadComparator !== null
    ? formatSignedGapLabel(projection.aheadComparator.signedGapMs)
    : null

  const baseSvgDescription = buildSvgDescription(selectedLabel, aheadState, ahead, aheadLabel, statusAheadLabel, projection, behind, behindLabel, clearAirDelta)
  const svgDescription = behindGapComparator !== null && behindGapLabel !== null
    ? `${baseSvgDescription} Gap to ${behindGapLabel} behind ${formatSignedGapLabel(behindGapComparator.signedGapMs)}.`
    : baseSvgDescription

  const stageContent = (
    <div className="after-pit-comparison__stage">
      <svg
        className="after-pit-comparison__svg"
        viewBox={GRAPH_VIEW_BOX}
        role="img"
        aria-label={svgDescription}
      >
        <defs>
          <symbol id="after-pit-car" viewBox="0 0 60 100">
            <g fill="currentColor">
              <path d="M 2 4 q 28 -4 56 0 q 2 5 0 10 q -12 -2 -24 -9 h -8 q -12 7 -24 9 q -2 -5 0 -10" />
              <path d="M 27 2 q 3 -2 6 0 q 3 22.5 5 45 q -8 2 -16 0 q 2 -22.5 5 -45" />
              <g transform="translate(0 20)">
                <path d="M 2 0 q -2 10 0 20 q 7.5 1 15 0 q 2 -10 0 -20 q -7.5 -1 -15 0" />
                <path transform="translate(41 0)" d="M 2 0 q -2 10 0 20 q 7.5 1 15 0 q 2 -10 0 -20 q -7.5 -1 -15 0" />
                <path d="M 5 10 h 42" stroke="currentColor" strokeWidth="4" />
              </g>
              <path d="M 8 48 q 22 -10 44 0 q -8 30 -15 40 q -7 5 -14 0 q -7 -10 -15 -40" />
              <path d="M 26 90 l 8 0 q 4 2 7 7 q -11 2 -22 0 q 3 -5 7 -7" />
              <g transform="translate(0 80)">
                <path d="M 2 0 q -2 10 0 20 q 7.5 1 15 0 q 2 -10 0 -20 q -7.5 -1 -15 0" />
                <path transform="translate(41 0)" d="M 2 0 q -2 10 0 20 q 7.5 1 15 0 q 2 -10 0 -20 q -7.5 -1 -15 0" />
                <path d="M 5 10 h 42" stroke="currentColor" strokeWidth="4" />
              </g>
            </g>
          </symbol>
        </defs>

        <text x={CENTER_X} y="10" textAnchor="middle" className="after-pit-comparison__svg-title">Gap vs rejoin</text>
        <line x1={CENTER_X} y1={SCALE_TOP} x2={CENTER_X} y2={SCALE_TOP + SCALE_HEIGHT} className="after-pit-comparison__svg-spine" />

        <GapScale />

        {/* Selected car — always at the zero midpoint */}
        <g style={{ color: selectedColor }}>
          <use href="#after-pit-car" x={LEFT_LANE_X - 10} y={selectedCarY} width="20" height={CAR_HEIGHT} />
        </g>
        <text x={LEFT_LANE_X + 15} y={selectedCenterY} dominantBaseline="middle" className="after-pit-comparison__svg-label">
          {selectedLabel}
        </text>

        {/* Ahead comparator car — only in ahead-close state */}
        {aheadCenterY !== null && aheadCarY !== null && aheadLabel !== null && ahead !== null && (
          <>
            <g style={{ color: aheadColor }}>
              <use href="#after-pit-car" x={RIGHT_LANE_X - 10} y={aheadCarY} width="20" height={CAR_HEIGHT} />
            </g>
            <text x={RIGHT_LABEL_X} y={aheadCenterY} dominantBaseline="middle" className="after-pit-comparison__svg-label">
              {aheadLabel} {formatSignedGapLabel(ahead.signedGapMs)}
            </text>
          </>
        )}

        {/* Behind comparator car — independently visible in all ahead states */}
        {behindCenterY !== null && behindCarY !== null && behindLabel !== null && behind !== null && (
          <>
            <g style={{ color: behindColor }}>
              <use href="#after-pit-car" x={RIGHT_LANE_X - 10} y={behindCarY} width="20" height={CAR_HEIGHT} />
            </g>
            <text x={RIGHT_LABEL_X} y={behindCenterY} dominantBaseline="middle" className="after-pit-comparison__svg-label">
              {behindLabel} {formatSignedGapLabel(behind.signedGapMs)}
            </text>
          </>
        )}

        {/* Green delta marker for the closest driver behind beyond the comparison window */}
        {behindGapComparator !== null && behindGapLabel !== null && (
          <text
            x={STATUS_LABEL_X}
            y={SCALE_TOP + SCALE_HEIGHT}
            dominantBaseline="middle"
            className="after-pit-comparison__svg-behind-gap-label"
          >
            {behindGapLabel}: {formatSignedGapLabel(behindGapComparator.signedGapMs)}
          </text>
        )}

        {/* Red GAP marker — only in ahead-gap state */}
        {showGapMarker && gapLabel !== null && (
          <text
            x={STATUS_LABEL_X}
            y={GAP_MARKER_Y}
            dominantBaseline="middle"
            className="after-pit-comparison__svg-gap-label"
          >
            {statusAheadLabel}: {gapLabel}
          </text>
        )}

        {/* CLEAR AIR + optional delta + wind — only in ahead-clear-air state */}
        {isClearAir && (
          <>
            <text
              x={LEFT_LANE_X}
              y={GAP_MARKER_Y}
              textAnchor="middle"
              dominantBaseline="middle"
              className="after-pit-comparison__svg-clear-air-label"
            >
              CLEAN AIR
            </text>
            {clearAirDelta !== null && (
              <text
                x={STATUS_LABEL_X}
                y={GAP_MARKER_Y}
                dominantBaseline="middle"
                className="after-pit-comparison__svg-gap-label after-pit-comparison__svg-gap-label--clear-air"
              >
                {statusAheadLabel}: {clearAirDelta}
              </text>
            )}
            <g className="after-pit-comparison__wind" aria-hidden="true">
              <line x1="10" y1="68" x2="10" y2="92" className="after-pit-comparison__wind-streak after-pit-comparison__wind-streak--1" />
              <line x1="40" y1="102" x2="40" y2="126" className="after-pit-comparison__wind-streak after-pit-comparison__wind-streak--2" />
              <line x1="10" y1="142" x2="10" y2="166" className="after-pit-comparison__wind-streak after-pit-comparison__wind-streak--3" />
            </g>
          </>
        )}
      </svg>
    </div>
  )

  if (graphOnly) return stageContent

  if (aheadState === 'ahead-close') {
    return (
      <div className="tyre-strategy-panel__card after-pit-comparison">
        <p className="tyre-strategy-panel__eyebrow">After pit comparison</p>
        <p className="after-pit-comparison__value" aria-live="polite">P{projection.projectedPosition}</p>
        {stageContent}
        <p className="after-pit-comparison__footnote">Based on current gaps</p>
      </div>
    )
  }

  // ahead-gap and ahead-clear-air both use the clear-air card variant
  return renderClearAirCard(projection, drivers, selectedLabel, stageContent)
}

function EmptyAfterPitComparisonGraph(): ReactElement {
  return (
    <div className="after-pit-comparison__stage">
      <svg
        className="after-pit-comparison__svg"
        viewBox={GRAPH_VIEW_BOX}
        role="img"
        aria-label="After pit comparison unavailable"
      >
        <text x={CENTER_X} y="10" textAnchor="middle" className="after-pit-comparison__svg-title">Gap vs rejoin</text>
        <line x1={CENTER_X} y1={SCALE_TOP} x2={CENTER_X} y2={SCALE_TOP + SCALE_HEIGHT} className="after-pit-comparison__svg-spine" />
        <GapScale />
      </svg>
    </div>
  )
}

function GapScale(): ReactElement {
  return (
    <g className="after-pit-comparison__svg-scale">
      <line x1={CENTER_X - 6} y1={SCALE_TOP} x2={CENTER_X + 6} y2={SCALE_TOP} />
      <text x={SCALE_LABEL_X} y={SCALE_TOP} textAnchor="end" dominantBaseline="middle">-1.0s</text>
      <line x1={CENTER_X - 4} y1={SCALE_TOP + SCALE_HEIGHT * 0.25} x2={CENTER_X + 4} y2={SCALE_TOP + SCALE_HEIGHT * 0.25} />
      <text x={SCALE_LABEL_X} y={SCALE_TOP + SCALE_HEIGHT * 0.25} textAnchor="end" dominantBaseline="middle">-0.5s</text>
      <line x1={CENTER_X - 6} y1={SCALE_TOP + SCALE_HEIGHT * 0.5} x2={CENTER_X + 6} y2={SCALE_TOP + SCALE_HEIGHT * 0.5} />
      <text x={SCALE_LABEL_X} y={SCALE_TOP + SCALE_HEIGHT * 0.5} textAnchor="end" dominantBaseline="middle">0.0s</text>
      <line x1={CENTER_X - 4} y1={SCALE_TOP + SCALE_HEIGHT * 0.75} x2={CENTER_X + 4} y2={SCALE_TOP + SCALE_HEIGHT * 0.75} />
      <text x={SCALE_LABEL_X} y={SCALE_TOP + SCALE_HEIGHT * 0.75} textAnchor="end" dominantBaseline="middle">+0.5s</text>
      <line x1={CENTER_X - 6} y1={SCALE_TOP + SCALE_HEIGHT} x2={CENTER_X + 6} y2={SCALE_TOP + SCALE_HEIGHT} />
      <text x={SCALE_LABEL_X} y={SCALE_TOP + SCALE_HEIGHT} textAnchor="end" dominantBaseline="middle">+1.0s</text>
    </g>
  )
}

function buildSvgDescription(
  selectedLabel: string,
  aheadState: AheadState,
  ahead: { readonly signedGapMs: number } | null,
  aheadLabel: string | null,
  statusAheadLabel: string | null,
  projection: PitRejoinProjection,
  behind: { readonly signedGapMs: number } | null,
  behindLabel: string | null,
  clearAirDelta: string | null,
): string {
  if (aheadState === 'ahead-close') {
    const parts = [selectedLabel]
    if (ahead !== null && aheadLabel !== null) {
      parts.push(`${Math.abs(ahead.signedGapMs)} ms behind ${aheadLabel}`)
    }
    if (behind !== null && behindLabel !== null) {
      parts.push(`${Math.abs(behind.signedGapMs)} ms ahead of ${behindLabel}`)
    }
    return parts.join(', ')
  }

  if (aheadState === 'ahead-gap') {
    const aheadGapLabel = projection.aheadComparator !== null
      ? formatSignedGapLabel(projection.aheadComparator.signedGapMs)
      : ''
    const parts = [`${selectedLabel}: gap to ${statusAheadLabel ?? 'ahead car'} ${aheadGapLabel}`.trim()]
    if (behind !== null && behindLabel !== null) {
      parts.push(`${Math.abs(behind.signedGapMs)} ms ahead of ${behindLabel}`)
    }
    return parts.join(', ')
  }

  // ahead-clear-air
  const parts = clearAirDelta !== null
    ? [`Clean air around ${selectedLabel}. Gap to ${statusAheadLabel ?? 'ahead car'} ${clearAirDelta}.`]
    : [`Clean air around ${selectedLabel}. No ahead comparator.`]
  if (behind !== null && behindLabel !== null) {
    parts.push(`${Math.abs(behind.signedGapMs)} ms ahead of ${behindLabel}`)
  }
  return parts.join(' ')
}

function renderClearAirCard(
  projection: PitRejoinProjection,
  drivers: readonly DriverMetadata[],
  selectedLabel: string,
  stageContent: ReactElement,
): ReactElement {
  // Find the actually nearest comparator by absolute signed gap for the meta text
  let nearestComparator: typeof projection.aheadComparator = null
  if (projection.aheadComparator !== null && projection.behindComparator !== null) {
    const aheadAbsGap = Math.abs(projection.aheadComparator.signedGapMs)
    const behindAbsGap = Math.abs(projection.behindComparator.signedGapMs)
    if (aheadAbsGap < behindAbsGap) {
      nearestComparator = projection.aheadComparator
    } else if (behindAbsGap < aheadAbsGap) {
      nearestComparator = projection.behindComparator
    } else {
      nearestComparator = projection.aheadComparator.driverId < projection.behindComparator.driverId
        ? projection.aheadComparator
        : projection.behindComparator
    }
  } else {
    nearestComparator = projection.aheadComparator ?? projection.behindComparator
  }

  const nearestDriver = nearestComparator !== null ? resolveDriver(drivers, nearestComparator.driverId) : null
  const nearestLabel = nearestDriver?.id ?? nearestComparator?.driverId ?? null
  const nearestGap = nearestComparator !== null ? Math.abs(nearestComparator.signedGapMs) : null
  const direction = nearestComparator !== null
    ? (nearestComparator.signedGapMs > 0 ? 'behind' : nearestComparator.signedGapMs < 0 ? 'ahead' : 'level')
    : null

  const clearAirLabel = nearestLabel !== null && nearestGap !== null && direction !== null
    ? `Clean air. Projected P${projection.projectedPosition}, ${nearestGap} ms ${direction} ${nearestLabel}.`
    : `Clean air. Projected P${projection.projectedPosition}.`

  return (
    <div className="tyre-strategy-panel__card after-pit-comparison">
      <p className="tyre-strategy-panel__eyebrow">After pit comparison</p>
      <p className="after-pit-comparison__value" aria-live="polite">P{projection.projectedPosition}</p>

      {stageContent}

      {nearestLabel !== null && nearestGap !== null && direction !== null && (
        <p className="after-pit-comparison__meta" aria-label={clearAirLabel}>
          <span>{formatNaturalLanguageGap(nearestGap, direction === 'behind' ? 'behind' : 'ahead')}</span>
          {' to '}
          <strong>{nearestLabel}</strong>
        </p>
      )}
      <p className="after-pit-comparison__footnote">Based on current gaps for {selectedLabel}</p>
    </div>
  )
}
