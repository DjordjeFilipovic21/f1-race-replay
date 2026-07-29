/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, test } from 'vitest'
import {
  AfterPitComparison,
  CLOSE_REJOIN_THRESHOLD_MS,
  GAP_THRESHOLD_MS,
  classifyAheadState,
  formatDistanceLabel,
  formatNaturalLanguageGap,
  formatSignedGapLabel,
} from '../../../../src/features/replay/panels/AfterPitComparison'
import type { PitRejoinProjection } from '../../../../src/features/replay/selectors/pit-rejoin-selectors'
import type { DriverMetadata } from '../../../../src/data/replay/types'

const drivers: readonly DriverMetadata[] = [
  { id: 'VER', displayName: 'Max Verstappen', teamName: 'Red Bull Racing', colorHex: '#3671c6', carNumber: '1' },
  { id: 'NOR', displayName: 'Lando Norris', teamName: 'McLaren', colorHex: '#ff8000', carNumber: '4' },
]

const threeDrivers: readonly DriverMetadata[] = [
  ...drivers,
  { id: 'LEC', displayName: 'Charles Leclerc', teamName: 'Ferrari', colorHex: '#dc0000', carNumber: '16' },
]

function makeProjection(overrides: Partial<PitRejoinProjection> = {}): PitRejoinProjection {
  return Object.freeze({
    selectedDriverId: 'VER',
    projectedGapToLeaderMs: 22_000,
    projectedPosition: 2,
    currentPosition: 1,
    aheadComparator: null,
    behindComparator: null,
    ...overrides,
  })
}

afterEach(cleanup)

describe('AfterPitComparison pure helpers', () => {
  test('CLOSE_REJOIN_THRESHOLD_MS is 1000', () => {
    expect(CLOSE_REJOIN_THRESHOLD_MS).toBe(1_000)
  })

  test('GAP_THRESHOLD_MS is 3000', () => {
    expect(GAP_THRESHOLD_MS).toBe(3_000)
  })

  test('classifyAheadState returns ahead-close at exactly 1000ms', () => {
    expect(classifyAheadState(makeProjection({ aheadComparator: { driverId: 'NOR', gapMs: 21_000, signedGapMs: 1_000 } }))).toBe('ahead-close')
  })

  test('classifyAheadState returns ahead-gap at 1001ms', () => {
    expect(classifyAheadState(makeProjection({ aheadComparator: { driverId: 'NOR', gapMs: 20_999, signedGapMs: 1_001 } }))).toBe('ahead-gap')
  })

  test('classifyAheadState returns ahead-gap at exactly 3000ms', () => {
    expect(classifyAheadState(makeProjection({ aheadComparator: { driverId: 'NOR', gapMs: 19_000, signedGapMs: 3_000 } }))).toBe('ahead-gap')
  })

  test('classifyAheadState returns ahead-clear-air at 3001ms', () => {
    expect(classifyAheadState(makeProjection({ aheadComparator: { driverId: 'NOR', gapMs: 18_999, signedGapMs: 3_001 } }))).toBe('ahead-clear-air')
  })

  test('classifyAheadState returns ahead-clear-air when no ahead comparator', () => {
    expect(classifyAheadState(makeProjection())).toBe('ahead-clear-air')
    expect(classifyAheadState(makeProjection({ behindComparator: { driverId: 'NOR', gapMs: 22_500, signedGapMs: -500 } }))).toBe('ahead-clear-air')
  })

  test('classifyAheadState ignores behind comparator for ahead state', () => {
    // Behind within 1000 does not affect ahead classification
    expect(classifyAheadState(makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 19_000, signedGapMs: 3_000 },
      behindComparator: { driverId: 'LEC', gapMs: 22_500, signedGapMs: -500 },
    }))).toBe('ahead-gap')
  })

  test('formatDistanceLabel shows absolute gap in milliseconds', () => {
    expect(formatDistanceLabel(1_500)).toBe('1500 ms')
    expect(formatDistanceLabel(-1_500)).toBe('1500 ms')
    expect(formatDistanceLabel(0)).toBe('0 ms')
    expect(formatDistanceLabel(NaN)).toBe('—')
  })

  test('formatNaturalLanguageGap formats absolute gap with direction', () => {
    expect(formatNaturalLanguageGap(500, 'ahead')).toBe('0.500s ahead')
    expect(formatNaturalLanguageGap(1_200, 'behind')).toBe('1.200s behind')
    expect(formatNaturalLanguageGap(0, 'ahead')).toBe('0.000s ahead')
    expect(formatNaturalLanguageGap(NaN, 'behind')).toBe('—')
  })

  test('formatSignedGapLabel formats signed gap with sign', () => {
    expect(formatSignedGapLabel(500)).toBe('+0.500s')
    expect(formatSignedGapLabel(-700)).toBe('-0.700s')
    expect(formatSignedGapLabel(0)).toBe('0.000s')
    expect(formatSignedGapLabel(NaN)).toBe('—')
  })
})

describe('AfterPitComparison component — ahead-close state', () => {
  test('renders unavailable state when projection is null', () => {
    render(<AfterPitComparison projection={null} drivers={drivers} />)
    const card = screen.getByText('After pit comparison').closest('.after-pit-comparison')
    expect(card).not.toBeNull()
    expect(card!.textContent).toContain('Unavailable')
  })

  test('shows ahead car and signed label when ahead comparator is within 1000ms', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 21_500, signedGapMs: 500 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={drivers} />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const textContent = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    expect(textContent).toContain('VER')
    expect(textContent).toContain('NOR')
    expect(textContent).toContain('+0.500s')
    // No CLEAR AIR, no GAP marker, no wind
    expect(textContent).not.toContain('CLEAN AIR')
    expect(textContent).not.toContain('GAP')
    expect(container.querySelector('.after-pit-comparison__wind')).toBeNull()
  })

  test('shows behind car independently when behind is within 1000ms in ahead-close', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 21_500, signedGapMs: 500 },
      behindComparator: { driverId: 'LEC', gapMs: 22_600, signedGapMs: -600 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={threeDrivers} />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const textContent = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    expect(textContent).toContain('VER')
    expect(textContent).toContain('NOR')
    expect(textContent).toContain('+0.500s')
    expect(textContent).toContain('LEC')
    expect(textContent).toContain('-0.600s')
    // Three cars: selected + ahead + behind
    expect(svg!.querySelectorAll('use').length).toBe(3)
  })

  test('shows ahead-close at exact 1000ms boundary with car and label', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 21_000, signedGapMs: 1_000 },
      behindComparator: { driverId: 'LEC', gapMs: 23_000, signedGapMs: -1_000 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={threeDrivers} />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const textContent = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    expect(textContent).toContain('VER')
    expect(textContent).toContain('NOR')
    expect(textContent).toContain('+1.000s')
    expect(textContent).toContain('LEC')
    expect(textContent).toContain('-1.000s')
    expect(textContent).not.toContain('CLEAN AIR')
    expect(container.querySelector('.after-pit-comparison__wind')).toBeNull()
  })
})

describe('AfterPitComparison component — ahead-gap state', () => {
  test('shows red GAP marker and hides ahead car when ahead is 1001ms', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 20_999, signedGapMs: 1_001 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={drivers} />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const textContent = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    expect(textContent).toContain('VER')
    // Ahead car hidden
    expect(svg!.querySelectorAll('use').length).toBe(1) // only selected car
    expect(textContent).toContain('NOR:')
    // Red driver delta marker shown
    expect(textContent).not.toContain('GAP')
    expect(textContent).toContain('+1.001s')
    // No CLEAR AIR, no wind
    expect(textContent).not.toContain('CLEAN AIR')
    expect(container.querySelector('.after-pit-comparison__wind')).toBeNull()
    // Gap marker has red class
    const gapLabel = container.querySelector('.after-pit-comparison__svg-gap-label')
    expect(gapLabel).not.toBeNull()
  })

  test('shows red GAP marker at exactly 3000ms boundary', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 19_000, signedGapMs: 3_000 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={drivers} />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const textContent = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    expect(textContent).toContain('NOR:')
    expect(textContent).toContain('+3.000s')
    expect(textContent).not.toContain('CLEAN AIR')
    expect(container.querySelector('.after-pit-comparison__wind')).toBeNull()
  })

  test('shows no wind decoration in ahead-gap state', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 20_000, signedGapMs: 2_000 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={drivers} />)
    expect(container.querySelector('.after-pit-comparison__wind')).toBeNull()
  })

  test('behind comparator remains visible in ahead-gap state when within 1000ms', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 20_000, signedGapMs: 2_000 },
      behindComparator: { driverId: 'LEC', gapMs: 22_500, signedGapMs: -500 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={threeDrivers} />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const textContent = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    // Selected + behind cars visible
    expect(svg!.querySelectorAll('use').length).toBe(2)
    expect(textContent).toContain('VER')
    expect(textContent).toContain('LEC')
    expect(textContent).toContain('-0.500s')
    // Ahead car hidden, driver delta marker shown
    expect(textContent).toContain('NOR:')
    expect(textContent).not.toContain('GAP')
    expect(textContent).toContain('+2.000s')
  })

  test('shows green behind delta at the lower 1s line when beyond 1000ms', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 20_000, signedGapMs: 2_000 },
      behindComparator: { driverId: 'LEC', gapMs: 24_000, signedGapMs: -2_000 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={threeDrivers} />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    // Only selected car visible
    expect(svg!.querySelectorAll('use').length).toBe(1)
    const textContent = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    expect(textContent).toContain('LEC:')
    expect(textContent).toContain('-2.000s')
    expect(textContent).toContain('NOR:')
    expect(container.querySelector('.after-pit-comparison__svg-behind-gap-label')).not.toBeNull()
  })
})

describe('AfterPitComparison component — ahead-clear-air state', () => {
  test('shows CLEAR AIR and wind when ahead exceeds 3000ms, with delta to ahead', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 18_999, signedGapMs: 3_001 },
      projectedPosition: 3,
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={drivers} />)
    expect(screen.getByText('P3')).toBeTruthy()
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const textContent = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    expect(textContent).toContain('CLEAN AIR')
    expect(textContent).toContain('VER')
    // Delta to ahead shown
    expect(textContent).toContain('NOR:')
    expect(textContent).toContain('+3.001s')
    // No ahead car
    expect(svg!.querySelectorAll('use').length).toBe(1)
    // Wind present
    expect(container.querySelector('.after-pit-comparison__wind')).not.toBeNull()
  })

  test('shows CLEAR AIR and wind with no delta when no ahead comparator', () => {
    const projection = makeProjection({ projectedPosition: 1 })
    const { container } = render(<AfterPitComparison projection={projection} drivers={drivers} />)
    expect(screen.getByText('P1')).toBeTruthy()
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const textContent = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    expect(textContent).toContain('CLEAN AIR')
    expect(textContent).toContain('VER')
    // No GAP delta (no fake delta)
    expect(textContent).not.toContain('GAP')
    // Wind present
    expect(container.querySelector('.after-pit-comparison__wind')).not.toBeNull()
  })

  test('behind comparator remains visible in ahead-clear-air state when within 1000ms', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 18_000, signedGapMs: 4_000 },
      behindComparator: { driverId: 'LEC', gapMs: 22_700, signedGapMs: -700 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={threeDrivers} />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const textContent = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    // Selected + behind cars visible
    expect(svg!.querySelectorAll('use').length).toBe(2)
    expect(textContent).toContain('VER')
    expect(textContent).toContain('LEC')
    expect(textContent).toContain('-0.700s')
    // CLEAR AIR + wind + delta to ahead
    expect(textContent).toContain('CLEAN AIR')
    expect(textContent).toContain('NOR:')
    expect(textContent).toContain('+4.000s')
    expect(container.querySelector('.after-pit-comparison__wind')).not.toBeNull()
  })

  test('clear-air SVG provides accessible description with ahead delta', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 18_000, signedGapMs: 4_000 },
    })
    render(<AfterPitComparison projection={projection} drivers={drivers} graphOnly />)
    const svg = document.querySelector('svg[role="img"]')
    expect(svg).not.toBeNull()
    const label = svg!.getAttribute('aria-label') ?? ''
    expect(label).toContain('Clean air')
    expect(label).toContain('VER')
    expect(label).toContain('+4.000s')
  })

  test('clear-air SVG provides accessible description with no ahead comparator', () => {
    const projection = makeProjection()
    render(<AfterPitComparison projection={projection} drivers={drivers} graphOnly />)
    const svg = document.querySelector('svg[role="img"]')
    expect(svg).not.toBeNull()
    const label = svg!.getAttribute('aria-label') ?? ''
    expect(label).toContain('Clean air')
    expect(label).toContain('VER')
    expect(label).toContain('No ahead comparator')
  })
})

describe('AfterPitComparison component — shared behavior', () => {
  test('applies team colours from driver metadata to cars in SVG', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 21_500, signedGapMs: 500 },
      behindComparator: { driverId: 'LEC', gapMs: 22_700, signedGapMs: -700 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={threeDrivers} />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const useElements = svg!.querySelectorAll('use')
    expect(useElements.length).toBe(3)
    const carGroups = svg!.querySelectorAll('g[style]')
    expect(carGroups.length).toBe(3)
    const colors = Array.from(carGroups).map(g => (g as HTMLElement).style.color)
    expect(colors).toContain('rgb(54, 113, 198)') // VER
    expect(colors).toContain('rgb(255, 128, 0)') // NOR
    expect(colors).toContain('rgb(220, 0, 0)') // LEC
  })

  test('falls back to default team colour when driver metadata is missing', () => {
    const projection = makeProjection({
      selectedDriverId: 'UNKNOWN',
      aheadComparator: { driverId: 'GHOST', gapMs: 21_500, signedGapMs: 500 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={drivers} />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const carGroups = svg!.querySelectorAll('g[style]')
    expect(carGroups.length).toBe(2)
    const colors = Array.from(carGroups).map(g => (g as HTMLElement).style.color)
    expect(colors).toEqual(['rgb(122, 135, 148)', 'rgb(122, 135, 148)'])
  })

  test('uses constrained stage so the SVG does not stretch in a wide panel', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 21_500, signedGapMs: 500 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={drivers} />)
    const stage = container.querySelector('.after-pit-comparison__stage')
    expect(stage).not.toBeNull()
  })

  test('provides accessible description naming all visible close cars', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 21_200, signedGapMs: 800 },
      behindComparator: { driverId: 'LEC', gapMs: 22_600, signedGapMs: -600 },
    })
    render(<AfterPitComparison projection={projection} drivers={threeDrivers} />)
    const svg = screen.getByRole('img')
    const label = svg.getAttribute('aria-label') ?? ''
    expect(label).toContain('VER')
    expect(label).toContain('NOR')
    expect(label).toContain('LEC')
    expect(label).toContain('800 ms')
    expect(label).toContain('600 ms')
  })

  test('keeps viewBox identical across all ahead states to prevent layout shift', () => {
    const closeProjection = makeProjection({ aheadComparator: { driverId: 'NOR', gapMs: 21_500, signedGapMs: 500 } })
    const { container, rerender } = render(<AfterPitComparison projection={closeProjection} drivers={drivers} graphOnly />)
    const viewBoxes: string[] = []
    viewBoxes.push(container.querySelector('svg')!.getAttribute('viewBox')!)

    const gapProjection = makeProjection({ aheadComparator: { driverId: 'NOR', gapMs: 20_000, signedGapMs: 2_000 } })
    rerender(<AfterPitComparison projection={gapProjection} drivers={drivers} graphOnly />)
    viewBoxes.push(container.querySelector('svg')!.getAttribute('viewBox')!)

    const clearAirProjection = makeProjection({ aheadComparator: { driverId: 'NOR', gapMs: 18_000, signedGapMs: 4_000 } })
    rerender(<AfterPitComparison projection={clearAirProjection} drivers={drivers} graphOnly />)
    viewBoxes.push(container.querySelector('svg')!.getAttribute('viewBox')!)

    const noAheadProjection = makeProjection()
    rerender(<AfterPitComparison projection={noAheadProjection} drivers={drivers} graphOnly />)
    viewBoxes.push(container.querySelector('svg')!.getAttribute('viewBox')!)

    expect(new Set(viewBoxes).size).toBe(1)
    expect(viewBoxes[0]).toBe('0 0 200 260')
  })
})

describe('AfterPitComparison graphOnly mode', () => {
  test('keeps an empty stable graph when projection is null in graphOnly mode', () => {
    const { container } = render(<AfterPitComparison projection={null} drivers={drivers} graphOnly />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg!.getAttribute('viewBox')).toBe('0 0 200 260')
    expect(svg!.getAttribute('aria-label')).toBe('After pit comparison unavailable')
    expect(svg!.querySelectorAll('use').length).toBe(0)
    expect(svg!.textContent).toContain('Gap vs rejoin')
  })

  test('renders only the SVG without eyebrow, value, or footnote in graphOnly ahead-close', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 21_500, signedGapMs: 500 },
      behindComparator: { driverId: 'LEC', gapMs: 22_700, signedGapMs: -700 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={threeDrivers} graphOnly />)
    expect(container.querySelector('.tyre-strategy-panel__card')).toBeNull()
    expect(container.querySelector('.tyre-strategy-panel__eyebrow')).toBeNull()
    expect(screen.queryByText('P2')).toBeNull()
    expect(screen.queryByText('Based on current gaps')).toBeNull()
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const textContent = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    expect(textContent).toContain('VER')
    expect(textContent).toContain('NOR')
    expect(textContent).toContain('LEC')
  })

  test('renders ahead-gap state with stable SVG and no wind in graphOnly mode', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 20_000, signedGapMs: 2_000 },
      behindComparator: { driverId: 'LEC', gapMs: 24_000, signedGapMs: -2_000 },
      projectedPosition: 3,
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={threeDrivers} graphOnly />)
    expect(container.querySelector('.tyre-strategy-panel__card')).toBeNull()
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const textContent = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    expect(textContent).toContain('VER')
    expect(textContent).toContain('NOR:')
    expect(textContent).toContain('+2.000s')
    expect(textContent).not.toContain('CLEAN AIR')
    expect(container.querySelector('.after-pit-comparison__wind')).toBeNull()
  })

  test('renders ahead-clear-air state with CLEAR AIR, delta, and wind in graphOnly mode', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 18_000, signedGapMs: 4_000 },
      projectedPosition: 3,
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={drivers} graphOnly />)
    expect(container.querySelector('.tyre-strategy-panel__card')).toBeNull()
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    const textContent = Array.from(svg!.querySelectorAll('text')).map(el => el.textContent).join(' ')
    expect(textContent).toContain('CLEAN AIR')
    expect(textContent).toContain('VER')
    expect(textContent).toContain('NOR:')
    expect(textContent).toContain('+4.000s')
    expect(container.querySelector('.after-pit-comparison__wind')).not.toBeNull()
    expect(screen.queryByText(/Based on current gaps/)).toBeNull()
  })

  test('default mode still renders the full card with eyebrow, value, and footnote', () => {
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 21_500, signedGapMs: 500 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={drivers} />)
    expect(container.querySelector('.tyre-strategy-panel__card')).not.toBeNull()
    expect(container.querySelector('.tyre-strategy-panel__eyebrow')).not.toBeNull()
    expect(screen.getByText('P2')).toBeTruthy()
    expect(screen.getByText('Based on current gaps')).toBeTruthy()
  })

  test('wind streak decoration is marked as aria-hidden', () => {
    // Use ahead-clear-air to get wind
    const projection = makeProjection({
      aheadComparator: { driverId: 'NOR', gapMs: 18_000, signedGapMs: 4_000 },
    })
    const { container } = render(<AfterPitComparison projection={projection} drivers={drivers} graphOnly />)
    const wind = container.querySelector('.after-pit-comparison__wind')
    expect(wind).not.toBeNull()
    expect(wind!.getAttribute('aria-hidden')).toBe('true')
  })

  test('panels.css contains a reduced-motion rule for wind streaks', () => {
    const { readFileSync } = require('node:fs')
    const { resolve } = require('node:path')
    const panelsCss = readFileSync(resolve(process.cwd(), 'src/styles/panels.css'), 'utf8')
    expect(panelsCss).toContain('prefers-reduced-motion')
    expect(panelsCss).toContain('.after-pit-comparison__wind-streak')
    expect(panelsCss).toContain('animation: none')
  })
})
