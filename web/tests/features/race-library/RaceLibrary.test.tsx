/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { RaceLibraryPage } from '../../../src/features/race-library/RaceLibraryPage'
import type { CatalogV2, CatalogV2Race, CatalogV2Session } from '../../../src/data/catalog/types'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
})

function createSession(overrides: Partial<CatalogV2Session> = {}): CatalogV2Session {
  return {
    session_code: 'r',
    session_name: 'Race',
    generation_id: 'gen-1',
    delivery_version: 'v1',
    outcome: 'classified',
    validated: true,
    canonical_pointer: 'canonical/race-1/sessions/r/manifest.json',
    browser_pointer: 'browser/race-1/sessions/r/browser-current.json',
    ...overrides,
  }
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

function createCatalog(overrides: Partial<CatalogV2> = {}): CatalogV2 {
  return {
    schemaVersion: 2,
    year: 2024,
    atomicAcrossRaces: false,
    races: [createRace()],
    ...overrides,
  }
}

describe('RaceLibraryPage', () => {
  test('renders loading state with status message', () => {
    render(
      <RaceLibraryPage
        catalog={null}
        selectedRaceId={null}
        selectedSessionCode={null}
        isLoading={true}
        error={null}
        onSelectRace={vi.fn()}
        onSelectSession={vi.fn()}
        onOpenWorkspace={vi.fn()}
      />
    )

    expect(screen.getByRole('status', { name: 'Loading Season' })).toBeTruthy()
    expect(screen.getByText('Fetching the race catalog…')).toBeTruthy()
  })

  test('renders error state with actionable message and retry button', () => {
    const onRetry = vi.fn()
    render(
      <RaceLibraryPage
        catalog={null}
        selectedRaceId={null}
        selectedSessionCode={null}
        isLoading={false}
        error={new Error('Network timeout')}
        onSelectRace={vi.fn()}
        onSelectSession={vi.fn()}
        onOpenWorkspace={vi.fn()}
        onRetry={onRetry}
      />
    )

    expect(screen.getByRole('alert', { name: 'Unable to Load Season' })).toBeTruthy()
    expect(screen.getByText('Network timeout')).toBeTruthy()
    const retryButton = screen.getByRole('button', { name: 'Retry' })
    expect(retryButton).toBeTruthy()
    
    fireEvent.click(retryButton)
    expect(onRetry).toHaveBeenCalledOnce()
  })

  test('renders empty state when catalog has no races', () => {
    const emptyCatalog = createCatalog({ races: [] })
    render(
      <RaceLibraryPage
        catalog={emptyCatalog}
        selectedRaceId={null}
        selectedSessionCode={null}
        isLoading={false}
        error={null}
        onSelectRace={vi.fn()}
        onSelectSession={vi.fn()}
        onOpenWorkspace={vi.fn()}
      />
    )

    expect(screen.getByRole('status', { name: 'No Races Available' })).toBeTruthy()
    expect(screen.getByText('This season has no races in the catalog yet.')).toBeTruthy()
  })

  test('displays season year in header when catalog is loaded', () => {
    const catalog = createCatalog()
    render(
      <RaceLibraryPage
        catalog={catalog}
        selectedRaceId={null}
        selectedSessionCode={null}
        isLoading={false}
        error={null}
        onSelectRace={vi.fn()}
        onSelectSession={vi.fn()}
        onOpenWorkspace={vi.fn()}
      />
    )

    expect(screen.getByText('Season 2024')).toBeTruthy()
    expect(screen.getByText('2024 Races')).toBeTruthy()
    expect(screen.getByText('1 event')).toBeTruthy()
  })

  test('renders race list with catalog data', () => {
    const catalog = createCatalog()
    render(
      <RaceLibraryPage
        catalog={catalog}
        selectedRaceId={null}
        selectedSessionCode={null}
        isLoading={false}
        error={null}
        onSelectRace={vi.fn()}
        onSelectSession={vi.fn()}
        onOpenWorkspace={vi.fn()}
      />
    )

    expect(screen.getByRole('list', { name: 'Season races' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Bahrain Grand Prix/ })).toBeTruthy()
    expect(screen.getByText('R1')).toBeTruthy()
    expect(screen.getByText('Bahrain')).toBeTruthy()
  })

  test('calls onSelectRace when a race is clicked', () => {
    const onSelectRace = vi.fn()
    const catalog = createCatalog()
    render(
      <RaceLibraryPage
        catalog={catalog}
        selectedRaceId={null}
        selectedSessionCode={null}
        isLoading={false}
        error={null}
        onSelectRace={onSelectRace}
        onSelectSession={vi.fn()}
        onOpenWorkspace={vi.fn()}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /Bahrain Grand Prix/ }))
    expect(onSelectRace).toHaveBeenCalledWith('race-1')
  })

  test('expands selected race to show sessions', () => {
    const catalog = createCatalog()
    render(
      <RaceLibraryPage
        catalog={catalog}
        selectedRaceId="race-1"
        selectedSessionCode={null}
        isLoading={false}
        error={null}
        onSelectRace={vi.fn()}
        onSelectSession={vi.fn()}
        onOpenWorkspace={vi.fn()}
      />
    )

    const raceTrigger = screen.getByRole('button', { name: /Bahrain Grand Prix/ })
    expect(raceTrigger.getAttribute('aria-expanded')).toBe('true')
    const detailsId = raceTrigger.getAttribute('aria-controls')
    expect(detailsId).toBeTruthy()
    expect(document.getElementById(detailsId ?? '')?.getAttribute('role')).toBe('region')
    const raceListItem = raceTrigger.closest('[role="listitem"]')
    expect(raceListItem?.hasAttribute('aria-selected')).toBe(false)
    expect(raceListItem?.hasAttribute('aria-expanded')).toBe(false)
    expect(screen.getByRole('radiogroup', { name: 'Available sessions' })).toBeTruthy()
    expect(screen.getByRole('radio', { name: /Race/ })).toBeTruthy()
  })

  test('calls onSelectSession when a session is clicked', () => {
    const onSelectSession = vi.fn()
    const catalog = createCatalog()
    render(
      <RaceLibraryPage
        catalog={catalog}
        selectedRaceId="race-1"
        selectedSessionCode={null}
        isLoading={false}
        error={null}
        onSelectRace={vi.fn()}
        onSelectSession={onSelectSession}
        onOpenWorkspace={vi.fn()}
      />
    )

    fireEvent.click(screen.getByRole('radio', { name: /Race/ }))
    expect(onSelectSession).toHaveBeenCalledWith('r')
  })

  test('disables Open Workspace button when no valid session is selected', () => {
    const catalog = createCatalog()
    render(
      <RaceLibraryPage
        catalog={catalog}
        selectedRaceId="race-1"
        selectedSessionCode={null}
        isLoading={false}
        error={null}
        onSelectRace={vi.fn()}
        onSelectSession={vi.fn()}
        onOpenWorkspace={vi.fn()}
      />
    )

    const openButton = screen.getByRole('button', { name: 'Open replay workspace' })
    expect((openButton as HTMLButtonElement).disabled).toBe(true)
  })

  test('enables Open Workspace button when a valid session is selected', () => {
    const catalog = createCatalog()
    render(
      <RaceLibraryPage
        catalog={catalog}
        selectedRaceId="race-1"
        selectedSessionCode="r"
        isLoading={false}
        error={null}
        onSelectRace={vi.fn()}
        onSelectSession={vi.fn()}
        onOpenWorkspace={vi.fn()}
      />
    )

    const openButton = screen.getByRole('button', { name: 'Open replay workspace' })
    expect((openButton as HTMLButtonElement).disabled).toBe(false)
  })

  test('calls onOpenWorkspace when Open Workspace button is clicked', () => {
    const onOpenWorkspace = vi.fn()
    const catalog = createCatalog()
    render(
      <RaceLibraryPage
        catalog={catalog}
        selectedRaceId="race-1"
        selectedSessionCode="r"
        isLoading={false}
        error={null}
        onSelectRace={vi.fn()}
        onSelectSession={vi.fn()}
        onOpenWorkspace={onOpenWorkspace}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open replay workspace' }))
    expect(onOpenWorkspace).toHaveBeenCalledOnce()
  })

  test('disables non-replay-ready sessions with explanation', () => {
    const unvalidatedSession = createSession({
      session_code: 'FP1',
      session_name: 'Practice 1',
      validated: false,
      generation_id: null,
      delivery_version: null,
      canonical_pointer: null,
      browser_pointer: null,
    })
    const catalog = createCatalog({
      races: [createRace({ sessions: [unvalidatedSession] })],
    })
    render(
      <RaceLibraryPage
        catalog={catalog}
        selectedRaceId="race-1"
        selectedSessionCode={null}
        isLoading={false}
        error={null}
        onSelectRace={vi.fn()}
        onSelectSession={vi.fn()}
        onOpenWorkspace={vi.fn()}
      />
    )

    const sessionButton = screen.getByRole('radio', { name: /Practice 1.*not yet available/ })
    expect((sessionButton as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('Awaiting validation')).toBeTruthy()
  })

  test('collapses race when clicking selected race again', () => {
    const onSelectRace = vi.fn()
    const onSelectSession = vi.fn()
    const catalog = createCatalog()
    render(
      <RaceLibraryPage
        catalog={catalog}
        selectedRaceId="race-1"
        selectedSessionCode="r"
        isLoading={false}
        error={null}
        onSelectRace={onSelectRace}
        onSelectSession={onSelectSession}
        onOpenWorkspace={vi.fn()}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: /Bahrain Grand Prix/ }))
    expect(onSelectRace).toHaveBeenCalledWith(null)
    expect(onSelectSession).toHaveBeenCalledWith(null)
  })
})
