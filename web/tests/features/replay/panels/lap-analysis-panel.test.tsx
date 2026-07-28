/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, test } from 'vitest'
import { LapAnalysisPanel, formatLapDuration, sectorColourClass } from '../../../../src/features/replay/panels/LapAnalysisPanel'
import type { DriverMetadata } from '../../../../src/data/replay/types'
import type { LapSectorSelection, VisibleLap, VisibleSector } from '../../../../src/features/replay/selectors/lap-sector-selectors'
import type { ColouredSector, SectorColour, SectorColourSelection } from '../../../../src/features/replay/selectors/sector-colour-selectors'

const DRIVERS: readonly DriverMetadata[] = [
  { id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' },
  { id: 'NOR', displayName: 'Lando Norris', teamName: 'McLaren', colorHex: '#ff8000', carNumber: '4' },
]

afterEach(cleanup)

describe('LapAnalysisPanel', () => {
  test('renders the chart and always-visible summary, hiding the sector history by default', () => {
    const lapSector = createLapSectorSelection('VER', [
      { lapNumber: 1, durationMs: 92_456 },
      { lapNumber: 2, durationMs: 91_234 },
      { lapNumber: 3, durationMs: 90_876 },
    ])
    const sectorColours = createSectorColourSelection('VER', [
      { lapNumber: 1, sectorNumber: 1, durationMs: 30_100, colour: 'slower' },
      { lapNumber: 1, sectorNumber: 2, durationMs: 32_200, colour: 'slower' },
      { lapNumber: 1, sectorNumber: 3, durationMs: 30_156, colour: 'slower' },
      { lapNumber: 2, sectorNumber: 1, durationMs: 29_800, colour: 'personal-best' },
      { lapNumber: 2, sectorNumber: 2, durationMs: 31_500, colour: 'slower' },
      { lapNumber: 2, sectorNumber: 3, durationMs: 29_934, colour: 'personal-best' },
      { lapNumber: 3, sectorNumber: 1, durationMs: 29_500, colour: 'session-best' },
      { lapNumber: 3, sectorNumber: 2, durationMs: 31_200, colour: 'slower' },
      { lapNumber: 3, sectorNumber: 3, durationMs: 30_176, colour: 'slower' },
    ])

    const { container } = render(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={lapSector} sectorColours={sectorColours} />,
    )

    expect(screen.getByRole('heading', { name: 'Lap analysis' })).toBeTruthy()
    expect(screen.queryByText('Max Verstappen')).toBeNull()
    expect(screen.queryByText('#1')).toBeNull()
    expect(screen.getByRole('img', { name: /lap time chart for 3 completed laps/i })).toBeTruthy()
    expect(screen.getByText('Latest')).toBeTruthy()
    expect(screen.getByText('Best')).toBeTruthy()
    expect(screen.getAllByText(formatLapDuration(90_876)).length).toBeGreaterThanOrEqual(1)

    // History table hidden by default
    const table = container.querySelector('.lap-analysis-panel__sectors')
    expect(table).toBeNull()

    // Toggle button present and collapsed
    const toggle = screen.getByRole('button', { name: 'Show lap history' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(toggle.getAttribute('aria-controls')).toBe('lap-analysis-history')
  })

  test('always shows the latest and best lap summary when completed laps exist', () => {
    const lapSector = createLapSectorSelection('VER', [
      { lapNumber: 1, durationMs: 92_000 },
      { lapNumber: 2, durationMs: 90_000 },
      { lapNumber: 3, durationMs: 91_000 },
    ])
    const sectorColours = createSectorColourSelection('VER', [])

    const { container } = render(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={lapSector} sectorColours={sectorColours} />,
    )

    const summary = container.querySelector('.lap-analysis-panel__summary')
    expect(summary).not.toBeNull()

    expect(screen.getByText('Latest')).toBeTruthy()
    expect(screen.getByText('Best')).toBeTruthy()

    // Best lap value (unique — not on chart axis)
    expect(screen.getByText(formatLapDuration(90_000))).toBeTruthy()

    // Summary has two items: Latest and Best
    const summaryItems = container.querySelectorAll('.lap-analysis-panel__summary-item')
    expect(summaryItems.length).toBe(2)
  })

  test('shows an accessible empty state when no driver is selected', () => {
    const lapSector = createLapSectorSelection('VER', [])
    const sectorColours = createSectorColourSelection('VER', [])

    render(<LapAnalysisPanel drivers={DRIVERS} selectedDriverId={null} lapSector={lapSector} sectorColours={sectorColours} />)

    const status = screen.getByRole('status')
    expect(status.textContent).toContain('Lap analysis is unavailable')
  })

  test('shows a no-laps empty state when a driver is selected but has no completed laps', () => {
    const lapSector = createLapSectorSelection('VER', [])
    const sectorColours = createSectorColourSelection('VER', [])

    render(<LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={lapSector} sectorColours={sectorColours} />)

    const status = screen.getByRole('status')
    expect(status.textContent).toContain('No completed laps yet')
  })

  test('provides an accessible toggle that reveals and hides the lap history sector table', () => {
    const lapSector = createLapSectorSelection('VER', [
      { lapNumber: 1, durationMs: 92_000 },
      { lapNumber: 2, durationMs: 91_000 },
    ])
    const sectorColours = createSectorColourSelection('VER', [
      { lapNumber: 1, sectorNumber: 1, durationMs: 30_000, colour: 'slower' },
      { lapNumber: 1, sectorNumber: 2, durationMs: 32_000, colour: 'slower' },
      { lapNumber: 1, sectorNumber: 3, durationMs: 30_000, colour: 'slower' },
      { lapNumber: 2, sectorNumber: 1, durationMs: 29_500, colour: 'personal-best' },
      { lapNumber: 2, sectorNumber: 2, durationMs: 31_500, colour: 'slower' },
      { lapNumber: 2, sectorNumber: 3, durationMs: 30_000, colour: 'slower' },
    ])

    const { container } = render(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={lapSector} sectorColours={sectorColours} />,
    )

    // Initially hidden
    expect(container.querySelector('.lap-analysis-panel__sectors')).toBeNull()

    // Expand
    fireEvent.click(screen.getByRole('button', { name: 'Show lap history' }))
    const table = container.querySelector('.lap-analysis-panel__sectors')
    expect(table).not.toBeNull()
    expect(table!.querySelectorAll('tbody tr').length).toBe(2)

    const expandedToggle = screen.getByRole('button', { name: 'Hide lap history' })
    expect(expandedToggle.getAttribute('aria-expanded')).toBe('true')
    expect(expandedToggle.getAttribute('aria-controls')).toBe('lap-analysis-history')

    // Collapse
    fireEvent.click(expandedToggle)
    expect(container.querySelector('.lap-analysis-panel__sectors')).toBeNull()
    expect(screen.getByRole('button', { name: 'Show lap history' }).getAttribute('aria-expanded')).toBe('false')
  })

  test('applies session-best and personal-best colour classes to sector cells when expanded', () => {
    const lapSector = createLapSectorSelection('VER', [
      { lapNumber: 1, durationMs: 92_000 },
      { lapNumber: 2, durationMs: 91_000 },
    ])
    const sectorColours = createSectorColourSelection('VER', [
      { lapNumber: 1, sectorNumber: 1, durationMs: 30_000, colour: 'slower' },
      { lapNumber: 1, sectorNumber: 2, durationMs: 32_000, colour: 'session-best' },
      { lapNumber: 1, sectorNumber: 3, durationMs: 30_000, colour: 'slower' },
      { lapNumber: 2, sectorNumber: 1, durationMs: 29_500, colour: 'personal-best' },
      { lapNumber: 2, sectorNumber: 2, durationMs: 31_500, colour: 'slower' },
      { lapNumber: 2, sectorNumber: 3, durationMs: 30_000, colour: 'slower' },
    ])

    const { container } = render(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={lapSector} sectorColours={sectorColours} />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Show lap history' }))

    const sessionBestCells = container.querySelectorAll(`.${sectorColourClass('session-best')}`)
    expect(sessionBestCells.length).toBe(1)
    expect(sessionBestCells[0].textContent).toBe('32.000')

    const personalBestCells = container.querySelectorAll(`.${sectorColourClass('personal-best')}`)
    expect(personalBestCells.length).toBe(1)
    expect(personalBestCells[0].textContent).toBe('29.500')

    const slowerCells = container.querySelectorAll(`.${sectorColourClass('slower')}`)
    expect(slowerCells.length).toBe(4)
  })

  test('respects causal boundaries by rendering only the laps present in the selection when expanded', () => {
    const earlySelection = createLapSectorSelection('VER', [
      { lapNumber: 1, durationMs: 92_000 },
      { lapNumber: 2, durationMs: 91_000 },
    ])
    const laterSelection = createLapSectorSelection('VER', [
      { lapNumber: 1, durationMs: 92_000 },
      { lapNumber: 2, durationMs: 91_000 },
      { lapNumber: 3, durationMs: 90_500 },
      { lapNumber: 4, durationMs: 91_200 },
      { lapNumber: 5, durationMs: 90_100 },
    ])
    const emptyColours = createSectorColourSelection('VER', [])

    const { container: earlyContainer } = render(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={earlySelection} sectorColours={emptyColours} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Show lap history' }))
    expect(earlyContainer.querySelectorAll('.lap-analysis-panel__sector-row').length).toBe(2)

    cleanup()

    const { container: laterContainer } = render(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={laterSelection} sectorColours={emptyColours} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Show lap history' }))
    expect(laterContainer.querySelectorAll('.lap-analysis-panel__sector-row').length).toBe(5)
  })

  test('highlights the latest and best lap points in the SVG chart', () => {
    const lapSector = createLapSectorSelection('VER', [
      { lapNumber: 1, durationMs: 92_000 },
      { lapNumber: 2, durationMs: 90_000 },
      { lapNumber: 3, durationMs: 91_000 },
    ])
    const sectorColours = createSectorColourSelection('VER', [])

    const { container } = render(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={lapSector} sectorColours={sectorColours} />,
    )

    const bestPoints = container.querySelectorAll('.lap-analysis-panel__chart-point--best')
    expect(bestPoints.length).toBe(1)
    expect(bestPoints[0].querySelector('title')?.textContent).toContain('Lap 2')

    const latestPoints = container.querySelectorAll('.lap-analysis-panel__chart-point--latest')
    expect(latestPoints.length).toBe(1)
    expect(latestPoints[0].querySelector('title')?.textContent).toContain('Lap 3')
  })

  test('marks unavailable sectors when coloured sector data is missing for a completed lap', () => {
    const lapSector = createLapSectorSelection('VER', [
      { lapNumber: 1, durationMs: 92_000 },
    ])
    const sectorColours = createSectorColourSelection('VER', [])

    const { container } = render(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={lapSector} sectorColours={sectorColours} />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Show lap history' }))

    const unavailableCells = container.querySelectorAll(`.${sectorColourClass('unavailable')}`)
    expect(unavailableCells.length).toBe(3)
    unavailableCells.forEach((cell) => expect(cell.textContent).toBe('—'))
  })

  test('removes future laps and sectors when the replay cursor moves backward', () => {
    const fiveLaps = createLapSectorSelection('VER', [
      { lapNumber: 1, durationMs: 92_000 },
      { lapNumber: 2, durationMs: 91_000 },
      { lapNumber: 3, durationMs: 90_500 },
      { lapNumber: 4, durationMs: 91_200 },
      { lapNumber: 5, durationMs: 90_100 },
    ])
    const fiveLapSectors = createSectorColourSelection('VER', [
      { lapNumber: 1, sectorNumber: 1, durationMs: 30_000, colour: 'slower' },
      { lapNumber: 1, sectorNumber: 2, durationMs: 32_000, colour: 'slower' },
      { lapNumber: 1, sectorNumber: 3, durationMs: 30_000, colour: 'slower' },
      { lapNumber: 2, sectorNumber: 1, durationMs: 29_500, colour: 'personal-best' },
      { lapNumber: 2, sectorNumber: 2, durationMs: 31_500, colour: 'slower' },
      { lapNumber: 2, sectorNumber: 3, durationMs: 30_000, colour: 'slower' },
      { lapNumber: 3, sectorNumber: 1, durationMs: 29_000, colour: 'session-best' },
      { lapNumber: 3, sectorNumber: 2, durationMs: 31_200, colour: 'slower' },
      { lapNumber: 3, sectorNumber: 3, durationMs: 30_300, colour: 'slower' },
      { lapNumber: 4, sectorNumber: 1, durationMs: 29_800, colour: 'slower' },
      { lapNumber: 4, sectorNumber: 2, durationMs: 31_100, colour: 'slower' },
      { lapNumber: 4, sectorNumber: 3, durationMs: 30_300, colour: 'slower' },
      { lapNumber: 5, sectorNumber: 1, durationMs: 29_200, colour: 'personal-best' },
      { lapNumber: 5, sectorNumber: 2, durationMs: 30_900, colour: 'slower' },
      { lapNumber: 5, sectorNumber: 3, durationMs: 30_000, colour: 'slower' },
    ])
    const emptyColours = createSectorColourSelection('VER', [])

    const { container, rerender } = render(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={fiveLaps} sectorColours={fiveLapSectors} />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Show lap history' }))
    expect(container.querySelectorAll('.lap-analysis-panel__sector-row').length).toBe(5)
    expect(container.querySelectorAll(`.${sectorColourClass('session-best')}`).length).toBe(1)
    expect(container.querySelectorAll(`.${sectorColourClass('personal-best')}`).length).toBe(2)

    const twoLaps = createLapSectorSelection('VER', [
      { lapNumber: 1, durationMs: 92_000 },
      { lapNumber: 2, durationMs: 91_000 },
    ])

    rerender(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={twoLaps} sectorColours={emptyColours} />,
    )

    expect(container.querySelectorAll('.lap-analysis-panel__sector-row').length).toBe(2)
    expect(container.querySelectorAll(`.${sectorColourClass('session-best')}`).length).toBe(0)
    expect(container.querySelectorAll(`.${sectorColourClass('personal-best')}`).length).toBe(0)
  })

  test('maintains hook order across unavailable to available transitions without crashing', () => {
    const emptyLapSector = createLapSectorSelection('VER', [])
    const emptySectorColours = createSectorColourSelection('VER', [])

    const { rerender } = render(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId={null} lapSector={emptyLapSector} sectorColours={emptySectorColours} />,
    )

    expect(screen.getByRole('status').textContent).toContain('Lap analysis is unavailable')

    const lapSector = createLapSectorSelection('VER', [
      { lapNumber: 1, durationMs: 92_000 },
    ])
    const sectorColours = createSectorColourSelection('VER', [
      { lapNumber: 1, sectorNumber: 1, durationMs: 30_000, colour: 'slower' },
      { lapNumber: 1, sectorNumber: 2, durationMs: 32_000, colour: 'slower' },
      { lapNumber: 1, sectorNumber: 3, durationMs: 30_000, colour: 'slower' },
    ])

    rerender(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={lapSector} sectorColours={sectorColours} />,
    )

    expect(screen.getByRole('heading', { name: 'Lap analysis' })).toBeTruthy()
    expect(screen.getByText('Latest')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Show lap history' })).toBeTruthy()
  })

  test('maintains hook order across no-laps to available transitions without crashing', () => {
    const emptyLapSector = createLapSectorSelection('VER', [])
    const emptySectorColours = createSectorColourSelection('VER', [])

    const { rerender } = render(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={emptyLapSector} sectorColours={emptySectorColours} />,
    )

    expect(screen.getByRole('status').textContent).toContain('No completed laps yet')

    const lapSector = createLapSectorSelection('VER', [
      { lapNumber: 1, durationMs: 92_000 },
    ])
    const sectorColours = createSectorColourSelection('VER', [
      { lapNumber: 1, sectorNumber: 1, durationMs: 30_000, colour: 'slower' },
      { lapNumber: 1, sectorNumber: 2, durationMs: 32_000, colour: 'slower' },
      { lapNumber: 1, sectorNumber: 3, durationMs: 30_000, colour: 'slower' },
    ])

    rerender(
      <LapAnalysisPanel drivers={DRIVERS} selectedDriverId="VER" lapSector={lapSector} sectorColours={sectorColours} />,
    )

    expect(screen.getByRole('heading', { name: 'Lap analysis' })).toBeTruthy()
    expect(screen.getByText('Latest')).toBeTruthy()
  })
})

describe('formatLapDuration', () => {
  test('formats milliseconds as M:SS.mmm', () => {
    expect(formatLapDuration(92_456)).toBe('1:32.456')
    expect(formatLapDuration(60_000)).toBe('1:00.000')
    expect(formatLapDuration(30_123)).toBe('0:30.123')
  })

  test('returns a dash for null, non-finite, or non-positive values', () => {
    expect(formatLapDuration(null)).toBe('—')
    expect(formatLapDuration(NaN)).toBe('—')
    expect(formatLapDuration(0)).toBe('—')
    expect(formatLapDuration(-1000)).toBe('—')
  })
})

// --- Test factories ---

function createLapSectorSelection(driverId: string, laps: ReadonlyArray<{ lapNumber: number; durationMs: number | null }>): LapSectorSelection {
  const visibleLaps: VisibleLap[] = laps.map((lap) => {
    const lapStartMs = (lap.lapNumber - 1) * 90_000
    const lapEndMs = lapStartMs + (lap.durationMs ?? 90_000)
    const sectors: VisibleSector[] = [1, 2, 3].map((sn) => ({
      lapNumber: lap.lapNumber,
      sectorNumber: sn as 1 | 2 | 3,
      durationMs: lap.durationMs !== null ? Math.round(lap.durationMs / 3) : null,
      sessionTimeMs: lapStartMs + sn * ((lap.durationMs ?? 90_000) / 3),
    }))
    return { lapNumber: lap.lapNumber, lapStartMs, lapEndMs, lapDurationMs: lap.durationMs, sectors }
  })
  const allSectors = visibleLaps.flatMap((lap) => lap.sectors)
  return Object.freeze({ driverId, sessionTimeMs: 300_000, laps: Object.freeze(visibleLaps), sectors: Object.freeze(allSectors) })
}

function createSectorColourSelection(driverId: string, entries: ReadonlyArray<{ lapNumber: number; sectorNumber: 1 | 2 | 3; durationMs: number | null; colour: SectorColour }>): SectorColourSelection {
  const sectors: ColouredSector[] = entries.map((entry) => ({
    lapNumber: entry.lapNumber,
    sectorNumber: entry.sectorNumber,
    durationMs: entry.durationMs,
    sessionTimeMs: entry.lapNumber * 90_000 + entry.sectorNumber * 30_000,
    sessionBestMs: entry.colour === 'session-best' ? entry.durationMs : null,
    personalBestMs: entry.colour === 'personal-best' ? entry.durationMs : null,
    colour: entry.colour,
    isSessionBest: entry.colour === 'session-best',
    isPersonalBest: entry.colour === 'personal-best',
  }))
  return Object.freeze({ driverId, sessionTimeMs: 300_000, sectors: Object.freeze(sectors) })
}
