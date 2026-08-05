/**
 * @vitest-environment jsdom
 */
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { RaceGlobe } from '../../../src/features/race-library/RaceGlobe'
import type { CatalogV2Race } from '../../../src/data/catalog/types'

interface AnimationFrameHandle {
  readonly id: number
  readonly callback: FrameRequestCallback
}

function createSession() {
  return {
    session_code: 'R',
    session_name: 'Race',
    generation_id: '2024-round-01-session-race-mode-race',
    delivery_version: '2024-round-01-session-race-mode-race',
    outcome: 'classified',
    validated: true,
    canonical_pointer: 'canonical/race-1/sessions/r/manifest.json',
    browser_pointer: 'browser/race-1/sessions/r/browser-current.json',
  } as const
}

function createRace(overrides: Partial<CatalogV2Race> = {}): CatalogV2Race {
  return {
    race_id: 'race-1',
    round_number: 1,
    event_name: 'Bahrain Grand Prix',
    country: 'Bahrain',
    location: 'Sakhir',
    event_date: '2024-03-02',
    sessions: [createSession()],
    ...overrides,
  }
}

let frameCallbacks: AnimationFrameHandle[]
let nextFrameId: number
let performanceNowValue: number
let matchMediaMatches: boolean
let matchMediaListeners: Array<(event: MediaQueryListEvent) => void>

beforeEach(() => {
  frameCallbacks = []
  nextFrameId = 1
  performanceNowValue = 0
  matchMediaMatches = false
  matchMediaListeners = []

  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback: FrameRequestCallback): number => {
    const id = nextFrameId++
    frameCallbacks.push({ id, callback })
    return id
  })

  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id: number): void => {
    const index = frameCallbacks.findIndex((handle) => handle.id === id)
    if (index >= 0) {
      frameCallbacks.splice(index, 1)
    }
  })

  vi.spyOn(performance, 'now').mockImplementation(() => performanceNowValue)

  const mediaQueryStub = {
    get matches() {
      return matchMediaMatches
    },
    addEventListener(_event: string, handler: (event: MediaQueryListEvent) => void) {
      matchMediaListeners.push(handler)
    },
    removeEventListener(_event: string, handler: (event: MediaQueryListEvent) => void) {
      const index = matchMediaListeners.indexOf(handler)
      if (index >= 0) matchMediaListeners.splice(index, 1)
    },
  } as unknown as MediaQueryList

  const matchMediaMock = vi.fn().mockImplementation(() => mediaQueryStub)
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: matchMediaMock,
  })

  vi.spyOn(console, 'error').mockImplementation(() => undefined)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function flushAnimationFrame(elapsedMs: number): void {
  performanceNowValue += elapsedMs
  const pending = [...frameCallbacks]
  frameCallbacks = []
  for (const { callback } of pending) {
    callback(performanceNowValue)
  }
}

function flushAllFrames(totalMs: number, stepMs = 16): void {
  let remaining = totalMs
  while (remaining > 0 && frameCallbacks.length > 0) {
    const step = Math.min(stepMs, remaining)
    flushAnimationFrame(step)
    remaining -= step
  }
}

describe('RaceGlobe', () => {
  describe('accessible rendering', () => {
    test('renders an SVG with role img and an aria-label for a race with coordinates', () => {
      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      render(<RaceGlobe race={race} />)

      const svg = document.querySelector('svg.race-globe__canvas')
      expect(svg).toBeTruthy()
      expect(svg?.getAttribute('role')).toBe('img')
      expect(svg?.getAttribute('aria-label')).toContain('Bahrain Grand Prix')
    })

    test('includes a title and desc element for screen readers', () => {
      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      render(<RaceGlobe race={race} />)

      const title = document.querySelector('svg title')
      const desc = document.querySelector('svg desc')
      expect(title).toBeTruthy()
      expect(title?.textContent).toContain('Bahrain Grand Prix')
      expect(desc).toBeTruthy()
      expect(desc?.textContent).toContain('26.0325')
      expect(desc?.textContent).toContain('50.5106')
    })

    test('does not add a keyboard tab stop to the globe SVG', () => {
      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      render(<RaceGlobe race={race} />)

      const svg = document.querySelector('svg.race-globe__canvas')
      expect(svg?.getAttribute('tabindex')).toBeNull()
    })

    test('sets preserveAspectRatio on the SVG for responsive scaling', () => {
      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      render(<RaceGlobe race={race} />)

      const svg = document.querySelector('svg.race-globe__canvas')
      expect(svg?.getAttribute('preserveAspectRatio')).toBe('xMidYMid meet')
    })

    test('includes a descriptive desc mentioning unavailable coordinates for placeholder', () => {
      render(<RaceGlobe race={null} />)

      const desc = document.querySelector('svg desc')
      expect(desc?.textContent).toContain('Location coordinates are not available')
    })
  })

  describe('geometry rendering', () => {
    test('renders the geographic map on a canvas beneath the SVG overlay', () => {
      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      render(<RaceGlobe race={race} />)

      const map = document.querySelector('canvas.race-globe__map')
      expect(map).toBeTruthy()
      expect(map?.getAttribute('aria-hidden')).toBe('true')
      expect(map?.getAttribute('width')).toBe('800')
      expect(map?.getAttribute('height')).toBe('800')
    })

    test('renders the sphere background circle', () => {
      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      render(<RaceGlobe race={race} />)

      const sphere = document.querySelector('.race-globe__sphere')
      expect(sphere).toBeTruthy()
      expect(sphere?.tagName.toLowerCase()).toBe('circle')
    })

    test('renders a marker at projected coordinates for a race with valid visual data', () => {
      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      render(<RaceGlobe race={race} />)

      const marker = document.querySelector('.race-globe__marker')
      expect(marker).toBeTruthy()
      expect(marker?.tagName.toLowerCase()).toBe('circle')
    })
  })

  describe('placeholder state', () => {
    test('renders a placeholder when race is null', () => {
      render(<RaceGlobe race={null} />)

      expect(document.querySelector('.race-globe--placeholder')).toBeTruthy()
      expect(screen.getByText('Select a race to see its location on the globe.')).toBeTruthy()
    })

    test('renders a placeholder when race has no visual metadata', () => {
      const race = createRace()
      render(<RaceGlobe race={race} />)

      expect(document.querySelector('.race-globe--placeholder')).toBeTruthy()
      expect(screen.getByText('Location data is not available for this race.')).toBeTruthy()
    })

    test('renders a placeholder when coordinates are out of range', () => {
      const race = createRace({
        visual: { latitude: 200, longitude: 50 },
      })
      render(<RaceGlobe race={race} />)

      expect(document.querySelector('.race-globe--placeholder')).toBeTruthy()
      expect(screen.getByText('Location data is not available for this race.')).toBeTruthy()
    })

    test('does not render a marker in the placeholder state', () => {
      render(<RaceGlobe race={null} />)

      expect(document.querySelector('.race-globe__marker')).toBeNull()
    })

    test('still renders the geographic map in placeholder state', () => {
      render(<RaceGlobe race={null} />)

      expect(document.querySelector('canvas.race-globe__map')).toBeTruthy()
    })

    test('transitions from valid coordinates to placeholder when race becomes null', () => {
      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      const { rerender } = render(<RaceGlobe race={race} />)

      expect(document.querySelector('.race-globe__marker')).toBeTruthy()
      expect(document.querySelector('.race-globe--placeholder')).toBeNull()

      act(() => {
        rerender(<RaceGlobe race={null} />)
      })

      expect(document.querySelector('.race-globe__marker')).toBeNull()
      expect(document.querySelector('.race-globe--placeholder')).toBeTruthy()
      expect(screen.getByText('Select a race to see its location on the globe.')).toBeTruthy()
    })

    test('transitions from placeholder to valid coordinates when race is selected', () => {
      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      const { rerender } = render(<RaceGlobe race={null} />)

      expect(document.querySelector('.race-globe--placeholder')).toBeTruthy()
      expect(document.querySelector('.race-globe__marker')).toBeNull()

      act(() => {
        rerender(<RaceGlobe race={race} />)
      })

      expect(document.querySelector('.race-globe--placeholder')).toBeNull()
      expect(document.querySelector('.race-globe__marker')).toBeTruthy()
    })
  })

  describe('animation behaviour', () => {
    test('does not schedule an animation on initial mount', () => {
      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      render(<RaceGlobe race={race} />)

      expect(window.requestAnimationFrame).not.toHaveBeenCalled()
    })

    test('schedules a requestAnimationFrame when a new race with coordinates is selected', () => {
      const { rerender } = render(<RaceGlobe race={null} />)
      vi.mocked(window.requestAnimationFrame).mockClear()

      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })

      act(() => {
        rerender(<RaceGlobe race={race} />)
      })

      expect(window.requestAnimationFrame).toHaveBeenCalled()
    })

    test('schedules a new animation when the selected race changes', () => {
      const raceA = createRace({
        race_id: 'race-a',
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      const { rerender } = render(<RaceGlobe race={raceA} />)

      flushAllFrames(500)
      vi.mocked(window.requestAnimationFrame).mockClear()

      const raceB = createRace({
        race_id: 'race-b',
        event_name: 'Monaco Grand Prix',
        visual: { latitude: 43.7389, longitude: 7.4194 },
      })

      act(() => {
        rerender(<RaceGlobe race={raceB} />)
      })

      expect(window.requestAnimationFrame).toHaveBeenCalled()
    })

    test('draws and erases a clipped geographic journey while travelling to the next race', () => {
      const raceA = createRace({
        race_id: 'race-a',
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      const { rerender } = render(<RaceGlobe race={raceA} />)
      const initialMarker = document.querySelector('.race-globe__marker')
      const initialMarkerPosition = {
        cx: initialMarker?.getAttribute('cx'),
        cy: initialMarker?.getAttribute('cy'),
      }
      const raceB = createRace({
        race_id: 'race-b',
        event_name: 'Monaco Grand Prix',
        visual: { latitude: 43.7389, longitude: 7.4194 },
      })

      act(() => {
        rerender(<RaceGlobe race={raceB} />)
      })

      const journey = document.querySelector('.race-globe__journey')
      expect(journey).toBeTruthy()
      expect(journey?.getAttribute('d')).toBeTruthy()
      expect(journey?.getAttribute('pathLength')).toBeNull()
      expect(journey?.getAttribute('clip-path')).toBe('url(#race-globe-sphere-clip)')
      expect(document.querySelector('.race-globe--animating')).toBeTruthy()
      expect(document.querySelector('.race-globe__marker')?.getAttribute('cx')).toBe(initialMarkerPosition.cx)
      expect(document.querySelector('.race-globe__marker')?.getAttribute('cy')).toBe(initialMarkerPosition.cy)
      const initialJourneyPath = journey?.getAttribute('d')

      act(() => {
        flushAllFrames(200)
      })

      expect(journey?.getAttribute('d')).toBe(initialJourneyPath)

      act(() => {
        flushAllFrames(550)
      })

      expect(journey?.getAttribute('d')).not.toBe(initialJourneyPath)

      act(() => {
        flushAllFrames(500)
      })

      expect(document.querySelector('.race-globe__journey')).toBeNull()
      expect(document.querySelector('.race-globe--animating')).toBeNull()
    })

    test('cancels race B when race C is selected before race B completes', () => {
      const raceA = createRace({
        race_id: 'race-a',
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      const { rerender } = render(<RaceGlobe race={raceA} />)

      expect(frameCallbacks.length).toBe(0)

      const raceB = createRace({
        race_id: 'race-b',
        event_name: 'Monaco Grand Prix',
        visual: { latitude: 43.7389, longitude: 7.4194 },
      })

      act(() => {
        rerender(<RaceGlobe race={raceB} />)
      })

      expect(frameCallbacks.length).toBe(1)
      const raceBFrameId = frameCallbacks[0]?.id ?? 0

      const raceC = createRace({
        race_id: 'race-c',
        event_name: 'Silverstone Grand Prix',
        visual: { latitude: 52.0786, longitude: -1.0169 },
      })

      act(() => {
        rerender(<RaceGlobe race={raceC} />)
      })

      expect(window.cancelAnimationFrame).toHaveBeenCalledWith(raceBFrameId)
      expect(frameCallbacks.length).toBe(1)
    })

    test('cancels the pending animation frame on unmount', () => {
      const raceA = createRace({
        race_id: 'race-a',
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      const { rerender, unmount } = render(<RaceGlobe race={raceA} />)

      const raceB = createRace({
        race_id: 'race-b',
        event_name: 'Monaco Grand Prix',
        visual: { latitude: 43.7389, longitude: 7.4194 },
      })

      act(() => {
        rerender(<RaceGlobe race={raceB} />)
      })

      expect(frameCallbacks.length).toBeGreaterThan(0)

      unmount()

      expect(frameCallbacks.length).toBe(0)
    })

    test('completes the animation within the configured duration', () => {
      const raceA = createRace({
        race_id: 'race-a',
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      const { rerender } = render(<RaceGlobe race={raceA} />)

      flushAllFrames(1_300)
      vi.mocked(window.requestAnimationFrame).mockClear()

      const raceB = createRace({
        race_id: 'race-b',
        event_name: 'Monaco Grand Prix',
        visual: { latitude: 43.7389, longitude: 7.4194 },
      })

      act(() => {
        rerender(<RaceGlobe race={raceB} />)
      })

      flushAllFrames(1_300)

      expect(frameCallbacks.length).toBe(0)
    })

    test('does not schedule animation when coordinates are unchanged between selections', () => {
      const visual = { latitude: 26.0325, longitude: 50.5106 }
      const raceA = createRace({ race_id: 'race-a', visual })
      const { rerender } = render(<RaceGlobe race={raceA} />)

      flushAllFrames(500)
      vi.mocked(window.requestAnimationFrame).mockClear()

      const raceB = createRace({ race_id: 'race-b', visual })

      act(() => {
        rerender(<RaceGlobe race={raceB} />)
      })

      expect(window.requestAnimationFrame).not.toHaveBeenCalled()
    })

    test('updates the aria-label to reflect the newly selected race name', () => {
      const raceA = createRace({
        race_id: 'race-a',
        event_name: 'Bahrain Grand Prix',
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      const { rerender } = render(<RaceGlobe race={raceA} />)

      const svg = document.querySelector('svg.race-globe__canvas')
      expect(svg?.getAttribute('aria-label')).toContain('Bahrain Grand Prix')

      const raceB = createRace({
        race_id: 'race-b',
        event_name: 'Monaco Grand Prix',
        visual: { latitude: 43.7389, longitude: 7.4194 },
      })

      act(() => {
        rerender(<RaceGlobe race={raceB} />)
      })

      expect(svg?.getAttribute('aria-label')).toContain('Monaco Grand Prix')
      expect(svg?.getAttribute('aria-label')).not.toContain('Bahrain Grand Prix')
    })

    test('desc reflects placeholder text when race has no visual metadata', () => {
      const race = createRace({ event_name: 'Bahrain Grand Prix' })
      render(<RaceGlobe race={race} />)

      const desc = document.querySelector('svg desc')
      expect(desc?.textContent).toContain('Location coordinates are not available')
    })
  })

  describe('reduced motion', () => {
    test('snaps to the target rotation without scheduling a frame when reduced motion is preferred', () => {
      matchMediaMatches = true

      const { rerender } = render(<RaceGlobe race={null} />)
      vi.mocked(window.requestAnimationFrame).mockClear()

      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })

      act(() => {
        rerender(<RaceGlobe race={race} />)
      })

      expect(window.requestAnimationFrame).not.toHaveBeenCalled()
      expect(document.querySelector('.race-globe__marker')).toBeTruthy()
    })

    test('cleans up the prefers-reduced-motion listener on unmount', () => {
      const { unmount } = render(<RaceGlobe race={null} />)

      expect(matchMediaListeners.length).toBe(1)

      unmount()

      expect(matchMediaListeners.length).toBe(0)
    })

    test('snaps to target rotation when reduced motion is enabled mid-lifecycle via change event', () => {
      const raceA = createRace({
        race_id: 'race-a',
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      const { rerender } = render(<RaceGlobe race={raceA} />)
      flushAllFrames(500)

      // Dynamically enable reduced motion through the change listener
      act(() => {
        for (const listener of matchMediaListeners) {
          listener({ matches: true } as MediaQueryListEvent)
        }
      })

      vi.mocked(window.requestAnimationFrame).mockClear()

      const raceB = createRace({
        race_id: 'race-b',
        event_name: 'Monaco Grand Prix',
        visual: { latitude: 43.7389, longitude: 7.4194 },
      })

      act(() => {
        rerender(<RaceGlobe race={raceB} />)
      })

      expect(window.requestAnimationFrame).not.toHaveBeenCalled()
      expect(document.querySelector('.race-globe__marker')).toBeTruthy()
    })

    test('stops an active journey when reduced motion is enabled', () => {
      const raceA = createRace({
        race_id: 'race-a',
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      const { rerender } = render(<RaceGlobe race={raceA} />)
      const raceB = createRace({
        race_id: 'race-b',
        event_name: 'Monaco Grand Prix',
        visual: { latitude: 43.7389, longitude: 7.4194 },
      })
      act(() => {
        rerender(<RaceGlobe race={raceB} />)
      })
      expect(window.requestAnimationFrame).toHaveBeenCalled()

      act(() => {
        matchMediaMatches = true
        for (const listener of matchMediaListeners) {
          listener({ matches: true } as MediaQueryListEvent)
        }
      })

      expect(window.cancelAnimationFrame).toHaveBeenCalled()
      expect(document.querySelector('.race-globe__marker')).toBeTruthy()
      expect(document.querySelector('.race-globe__journey')).toBeNull()
    })

    test('resumes animation when reduced motion is disabled mid-lifecycle via change event', () => {
      matchMediaMatches = true

      const raceA = createRace({
        race_id: 'race-a',
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      const { rerender } = render(<RaceGlobe race={raceA} />)

      // Dynamically disable reduced motion
      act(() => {
        matchMediaMatches = false
        for (const listener of matchMediaListeners) {
          listener({ matches: false } as MediaQueryListEvent)
        }
      })

      vi.mocked(window.requestAnimationFrame).mockClear()

      const raceB = createRace({
        race_id: 'race-b',
        event_name: 'Monaco Grand Prix',
        visual: { latitude: 43.7389, longitude: 7.4194 },
      })

      act(() => {
        rerender(<RaceGlobe race={raceB} />)
      })

      expect(window.requestAnimationFrame).toHaveBeenCalled()
    })
  })

  describe('BEM class structure', () => {
    test('applies the expected BEM classes to all structural elements', () => {
      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      render(<RaceGlobe race={race} />)

      expect(document.querySelector('.race-globe')).toBeTruthy()
      expect(document.querySelector('.race-globe__map')).toBeTruthy()
      expect(document.querySelector('.race-globe__canvas')).toBeTruthy()
      expect(document.querySelector('.race-globe__sphere')).toBeTruthy()
      expect(document.querySelector('.race-globe__marker')).toBeTruthy()
    })

    test('uses the placeholder modifier when visual metadata is absent', () => {
      render(<RaceGlobe race={null} />)

      expect(document.querySelector('.race-globe--placeholder')).toBeTruthy()
      expect(document.querySelector('.race-globe__placeholder-message')).toBeTruthy()
    })

    test('omits the placeholder modifier when a race with valid coordinates is provided', () => {
      const race = createRace({
        visual: { latitude: 26.0325, longitude: 50.5106 },
      })
      render(<RaceGlobe race={race} />)

      expect(document.querySelector('.race-globe--placeholder')).toBeNull()
    })
  })
})
