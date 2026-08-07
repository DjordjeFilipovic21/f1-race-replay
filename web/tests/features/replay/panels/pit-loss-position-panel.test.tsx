/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, test } from 'vitest'
import {
  PitLossPositionPanel,
  formatPitLossMs,
} from '../../../../src/features/replay/panels/PitLossPositionPanel'
import type { DriverMetadata, PitLossEstimateSidecar, PitLossEstimateTimeline, PitLossModel } from '../../../../src/data/replay/types'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'

const drivers: readonly DriverMetadata[] = [
  { id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' },
  { id: 'NOR', displayName: 'Lando Norris', teamName: 'McLaren', colorHex: '#ff8000', carNumber: '4' },
]

function createPitLossModel(overrides: {
  baselineMs?: number
  priorWeight?: number
  timeMs?: readonly number[]
  estimatedLossMs?: readonly number[]
  observedSampleCount?: readonly number[]
} = {}): PitLossModel {
  const baselineMs = overrides.baselineMs ?? 22_000
  return {
    contractVersion: 'v2',
    fixtureId: 'fixture-1',
    method: 'global-prior-weighted-mean-v1',
    baselineMs,
    priorWeight: overrides.priorWeight ?? 2,
    timeMs: overrides.timeMs ?? [0],
    estimatedLossMs: overrides.estimatedLossMs ?? [baselineMs],
    observedSampleCount: overrides.observedSampleCount ?? [1],
  }
}

function createSnapshot(sessionTimeMs: number, trackStatusCode: number | null = null): ReplaySnapshot {
  return {
    sessionTimeMs,
    leaderboardOrder: ['VER', 'NOR'],
    trackStatusCode,
    weatherState: null,
    events: [],
    drivers: {
      VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 0, lap: 10, position: 1, gear: null, drs: null, tyreCompound: 'SOFT', status: 'Running', isInPitLane: false },
      NOR: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 2_000, lap: 10, position: 2, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'Running', isInPitLane: false },
    },
  }
}

function createPitLossEstimateSidecar(overrides: {
  safetyCar?: PitLossEstimateSidecar['safetyCar']
  virtualSafetyCar?: PitLossEstimateSidecar['virtualSafetyCar']
} = {}): PitLossEstimateSidecar {
  return {
    contractVersion: 'v2',
    fixtureId: 'fixture-1',
    trackId: 'track-1',
    method: 'track-status-median-v1',
    race: { timeMs: [0, 100], estimatedLossMs: [22_000, 22_000], observedSampleCount: [0, 4] },
    ...(overrides.safetyCar === undefined ? {} : { safetyCar: overrides.safetyCar }),
    ...(overrides.virtualSafetyCar === undefined ? {} : { virtualSafetyCar: overrides.virtualSafetyCar }),
  }
}

/**
 * Australia curated baseline entry: Green 19300 ms, VSC 12300 ms, SC 9300 ms.
 * Curated status timelines are single replay-start points without
 * current-race observedSampleCount; catalog audit metadata is not part of the
 * browser payload.
 */
function createCuratedPitLossEstimateSidecar(overrides: {
  race?: PitLossEstimateTimeline
  safetyCar?: PitLossEstimateTimeline
  virtualSafetyCar?: PitLossEstimateTimeline
} = {}): PitLossEstimateSidecar {
  return {
    contractVersion: 'v2',
    fixtureId: 'fixture-1',
    trackId: 'track-1',
    method: 'curated-track-baseline-v1',
    race: overrides.race ?? { timeMs: [0], estimatedLossMs: [19_300] },
    safetyCar: overrides.safetyCar ?? { timeMs: [0], estimatedLossMs: [9_300] },
    virtualSafetyCar: overrides.virtualSafetyCar ?? { timeMs: [0], estimatedLossMs: [12_300] },
  }
}

afterEach(cleanup)

describe('PitLossPositionPanel rendering', () => {
  test('shows empty state when no driver is selected', () => {
    const snapshot = createSnapshot(0)
    render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId={null}
        snapshot={snapshot}
        pitLossModel={null}
      />,
    )

    expect(screen.getByRole('status').textContent).toContain('Pit loss position is unavailable')
  })

  test('shows empty state when snapshot is null', () => {
    render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={null}
        pitLossModel={null}
      />,
    )

    expect(screen.getByRole('status').textContent).toContain('Pit loss position is unavailable')
  })

  test('renders a compact summary row with pit-loss estimate and after pit comparison', () => {
    const snapshot = createSnapshot(120_000)
    const pitLossModel = createPitLossModel()

    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    const summary = container.querySelector('.pit-loss-position-panel__summary')
    expect(summary).not.toBeNull()
    const cells = summary!.querySelectorAll('.pit-loss-position-panel__summary-cell')
    expect(cells.length).toBe(2)
    expect(cells[0].textContent).toContain('Pit-loss estimate')
    expect(cells[0].textContent).toContain('+22.000s')
    expect(cells[0].textContent).toContain('1 sample')
    expect(cells[0].querySelector('[aria-label="Pit-loss source: Race estimate"]')).not.toBeNull()
    expect(cells[1].textContent).toContain('After pit comparison')
    expect(cells[1].textContent).toContain('P2')
  })

  test('renders pit-loss estimate before after pit comparison in the summary row', () => {
    const snapshot = createSnapshot(120_000)
    const pitLossModel = createPitLossModel()

    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    const summary = container.querySelector('.pit-loss-position-panel__summary')
    expect(summary).not.toBeNull()
    const cells = Array.from(summary!.querySelectorAll('.pit-loss-position-panel__summary-cell'))
    expect(cells.length).toBe(2)
    expect(cells[0].textContent).toContain('Pit-loss estimate')
    expect(cells[1].textContent).toContain('After pit comparison')
  })

  test('renders the after-pit comparison SVG graph below the summary row', () => {
    const snapshot = createSnapshot(120_000)
    const pitLossModel = createPitLossModel({ baselineMs: 1_500 })

    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    const content = container.querySelector('.pit-loss-position-panel__content')
    expect(content).not.toBeNull()
    const children = Array.from(content!.children)
    // First child: summary row, Second child: graph area
    expect(children.length).toBe(2)
    expect(children[0].classList.contains('pit-loss-position-panel__summary')).toBe(true)
    expect(children[1].classList.contains('pit-loss-position-panel__graph')).toBe(true)
    // The graph area contains the SVG
    const svg = children[1].querySelector('svg')
    expect(svg).not.toBeNull()
  })

  test('does not duplicate after-pit comparison headings inside the graph area', () => {
    const snapshot = createSnapshot(120_000)
    const pitLossModel = createPitLossModel()

    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    const graphArea = container.querySelector('.pit-loss-position-panel__graph')
    expect(graphArea).not.toBeNull()
    // graphOnly mode suppresses the eyebrow/heading inside AfterPitComparison
    expect(graphArea!.textContent).not.toContain('After pit comparison')
  })

  test('does not render a zero-sample pit-loss baseline', () => {
    const snapshot = createSnapshot(120_000)
    const pitLossModel = createPitLossModel({ observedSampleCount: [0] })

    render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    expect(screen.getByText('Unavailable')).toBeTruthy()
    expect(screen.getByText('—')).toBeTruthy()
  })

  test('labels pit-loss estimate with sample count when calibrated', () => {
    const snapshot = createSnapshot(120_000)
    const pitLossModel = createPitLossModel({
      timeMs: [0, 60_000, 120_000],
      estimatedLossMs: [22_000, 23_500, 24_100],
      observedSampleCount: [0, 1, 3],
    })

    render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    expect(screen.getByText('3 samples')).toBeTruthy()
    expect(screen.getByText('+24.100s')).toBeTruthy()
  })

  test('labels a legacy model-only delivery as a Race estimate', () => {
    // Arrange
    const snapshot = createSnapshot(120_000, 1)
    const pitLossModel = createPitLossModel({
      timeMs: [0, 60_000],
      estimatedLossMs: [22_000, 23_500],
      observedSampleCount: [0, 1],
    })

    // Act
    render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    // Assert
    expect(screen.getByLabelText('Pit-loss source: Race estimate')).toBeTruthy()
    expect(screen.getByText('+23.500s')).toBeTruthy()
  })

  test('displays the Safety Car estimate and provenance when Safety Car is active', () => {
    render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(120_000, 4)}
        pitLossModel={createPitLossModel()}
        pitLossEstimateSidecar={createPitLossEstimateSidecar({
          safetyCar: { timeMs: [0, 100], estimatedLossMs: [22_000, 18_500], observedSampleCount: [0, 2] },
        })}
      />,
    )

    expect(screen.getByText('+18.500s')).toBeTruthy()
    expect(screen.getByLabelText('Pit-loss source: Safety Car estimate')).toBeTruthy()
  })

  test('displays the Virtual Safety Car estimate and provenance when VSC is active', () => {
    render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(120_000, 6)}
        pitLossModel={createPitLossModel()}
        pitLossEstimateSidecar={createPitLossEstimateSidecar({
          virtualSafetyCar: { timeMs: [0, 100], estimatedLossMs: [22_000, 19_250], observedSampleCount: [0, 3] },
        })}
      />,
    )

    expect(screen.getByText('+19.250s')).toBeTruthy()
    expect(screen.getByLabelText('Pit-loss source: Virtual Safety Car estimate')).toBeTruthy()
  })

  test('falls back to the race estimate when the active status has no sample', () => {
    render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(120_000, 4)}
        pitLossModel={createPitLossModel()}
        pitLossEstimateSidecar={createPitLossEstimateSidecar({ safetyCar: { status: 'unavailable' } })}
      />,
    )

    expect(screen.getByText('+22.000s')).toBeTruthy()
    expect(screen.getByLabelText('Pit-loss source: Race estimate (fallback)')).toBeTruthy()
  })

  test('labels the race estimate for normal status and falls back for unknown status', () => {
    const sidecar = createPitLossEstimateSidecar()
    const { rerender } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(120_000, 1)}
        pitLossModel={createPitLossModel()}
        pitLossEstimateSidecar={sidecar}
      />,
    )

    expect(screen.getByLabelText('Pit-loss source: Race estimate')).toBeTruthy()

    rerender(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(120_000, 99)}
        pitLossModel={createPitLossModel()}
        pitLossEstimateSidecar={sidecar}
      />,
    )

    expect(screen.getByLabelText('Pit-loss source: Race estimate (fallback)')).toBeTruthy()
  })

  test('pit-loss estimate respects causal boundary (only uses samples at or before session cursor)', () => {
    const snapshot = createSnapshot(90_000)
    const pitLossModel = createPitLossModel({
      timeMs: [0, 60_000, 120_000],
      estimatedLossMs: [22_000, 23_500, 24_100],
      observedSampleCount: [0, 1, 3],
    })

    render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    // At sessionTimeMs=90000, only the 60000 sample is causal
    expect(screen.getByText('1 sample')).toBeTruthy()
    expect(screen.getByText('+23.500s')).toBeTruthy()
  })

  test('shows unavailable state in both summary cells when pit-loss model is null', () => {
    const snapshot = createSnapshot(120_000)

    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={null}
      />,
    )

    const summary = container.querySelector('.pit-loss-position-panel__summary')
    expect(summary).not.toBeNull()
    const cells = Array.from(summary!.querySelectorAll('.pit-loss-position-panel__summary-cell'))
    expect(cells[0].textContent).toContain('—')
    expect(cells[0].textContent).toContain('Unavailable')
    expect(cells[1].textContent).toContain('—')
    expect(cells[1].textContent).toContain('Unavailable')
  })

  test('keeps an empty SVG graph when projection is null', () => {
    const snapshot = createSnapshot(120_000)

    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={null}
      />,
    )

    const graphArea = container.querySelector('.pit-loss-position-panel__graph')
    expect(graphArea).not.toBeNull()
    const svg = graphArea!.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg!.querySelectorAll('use').length).toBe(0)
    expect(svg!.getAttribute('aria-label')).toBe('After pit comparison unavailable')
  })

  test('renders SVG graph for close comparison when gap is within threshold', () => {
    const snapshot = createSnapshot(120_000)
    // 2ms pit loss → projected gap = 0 + 2 = 2ms, NOR at 2000ms → signedGap = 2 - 2000 = -1998 (clear air)
    // Use a pit loss that puts us within 1000ms of NOR
    const pitLossModel = createPitLossModel({ estimatedLossMs: [2_500] })

    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    const graphArea = container.querySelector('.pit-loss-position-panel__graph')
    expect(graphArea).not.toBeNull()
    const svg = graphArea!.querySelector('svg')
    expect(svg).not.toBeNull()
    // Should show VER and NOR in the SVG
    const svgTexts = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    expect(svgTexts).toContain('VER')
    expect(svgTexts).toContain('NOR')
  })

  test('renders clear-air state in the graph area when gap exceeds threshold', () => {
    const snapshot = createSnapshot(120_000)
    const pitLossModel = createPitLossModel()

    render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    // VER at 0ms + 22s = 22s, NOR at 2s → signedGap = 22000 - 2000 = 20000ms → clear air
    expect(screen.getByText('CLEAN AIR')).toBeTruthy()
  })

  test('applies team accent to the header', () => {
    const snapshot = createSnapshot(120_000)
    const pitLossModel = createPitLossModel()

    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    const header = container.querySelector('.pit-loss-position-panel__header')
    expect(header).not.toBeNull()
    // VER's team color is #3671c6
    const headerStyle = (header as HTMLElement).style
    expect(headerStyle.getPropertyValue('--pit-loss-position-team-color')).toBe('#3671c6')
  })

  test('updates header team accent when selected driver changes', () => {
    const snapshot = createSnapshot(120_000)
    const pitLossModel = createPitLossModel()

    const { container, rerender } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    let header = container.querySelector('.pit-loss-position-panel__header') as HTMLElement
    expect(header.style.getPropertyValue('--pit-loss-position-team-color')).toBe('#3671c6')

    rerender(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="NOR"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    header = container.querySelector('.pit-loss-position-panel__header') as HTMLElement
    expect(header.style.getPropertyValue('--pit-loss-position-team-color')).toBe('#ff8000')
  })

  test('falls back to raw comparator driver ID when driver metadata is missing', () => {
    const snapshot = createSnapshot(120_000)
    const pitLossModel = createPitLossModel({ estimatedLossMs: [2_000] })

    // Only provide metadata for VER — NOR (a comparator) has no metadata
    const partialDrivers: readonly DriverMetadata[] = [
      { id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' },
    ]

    const { container } = render(
      <PitLossPositionPanel
        drivers={partialDrivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    // The graph area should still show NOR by its ID fallback in SVG
    const graphArea = container.querySelector('.pit-loss-position-panel__graph')
    expect(graphArea).not.toBeNull()
    const svg = graphArea!.querySelector('svg')
    if (svg !== null) {
      expect(svg.textContent).toContain('NOR')
    }
  })

  test('shows position loss indicator when projected position is worse than current', () => {
    // VER: position=1, gap=0, pit loss=22s → projected gap=22000, projected position=2
    // NOR: position=2, gap=2000 → behind NOR (signedGap = 22000-2000 = 20000)
    // Loss = 2 - 1 = 1
    const snapshot: ReplaySnapshot = {
      sessionTimeMs: 120_000,
      leaderboardOrder: ['VER', 'NOR'],
      trackStatusCode: null,
      weatherState: null,
      events: [],
      drivers: {
        VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 0, lap: 10, position: 1, gear: null, drs: null, tyreCompound: 'SOFT', status: 'Running', isInPitLane: false },
        NOR: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 2_000, lap: 10, position: 2, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'Running', isInPitLane: false },
      },
    }
    const pitLossModel = createPitLossModel()

    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    const summaryCells = container.querySelectorAll('.pit-loss-position-panel__summary-cell')
    const afterPitCell = summaryCells[1]
    expect(afterPitCell).toBeTruthy()
    expect(afterPitCell.textContent).toContain('P2')
    expect(afterPitCell.textContent).toContain('↓1')
    // Loss indicator has red color styling
    const lossIndicator = afterPitCell.querySelector('.pit-loss-position-panel__loss')
    expect(lossIndicator).not.toBeNull()
    expect((lossIndicator as HTMLElement).style.color).toBe('rgb(255, 81, 88)')
  })

  test('position loss indicator has accessible description', () => {
    const snapshot: ReplaySnapshot = {
      sessionTimeMs: 120_000,
      leaderboardOrder: ['VER', 'NOR'],
      trackStatusCode: null,
      weatherState: null,
      events: [],
      drivers: {
        VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 0, lap: 10, position: 1, gear: null, drs: null, tyreCompound: 'SOFT', status: 'Running', isInPitLane: false },
        NOR: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 2_000, lap: 10, position: 2, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'Running', isInPitLane: false },
      },
    }
    const pitLossModel = createPitLossModel()

    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    const summaryCells = container.querySelectorAll('.pit-loss-position-panel__summary-cell')
    const afterPitValue = summaryCells[1].querySelector('[aria-live="polite"]')
    expect(afterPitValue).not.toBeNull()
    const accessibleLabel = afterPitValue!.getAttribute('aria-label') ?? ''
    expect(accessibleLabel).toContain('Projected position 2')
    expect(accessibleLabel).toContain('loses 1 position')
  })

  test('hides position loss indicator when no positions are lost', () => {
    // VER: position=2, gap=20000, pit loss=3s → projected gap=23000, projected position=2
    // NOR: position=1, gap=0 → ahead (signedGap = 23000-0 = 23000)
    // Loss = 2 - 2 = 0 → no indicator
    const snapshot: ReplaySnapshot = {
      sessionTimeMs: 120_000,
      leaderboardOrder: ['NOR', 'VER'],
      trackStatusCode: null,
      weatherState: null,
      events: [],
      drivers: {
        NOR: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 0, lap: 10, position: 1, gear: null, drs: null, tyreCompound: 'SOFT', status: 'Running', isInPitLane: false },
        VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 20_000, lap: 10, position: 2, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'Running', isInPitLane: false },
      },
    }
    const pitLossModel = createPitLossModel({ estimatedLossMs: [3_000] })

    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    const summaryCells = container.querySelectorAll('.pit-loss-position-panel__summary-cell')
    const afterPitCell = summaryCells[1]
    expect(afterPitCell).toBeTruthy()
    expect(afterPitCell.textContent).toContain('P2')
    expect(afterPitCell.textContent).not.toContain('↓')
    expect(afterPitCell.querySelector('.pit-loss-position-panel__loss')).toBeNull()
  })

  test('hides position loss indicator when current position is unavailable', () => {
    // VER: position=null → current position unavailable → no indicator
    const snapshot: ReplaySnapshot = {
      sessionTimeMs: 120_000,
      leaderboardOrder: ['VER', 'NOR'],
      trackStatusCode: null,
      weatherState: null,
      events: [],
      drivers: {
        VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 0, lap: 10, position: null, gear: null, drs: null, tyreCompound: 'SOFT', status: 'Running', isInPitLane: false },
        NOR: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 2_000, lap: 10, position: 2, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'Running', isInPitLane: false },
      },
    }
    const pitLossModel = createPitLossModel()

    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        pitLossModel={pitLossModel}
      />,
    )

    const summaryCells = container.querySelectorAll('.pit-loss-position-panel__summary-cell')
    const afterPitCell = summaryCells[1]
    expect(afterPitCell).toBeTruthy()
    expect(afterPitCell.textContent).not.toContain('↓')
    expect(afterPitCell.querySelector('.pit-loss-position-panel__loss')).toBeNull()
  })

  test('labels legacy sidecar observations as current-race samples, never catalog evidence', () => {
    // Arrange — legacy sidecar race timeline carries current-race observations
    const sidecar = createPitLossEstimateSidecar()

    // Act
    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(120_000, 1)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar}
      />,
    )

    // Assert — scoped to the pit-loss summary cell; current-race counts are
    // labelled as samples, never as catalog calibration metadata
    const cells = container.querySelectorAll('.pit-loss-position-panel__summary-cell')
    const lossCell = cells[0]
    expect(lossCell.textContent).toContain('4 samples')
    expect(lossCell.textContent).not.toContain('Catalog evidence')
    expect(lossCell.textContent).not.toContain('calibration')
    expect(lossCell.textContent).not.toContain('Curated track data')
    expect(screen.queryByText(/Baseline/)).toBeNull()
  })
})

describe('PitLossPositionPanel curated baseline rendering', () => {
  test('renders only the selected Green Flag value at replay start', () => {
    // Arrange — curated values must be available from replay start without a legacy model
    const sidecar = createCuratedPitLossEstimateSidecar()

    // Act
    render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(0, 1)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar}
      />,
    )

    // Assert — only the selected status label and value are visible.
    expect(screen.getByText('+19.300s')).toBeTruthy()
    expect(screen.getByText('Green Flag value')).toBeTruthy()
    expect(screen.queryByText(/Catalog|Source|Captured|Evidence|Confidence|calibration/i)).toBeNull()
    expect(screen.queryByText(/Baseline/)).toBeNull()
  })

  test('renders only the selected SC value', () => {
    // Arrange
    const sidecar = createCuratedPitLossEstimateSidecar()

    // Act
    render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(0, 4)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar}
      />,
    )

    // Assert
    expect(screen.getByText('+9.300s')).toBeTruthy()
    expect(screen.getByText('SC value')).toBeTruthy()
    expect(screen.queryByText(/Baseline/)).toBeNull()
  })

  test.each([6, 7])('renders curated Virtual Safety Car catalog value for status code %s', (statusCode) => {
    // Arrange
    const sidecar = createCuratedPitLossEstimateSidecar()

    // Act
    render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(0, statusCode)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar}
      />,
    )

    // Assert — both VSC and VSC-ending resolve to the VSC catalog value
    expect(screen.getByText('+12.300s')).toBeTruthy()
    expect(screen.getByText('VSC value')).toBeTruthy()
    expect(screen.queryByText(/Baseline/)).toBeNull()
  })

  test('switches the catalog value as the track status changes', () => {
    // Arrange
    const sidecar = createCuratedPitLossEstimateSidecar()
    const { rerender } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(0, 1)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar}
      />,
    )

    // Act — Green → Safety Car → VSC ending → Green
    expect(screen.getByText('+19.300s')).toBeTruthy()

    rerender(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(0, 4)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar}
      />,
    )
    expect(screen.getByText('+9.300s')).toBeTruthy()

    rerender(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(0, 7)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar}
      />,
    )
    expect(screen.getByText('+12.300s')).toBeTruthy()

    rerender(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(0, 1)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar}
      />,
    )

    // Assert — returns to the Green value
    expect(screen.getByText('+19.300s')).toBeTruthy()
  })

  test('fails closed for unknown status with a curated sidecar and no legacy model', () => {
    // Arrange
    const sidecar = createCuratedPitLossEstimateSidecar()
    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(120_000, 99)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar}
      />,
    )

    // Act & Assert — unknown status must not silently show the old 22000 ms value
    const summary = container.querySelector('.pit-loss-position-panel__summary')
    expect(summary).not.toBeNull()
    const cells = Array.from(summary!.querySelectorAll('.pit-loss-position-panel__summary-cell'))
    expect(cells[0].textContent).toContain('—')
    expect(cells[0].textContent).toContain('Unavailable')
    expect(screen.queryByText('+22.000s')).toBeNull()
    expect(screen.queryByText(/Baseline/)).toBeNull()
  })

  test('fails closed for unknown status even when the legacy model has no samples', () => {
    // Arrange — the legacy model carries only a zero-sample baseline
    const sidecar = createCuratedPitLossEstimateSidecar()
    const pitLossModel = createPitLossModel({ observedSampleCount: [0] })
    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(120_000, 99)}
        pitLossModel={pitLossModel}
        pitLossEstimateSidecar={sidecar}
      />,
    )

    // Act & Assert — no silent 22000 ms and no Baseline label
    const summary = container.querySelector('.pit-loss-position-panel__summary')
    expect(summary).not.toBeNull()
    const cells = Array.from(summary!.querySelectorAll('.pit-loss-position-panel__summary-cell'))
    expect(cells[0].textContent).toContain('—')
    expect(cells[0].textContent).toContain('Unavailable')
    expect(screen.queryByText('+22.000s')).toBeNull()
    expect(screen.queryByText(/Baseline/)).toBeNull()
  })

  test('never renders catalog-only source status metadata', () => {
    // Arrange — a payload that smuggles catalog-only sourceStatus into the
    // sidecar and its timelines (cast through unknown like other malformed
    // fixtures) must not surface in the browser DOM
    const sidecar = createCuratedPitLossEstimateSidecar() as unknown as Record<string, unknown>
    sidecar.sourceStatus = 'official'
    ;(sidecar.race as Record<string, unknown>).sourceStatus = 'measured'
    ;(sidecar.safetyCar as Record<string, unknown>).sourceStatus = 'proxy'

    // Act
    const { container } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(0, 4)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar as unknown as PitLossEstimateSidecar}
      />,
    )

    // Assert — neither the field name nor its values reach the panel, and no
    // "Source status" metadata row exists
    expect(screen.queryByText(/sourceStatus/i)).toBeNull()
    expect(document.body.textContent).not.toContain('sourceStatus')
    expect(document.body.textContent).not.toContain('measured')
    expect(document.body.textContent).not.toContain('proxy')
    expect(container.querySelector('.pit-loss-position-panel__metadata')).toBeNull()
    // The selected value itself still renders from the known fields
    expect(screen.getByText('+9.300s')).toBeTruthy()
    expect(screen.getByText('SC value')).toBeTruthy()
  })

  test('keeps curated catalog values across a later cursor and switches by status', () => {
    // Arrange — curated values are static replay-start catalog data
    const sidecar = createCuratedPitLossEstimateSidecar()
    const { rerender } = render(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(600_000, 1)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar}
      />,
    )

    // Act & Assert — Green remains available at a later cursor
    expect(screen.getByText('+19.300s')).toBeTruthy()

    // Safety Car at the same later cursor
    rerender(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(600_000, 4)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar}
      />,
    )
    expect(screen.getByText('+9.300s')).toBeTruthy()

    // VSC ending at the same later cursor
    rerender(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(600_000, 7)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar}
      />,
    )
    expect(screen.getByText('+12.300s')).toBeTruthy()

    // Back to Green
    rerender(
      <PitLossPositionPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={createSnapshot(600_000, 1)}
        pitLossModel={null}
        pitLossEstimateSidecar={sidecar}
      />,
    )
    expect(screen.getByText('+19.300s')).toBeTruthy()
    expect(screen.queryByText(/Baseline/)).toBeNull()
  })

})

describe('formatPitLossMs', () => {
  test('formats pit loss with 3 decimal places', () => {
    expect(formatPitLossMs(null)).toBe('—')
    expect(formatPitLossMs(22_000)).toBe('+22.000s')
    expect(formatPitLossMs(23_456)).toBe('+23.456s')
  })
})
