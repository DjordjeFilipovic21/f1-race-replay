/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import type { WeatherSidecar } from '../../../../src/data/replay/types'
import type { ReplayControllerSnapshot } from '../../../../src/engine/replay'
import { compassPoint, WeatherPanel } from '../../../../src/features/replay/panels/WeatherPanel'

/** Three-row sidecar whose final observation is complete and recent enough to stay fresh. */
const sidecar = (overrides: Partial<WeatherSidecar> = {}): WeatherSidecar => ({
  contractVersion: 'v2',
  fixtureId: 'weather-fixture',
  timeMs: [1_000, 2_000, 3_000],
  airTempC: [19.5, 21.4, 22.0],
  humidityPct: [60, 58, 59],
  pressureMbar: [1014.2, 1013.6, 1013.1],
  rainfall: [false, false, true],
  trackTempC: [28.1, 31.2, 32.4],
  windDirectionDeg: [90, 270, 315],
  windSpeedMps: [2.1, 3.5, 4.8],
  ...overrides,
})

/** Single-row sidecar used to probe age/status formatting without extra observations. */
const singleObservation = (overrides: Partial<WeatherSidecar> = {}): WeatherSidecar => ({
  contractVersion: 'v2',
  fixtureId: 'weather-fixture',
  timeMs: [0],
  airTempC: [21.0],
  humidityPct: [60],
  pressureMbar: [1013.2],
  rainfall: [false],
  trackTempC: [30.5],
  windDirectionDeg: [180],
  windSpeedMps: [3.2],
  ...overrides,
})

const snapshotAt = (sessionTimeMs: number): ReplayControllerSnapshot => ({
  status: 'ready',
  timeMs: sessionTimeMs,
  speed: 1,
  isPlaying: true,
  committedSeekRevision: 0,
  crossedEvents: [],
  error: null,
  replay: {
    sessionTimeMs,
    leaderboardOrder: null,
    trackStatusCode: null,
    weatherState: null,
    events: [],
    drivers: {},
  },
})

afterEach(cleanup)

test('renders the compact weather metric set from the latest causal observation while fresh', () => {
  // Arrange - the last observation (3s) is complete, recent, and reports rain.
  render(<WeatherPanel snapshot={snapshotAt(3_500)} weatherSidecar={sidecar()} />)

  // Act - the panel selects the latest causal row (315°, 4.8 m/s, 22.0 °C).

  // Assert - the compact metric set, compass label, and update text.
  expect(screen.getByRole('article', { name: 'Weather' }).getAttribute('data-state')).toBe('fresh')
  expect(screen.getByRole('heading', { name: 'Weather' })).toBeTruthy()
  expect(screen.getByText('Wind from')).toBeTruthy()
  expect(screen.getByRole('img', { name: 'Wind from 315°, NW' })).toBeTruthy()
  expect(screen.getByText('315°')).toBeTruthy()
  expect(screen.getByText('NW · from')).toBeTruthy()
  expect(screen.getByText('Wind speed')).toBeTruthy()
  expect(screen.getByText('4.8 m/s')).toBeTruthy()
  expect(screen.getByText('Air temp')).toBeTruthy()
  expect(screen.getByText('22.0 °C')).toBeTruthy()
  expect(screen.getByText('Track temp')).toBeTruthy()
  expect(screen.getByText('32.4 °C')).toBeTruthy()
  expect(screen.getByText('Humidity')).toBeTruthy()
  expect(screen.getByText('59 %')).toBeTruthy()
  expect(screen.getByText('Pressure')).toBeTruthy()
  expect(screen.getByText('1.013 bar')).toBeTruthy()
  expect(screen.queryByText('Rainfall')).toBeNull()
  expect(screen.getByText('Last observation')).toBeTruthy()
  expect(screen.getByText('Just now')).toBeTruthy()
  expect(screen.getByText('fresh, Just now')).toBeTruthy()
})

test('maps wind direction degrees to compass labels and normalizes wraparound', () => {
  // Arrange/Act - exercise the exported compass label helper across the compass rose.
  // Assert - cardinal/intercardinal labels, wraparound normalization, and null fallback.
  expect(compassPoint(0)).toBe('N')
  expect(compassPoint(45)).toBe('NE')
  expect(compassPoint(90)).toBe('E')
  expect(compassPoint(180)).toBe('S')
  expect(compassPoint(270)).toBe('W')
  expect(compassPoint(315)).toBe('NW')
  expect(compassPoint(360)).toBe('N')
  expect(compassPoint(-90)).toBe('W')
  expect(compassPoint(450)).toBe('E')
  expect(compassPoint(Number.NaN)).toBe('—')
})

test('renders the cautious wind-from arrow with meaningful labels and rotation', () => {
  // Arrange - a westerly observation so the arrow rotates to the reported origin.
  const { container } = render(<WeatherPanel snapshot={snapshotAt(2_500)} weatherSidecar={sidecar({ timeMs: [1_000, 2_000, 3_000] })} />)

  // Act - the panel labels the direction as the origin and rotates the arrow by degrees.

  // Assert - the arrow is decorative, rotated to the reported degrees, and labelled for AT users.
  const arrowSvg = container.querySelector<SVGElement>('.weather-panel__wind-arrow')
  expect(arrowSvg?.getAttribute('aria-hidden')).toBe('true')
  expect(container.querySelector('.weather-panel__wind-arrow g')?.getAttribute('transform')).toBe('rotate(270 24 24)')
  expect(screen.getByRole('img', { name: 'Wind from 270°, W' })).toBeTruthy()
})

test('formats last-observation age from seconds to minutes without flaky timing', () => {
  // Arrange - one observation at t=0 and explicit cursor times (no wall clock).
  const { rerender } = render(<WeatherPanel snapshot={snapshotAt(500)} weatherSidecar={singleObservation()} />)

  // Act/Assert - sub-second observations read as just now.
  expect(screen.getByText('Just now')).toBeTruthy()

  rerender(<WeatherPanel snapshot={snapshotAt(5_000)} weatherSidecar={singleObservation()} />)
  expect(screen.getByText('5s ago')).toBeTruthy()
  expect(screen.getByText('fresh, 5s ago')).toBeTruthy()

  rerender(<WeatherPanel snapshot={snapshotAt(120_000)} weatherSidecar={singleObservation()} />)
  expect(screen.getByText('2m ago')).toBeTruthy()
  expect(screen.getByText('stale, 2m ago')).toBeTruthy()
  expect(screen.getByRole('status').textContent).toContain('stale and unavailable')
    expect(screen.getAllByText('—').length).toBe(6)
  expect(screen.queryByText('21.0 °C')).toBeNull()
  expect(screen.queryByText('3.2 m/s')).toBeNull()

  rerender(<WeatherPanel snapshot={snapshotAt(125_000)} weatherSidecar={singleObservation()} />)
  expect(screen.getByText('2m 5s ago')).toBeTruthy()
})

test('keeps measurements current at exactly 90 seconds and hides them after the boundary', () => {
  // Arrange - the selector's strict boundary is exactly 90,000 ms.
  const { rerender } = render(<WeatherPanel snapshot={snapshotAt(90_000)} weatherSidecar={singleObservation()} />)

  // Assert - equality remains fresh and values are rendered.
  expect(screen.getByRole('article', { name: 'Weather' }).getAttribute('data-state')).toBe('fresh')
  expect(screen.getByText('21.0 °C')).toBeTruthy()
  expect(screen.getByText('3.2 m/s')).toBeTruthy()
  expect(screen.getByText('1m 30s ago')).toBeTruthy()

  // Act - advance one millisecond beyond the threshold.
  rerender(<WeatherPanel snapshot={snapshotAt(90_001)} weatherSidecar={singleObservation()} />)

  // Assert - stale metadata remains visible, but the old row is fail-closed.
  expect(screen.getByRole('article', { name: 'Weather' }).getAttribute('data-state')).toBe('stale')
  expect(screen.getByText('Stale')).toBeTruthy()
  expect(screen.getByText('1m 30s ago')).toBeTruthy()
    expect(screen.getAllByText('—').length).toBe(6)
  expect(screen.queryByText('21.0 °C')).toBeNull()
  expect(screen.queryByText('3.2 m/s')).toBeNull()
})

test('marks fresh observations with live status semantics', () => {
  // Arrange - a recent observation.
  render(<WeatherPanel snapshot={snapshotAt(3_500)} weatherSidecar={sidecar()} />)

  // Act - read the status chrome.

  // Assert - state attribute, visible label, and descriptive live label agree.
  const panel = screen.getByRole('article', { name: 'Weather' })
  expect(panel.getAttribute('data-state')).toBe('fresh')
  expect(screen.getByText('Fresh')).toBeTruthy()
  expect(panel.querySelector('.weather-panel__status')?.getAttribute('aria-label')).toBe('Weather observations fresh, Just now')
})

test('marks stale observations older than the staleness threshold', () => {
  // Arrange - an observation 100s old exceeds the 90s staleness threshold.
  render(<WeatherPanel snapshot={snapshotAt(100_000)} weatherSidecar={singleObservation()} />)

  // Act - read the status chrome.

  // Assert - the panel is stale and communicates the age in its live label.
  const panel = screen.getByRole('article', { name: 'Weather' })
  expect(panel.getAttribute('data-state')).toBe('stale')
  expect(screen.getByText('Stale')).toBeTruthy()
  expect(panel.querySelector('.weather-panel__status')?.getAttribute('aria-label')).toBe('Weather observations stale, 1m 40s ago')
  expect(screen.getByRole('status').textContent).toContain('stale and unavailable')
   expect(screen.getAllByText('—').length).toBe(6)
  expect(screen.queryByText('21.0 °C')).toBeNull()
  expect(screen.queryByText('3.2 m/s')).toBeNull()
})

test('renders an unavailable state when no weather sidecar exists', () => {
  // Arrange - replay without a weather sidecar.
  const { container } = render(<WeatherPanel snapshot={snapshotAt(1_000)} weatherSidecar={null} />)

  // Act - the selector cannot find observations.

  // Assert - the panel is unavailable with a live notice and placeholder measurements.
  const panel = screen.getByRole('article', { name: 'Weather' })
  expect(panel.getAttribute('data-state')).toBe('unavailable')
  expect(screen.getByText('Unavailable')).toBeTruthy()
  expect(screen.getByRole('status').textContent).toContain('Weather observations are unavailable at this replay time.')
  expect(screen.getByRole('img', { name: 'Wind direction unavailable' })).toBeTruthy()
  expect(container.querySelector('.weather-panel__wind-arrow-placeholder')).toBeTruthy()
  expect(screen.getAllByText('—').length).toBe(7)
  expect(screen.queryByText(/°C/)).toBeNull()
  expect(screen.queryByText(/m\/s/)).toBeNull()
})

test('renders an unavailable state before the first observation', () => {
  // Arrange - the replay cursor precedes the earliest weather row.
  render(<WeatherPanel snapshot={snapshotAt(1_000)} weatherSidecar={singleObservation({ timeMs: [5_000] })} />)

  // Act - the selector finds no causal observation.

  // Assert - measurements fall back to unavailable placeholders, not zeros or guesses.
  const panel = screen.getByRole('article', { name: 'Weather' })
  expect(panel.getAttribute('data-state')).toBe('unavailable')
  expect(screen.getByRole('status').textContent).toContain('Weather observations are unavailable at this replay time.')
  expect(screen.getAllByText('—').length).toBe(7)
  expect(screen.queryByText('0 °C')).toBeNull()
})

test('renders null measurements as placeholders instead of zero or guessed values', () => {
  // Arrange - an observation with every measurement missing.
  const { container } = render(<WeatherPanel snapshot={snapshotAt(1_500)} weatherSidecar={singleObservation({
    airTempC: [null],
    humidityPct: [null],
    pressureMbar: [null],
    rainfall: [null],
    trackTempC: [null],
    windDirectionDeg: [null],
    windSpeedMps: [null],
  })} />)

  // Act - the selector keeps the row but finds no usable measurement.

  // Assert - each field renders an em-dash placeholder and the arrow is dashed.
  expect(screen.getByRole('article', { name: 'Weather' }).getAttribute('data-state')).toBe('unavailable')
  expect(screen.getByRole('img', { name: 'Wind direction unavailable' })).toBeTruthy()
  expect(container.querySelector('.weather-panel__wind-arrow-placeholder')).toBeTruthy()
  expect(screen.getAllByText('—').length).toBe(6)
  expect(screen.queryByText(/°C/)).toBeNull()
  expect(screen.queryByText(/m\/s/)).toBeNull()
  expect(screen.queryByText('0')).toBeNull()
})

test('keeps usable measurements and only placeholders the missing fields', () => {
  // Arrange - latest row is missing air temperature and humidity but has wind and pressure.
  render(<WeatherPanel snapshot={snapshotAt(3_500)} weatherSidecar={sidecar({ airTempC: [19.5, 21.4, null], humidityPct: [60, 58, null] })} />)

  // Act - the selector still picks the causal row because other measurements exist.

  // Assert - missing fields show em-dashes while present fields render values.
  expect(screen.getByRole('article', { name: 'Weather' }).getAttribute('data-state')).toBe('fresh')
  expect(screen.getAllByText('—').length).toBe(2)
  expect(screen.getByText('1.013 bar')).toBeTruthy()
  expect(screen.getByText('32.4 °C')).toBeTruthy()
  expect(screen.getByRole('img', { name: 'Wind from 315°, NW' })).toBeTruthy()
})

test('exposes accessible labels and live status semantics across the panel', () => {
  // Arrange - a fresh, complete observation.
  const { container } = render(<WeatherPanel snapshot={snapshotAt(3_500)} weatherSidecar={sidecar()} />)

  // Act - inspect the semantic structure exposed to assistive technology.

  // Assert - heading anchors, labelled regions, decorative art, and live messaging.
  const panel = screen.getByRole('article', { name: 'Weather' })
  expect(panel.getAttribute('aria-labelledby')).toBe('weather-panel-title')
  expect(screen.getByRole('heading', { name: 'Weather' }).getAttribute('id')).toBe('weather-panel-title')
  expect(screen.getByRole('region', { name: 'Wind from' }).getAttribute('aria-labelledby')).toBe('weather-wind-title')
  expect(container.querySelector('dl[aria-label="Weather observations"]')).toBeTruthy()
  expect(container.querySelector('.weather-panel__wind-arrow')?.getAttribute('aria-hidden')).toBe('true')
  expect(container.querySelector('.weather-panel__status')?.getAttribute('aria-label')).toContain('Weather observations fresh')
  expect(container.querySelector('.weather-panel__update-detail')?.textContent).toBe('fresh, Just now')
})
