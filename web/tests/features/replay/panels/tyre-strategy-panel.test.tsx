/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, test } from 'vitest'
import {
  TyreStrategyPanel,
  compoundColor,
  formatCompound,
  formatLapRange,
  formatSignedGapMs,
} from '../../../../src/features/replay/panels/TyreStrategyPanel'
import type { DriverMetadata, StintSummary } from '../../../../src/data/replay/types'
import type { ReplaySnapshot } from '../../../../src/engine/replay/types'

const drivers: readonly DriverMetadata[] = [
  { id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' },
  { id: 'NOR', displayName: 'Lando Norris', teamName: 'McLaren', colorHex: '#ff8000', carNumber: '4' },
]

function createStintSummary(driverStints: Record<string, {
  stintNumber: readonly number[]
  compound: readonly (string | null)[]
  startLap: readonly number[]
  endLap: readonly (number | null)[]
  startTimeMs: readonly (number | null)[]
  endTimeMs: readonly (number | null)[]
  tyreLifeAtStart: readonly (number | null)[]
  isFreshTyre: readonly (boolean | null)[]
  pitInTimeMs: readonly (number | null)[]
  pitOutTimeMs: readonly (number | null)[]
}>): StintSummary {
  return {
    contractVersion: 'v1',
    fixtureId: 'fixture-1',
    drivers: driverStints,
  }
}

function createSnapshot(sessionTimeMs: number): ReplaySnapshot {
  return {
    sessionTimeMs,
    leaderboardOrder: ['VER', 'NOR'],
    trackStatusCode: null,
    weatherState: null,
    events: [],
    drivers: {
      VER: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 0, lap: 10, position: 1, gear: null, drs: null, tyreCompound: 'SOFT', status: 'Running', isInPitLane: false },
      NOR: { x: null, y: null, trackDistanceMeters: null, speed: null, throttle: null, brake: null, gapToLeaderMs: 2_000, lap: 10, position: 2, gear: null, drs: null, tyreCompound: 'MEDIUM', status: 'Running', isInPitLane: false },
    },
  }
}

afterEach(cleanup)

describe('TyreStrategyPanel rendering', () => {
  test('shows empty state when no driver is selected', () => {
    const snapshot = createSnapshot(0)
    render(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId={null}
        snapshot={snapshot}
        stintSummary={null}
      />,
    )

    expect(screen.getByRole('status').textContent).toContain('Tyre strategy is unavailable')
  })

  test('shows empty state when snapshot is null', () => {
    render(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={null}
        stintSummary={null}
      />,
    )

    expect(screen.getByRole('status').textContent).toContain('Tyre strategy is unavailable')
  })

  test('shows a no-stints message when driver is selected but no stint data exists', () => {
    const snapshot = createSnapshot(0)
    const stintSummary = createStintSummary({})
    render(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
      />,
    )

    expect(screen.getByRole('status').textContent).toContain('No stint data is available yet')
    expect(screen.getByRole('heading', { name: 'Strategy' })).toBeTruthy()
    expect(screen.queryByText('Max Verstappen')).toBeNull()
    expect(screen.queryByText('#1')).toBeNull()
  })

  test('renders race-distance timeline with compound labels and lap ranges', () => {
    const snapshot = createSnapshot(120_000)
    const stintSummary = createStintSummary({
      VER: {
        stintNumber: [1, 2],
        compound: ['SOFT', 'MEDIUM'],
        startLap: [1, 15],
        endLap: [14, null],
        startTimeMs: [0, 60_000],
        endTimeMs: [55_000, null],
        tyreLifeAtStart: [0, 0],
        isFreshTyre: [true, true],
        pitInTimeMs: [52_000, null],
        pitOutTimeMs: [58_000, null],
      },
    })

    render(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Strategy' })).toBeTruthy()
    expect(screen.queryByText('Max Verstappen')).toBeNull()
    expect(screen.queryByText('#1')).toBeNull()

    // Compound labels appear in segments
    expect(screen.getAllByText('Soft').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Medium').length).toBeGreaterThanOrEqual(1)
    // Lap ranges appear in segments
    expect(screen.getByText('Lap 1–14')).toBeTruthy()
    expect(screen.getByText('Lap 15–ongoing')).toBeTruthy()
  })

  test('timeline segments include accessible fresh/used labels', () => {
    const snapshot = createSnapshot(120_000)
    const stintSummary = createStintSummary({
      VER: {
        stintNumber: [1],
        compound: ['SOFT'],
        startLap: [1],
        endLap: [null],
        startTimeMs: [0],
        endTimeMs: [null],
        tyreLifeAtStart: [0],
        isFreshTyre: [true],
        pitInTimeMs: [null],
        pitOutTimeMs: [null],
      },
    })

    render(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
      />,
    )

    const segment = screen.getByRole('listitem', { name: /Stint 1.*Fresh/i })
    expect(segment).toBeTruthy()
  })

  test('never reveals stints whose startTimeMs is beyond the session cursor', () => {
    const snapshot = createSnapshot(30_000)
    const stintSummary = createStintSummary({
      VER: {
        stintNumber: [1, 2],
        compound: ['SOFT', 'MEDIUM'],
        startLap: [1, 15],
        endLap: [14, null],
        startTimeMs: [0, 60_000],
        endTimeMs: [55_000, null],
        tyreLifeAtStart: [0, 0],
        isFreshTyre: [true, true],
        pitInTimeMs: [null, null],
        pitOutTimeMs: [null, null],
      },
    })

    render(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
      />,
    )

    expect(screen.getAllByText('Soft').length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText('Medium')).toBeNull()
    expect(screen.queryByText('Lap 15–ongoing')).toBeNull()
  })

  test('shows causal pit marker only when both pit-in and pit-out are within the causal boundary', () => {
    const snapshot = createSnapshot(56_000)
    const stintSummary = createStintSummary({
      VER: {
        stintNumber: [1, 2],
        compound: ['SOFT', 'MEDIUM'],
        startLap: [1, 15],
        endLap: [14, null],
        startTimeMs: [0, 60_000],
        endTimeMs: [55_000, null],
        tyreLifeAtStart: [0, 0],
        isFreshTyre: [true, true],
        pitInTimeMs: [52_000, null],
        pitOutTimeMs: [58_000, null],
      },
    })

    const { rerender } = render(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
      />,
    )

    // At sessionTimeMs=56000: pitIn=52000 (causal), pitOut=58000 (NOT causal)
    expect(screen.queryByLabelText(/Pit stop before stint 2/)).toBeNull()

    // Advance past pit-out AND past the second stint start so both stints are visible
    const advancedSnapshot = createSnapshot(65_000)
    rerender(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={advancedSnapshot}
        stintSummary={stintSummary}
      />,
    )

    expect(screen.getByLabelText(/Pit stop before stint 2/)).toBeTruthy()
  })

  test('renders empty segment when totalLaps is provided and exceeds visible stint laps', () => {
    const snapshot = createSnapshot(120_000)
    const stintSummary = createStintSummary({
      VER: {
        stintNumber: [1],
        compound: ['SOFT'],
        startLap: [1],
        endLap: [null],
        startTimeMs: [0],
        endTimeMs: [null],
        tyreLifeAtStart: [0],
        isFreshTyre: [true],
        pitInTimeMs: [null],
        pitOutTimeMs: [null],
      },
    })

    render(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
        totalLaps={57}
      />,
    )

    expect(screen.getByLabelText(/Remaining race distance/)).toBeTruthy()
  })

  test('does not render empty segment when totalLaps is less than or equal to visible end lap', () => {
    const snapshot = createSnapshot(120_000)
    const stintSummary = createStintSummary({
      VER: {
        stintNumber: [1],
        compound: ['SOFT'],
        startLap: [1],
        endLap: [20],
        startTimeMs: [0],
        endTimeMs: [60_000],
        tyreLifeAtStart: [0],
        isFreshTyre: [true],
        pitInTimeMs: [null],
        pitOutTimeMs: [null],
      },
    })

    render(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
        totalLaps={10}
      />,
    )

    expect(screen.queryByLabelText(/Remaining race distance/)).toBeNull()
  })

  test('treats invalid totalLaps (zero, negative, fractional) as null', () => {
    const snapshot = createSnapshot(120_000)
    const stintSummary = createStintSummary({
      VER: {
        stintNumber: [1],
        compound: ['SOFT'],
        startLap: [1],
        endLap: [20],
        startTimeMs: [0],
        endTimeMs: [60_000],
        tyreLifeAtStart: [0],
        isFreshTyre: [true],
        pitInTimeMs: [null],
        pitOutTimeMs: [null],
      },
    })

    const { rerender } = render(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
        totalLaps={0}
      />,
    )
    expect(screen.queryByLabelText(/Remaining race distance/)).toBeNull()

    rerender(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
        totalLaps={-5}
      />,
    )
    expect(screen.queryByLabelText(/Remaining race distance/)).toBeNull()

    rerender(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
        totalLaps={10.5}
      />,
    )
    expect(screen.queryByLabelText(/Remaining race distance/)).toBeNull()
  })

  test('pit markers are valid listitems within the timeline list', () => {
    const snapshot = createSnapshot(65_000)
    const stintSummary = createStintSummary({
      VER: {
        stintNumber: [1, 2],
        compound: ['SOFT', 'MEDIUM'],
        startLap: [1, 15],
        endLap: [14, null],
        startTimeMs: [0, 60_000],
        endTimeMs: [55_000, null],
        tyreLifeAtStart: [0, 0],
        isFreshTyre: [true, true],
        pitInTimeMs: [52_000, null],
        pitOutTimeMs: [58_000, null],
      },
    })

    render(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
      />,
    )

    const timeline = screen.getByRole('list', { name: /Race distance timeline/i })
    expect(timeline).toBeTruthy()
    const pitMarker = screen.getByLabelText(/Pit stop before stint 2/)
    expect(pitMarker).toBeTruthy()
    expect(pitMarker.getAttribute('role')).toBe('listitem')
    // Verify it's a direct child of the list's bar container
    expect(pitMarker.parentElement?.parentElement).toBe(timeline)
  })

  test('timeline bar reserves enough vertical space and does not clip content', () => {
    const snapshot = createSnapshot(120_000)
    const stintSummary = createStintSummary({
      VER: {
        stintNumber: [1],
        compound: ['SOFT'],
        startLap: [1],
        endLap: [null],
        startTimeMs: [0],
        endTimeMs: [null],
        tyreLifeAtStart: [0],
        isFreshTyre: [true],
        pitInTimeMs: [null],
        pitOutTimeMs: [null],
      },
    })

    render(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
      />,
    )

    const timeline = screen.getByRole('list', { name: /Race distance timeline/i })
    const bar = timeline.firstElementChild as HTMLElement | null
    expect(bar).not.toBeNull()
    expect(bar!.style.minHeight).toBe('4rem')
    expect(bar!.style.overflow).not.toBe('hidden')
  })

  test('does not break hook order when selectedDriverId and snapshot transition from unavailable to available', () => {
    // Regression: hooks must remain in the same order across renders even when
    // the component toggles between the "unavailable" early-return path and the
    // full rendering path. This is the same-mounted-component constraint that
    // React's Rules of Hooks enforce.
    const stintSummary = createStintSummary({
      VER: {
        stintNumber: [1],
        compound: ['SOFT'],
        startLap: [1],
        endLap: [null],
        startTimeMs: [0],
        endTimeMs: [null],
        tyreLifeAtStart: [0],
        isFreshTyre: [true],
        pitInTimeMs: [null],
        pitOutTimeMs: [null],
      },
    })

    const { rerender } = render(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId={null}
        snapshot={null}
        stintSummary={null}
      />,
    )
    expect(screen.getByRole('status').textContent).toContain('Tyre strategy is unavailable')

    // Transition to available state on the same mounted component
    const snapshot = createSnapshot(120_000)
    rerender(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
      />,
    )
    expect(screen.getAllByText('Soft').length).toBeGreaterThanOrEqual(1)

    // Transition back to unavailable — must not throw a hooks-order error
    rerender(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId={null}
        snapshot={null}
        stintSummary={null}
      />,
    )
    expect(screen.getByRole('status').textContent).toContain('Tyre strategy is unavailable')

    // Transition to available again to confirm bidirectional stability
    rerender(
      <TyreStrategyPanel
        drivers={drivers}
        selectedDriverId="VER"
        snapshot={snapshot}
        stintSummary={stintSummary}
      />,
    )
    expect(screen.getAllByText('Soft').length).toBeGreaterThanOrEqual(1)
  })
})

describe('Pure helper functions', () => {
  test('compoundColor maps known compounds to correct colours', () => {
    expect(compoundColor('SOFT')).toBe('#ff3138')
    expect(compoundColor('soft')).toBe('#ff3138')
    expect(compoundColor('MEDIUM')).toBe('#f0bc53')
    expect(compoundColor('HARD')).toBe('#f4f5f6')
    expect(compoundColor('INTERMEDIATE')).toBe('#3dcc6b')
    expect(compoundColor('WET')).toBe('#4da6e8')
    expect(compoundColor(null)).toBe('#7a8794')
    expect(compoundColor('UNKNOWN')).toBe('#7a8794')
  })

  test('formatCompound normalizes compound display', () => {
    expect(formatCompound('SOFT')).toBe('Soft')
    expect(formatCompound('soft')).toBe('Soft')
    expect(formatCompound('MEDIUM')).toBe('Medium')
    expect(formatCompound(null)).toBe('Unknown')
    expect(formatCompound('')).toBe('Unknown')
  })

  test('formatLapRange handles ongoing and completed stints', () => {
    expect(formatLapRange(1, 14)).toBe('Lap 1–14')
    expect(formatLapRange(15, null)).toBe('Lap 15–ongoing')
  })

  test('formatSignedGapMs formats signed gap with 3 decimal places', () => {
    expect(formatSignedGapMs(2_000)).toBe('+2.000s')
    expect(formatSignedGapMs(-1_500)).toBe('-1.500s')
    expect(formatSignedGapMs(0)).toBe('0.000s')
    expect(formatSignedGapMs(NaN)).toBe('—')
  })
})
