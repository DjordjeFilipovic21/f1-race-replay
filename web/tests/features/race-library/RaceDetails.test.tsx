/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { RaceDetails } from '../../../src/features/race-library/RaceDetails'
import { RaceLibraryPage } from '../../../src/features/race-library/RaceLibraryPage'
import type { CatalogV2Race } from '../../../src/data/catalog/types'
import type { ReplaySource } from '../../../src/data/replay/types'
import { loadCircuitPreview } from '../../../src/geo/circuit-preview'

vi.mock('../../../src/geo/circuit-preview', () => ({
  loadCircuitPreview: vi.fn(),
}))

const mockLoadCircuitPreview = vi.mocked(loadCircuitPreview)

function createRace(raceId: string, eventName: string, visual?: CatalogV2Race['visual']): CatalogV2Race {
  return {
    race_id: raceId,
    round_number: raceId === 'race-1' ? 1 : 2,
    event_name: eventName,
    sessions: [{
      session_code: 'r',
      session_name: 'Race',
      generation_id: 'generation-1',
      delivery_version: 'v1',
      outcome: 'classified',
      validated: true,
      canonical_pointer: 'canonical/manifest.json',
      browser_pointer: 'browser/manifest.json',
    }],
    ...(visual === undefined ? {} : { visual }),
  }
}

function createSource(): ReplaySource {
  return { read: vi.fn() }
}

function createPageCatalog(races: readonly CatalogV2Race[]) {
  return { schemaVersion: 2 as const, year: 2024, atomicAcrossRaces: false, races }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('RaceDetails visual integration', () => {
  test('renders the globe and circuit preview before sessions with the explicit season source', async () => {
    const source = createSource()
    mockLoadCircuitPreview.mockResolvedValue({ pathData: 'M 0 0 L 1 1', viewBox: '0 0 1 1' })

    render(
      <RaceDetails
        race={createRace('race-1', 'Bahrain Grand Prix', {
          latitude: 26.0325,
          longitude: 50.5106,
          circuitPreview: 'visuals/bahrain.json',
        })}
        source={source}
        selectedSessionCode={null}
        onSelectSession={vi.fn()}
        canOpenWorkspace={false}
        onOpenWorkspace={vi.fn()}
      />,
    )

    expect(document.querySelector('.race-globe')).toBeTruthy()
    expect(document.querySelector('.circuit-preview')).toBeTruthy()
    expect(document.querySelector('.library-details__visuals')?.compareDocumentPosition(
      screen.getByRole('radiogroup', { name: 'Available sessions' }),
    )).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    await waitFor(() => expect(mockLoadCircuitPreview).toHaveBeenCalledWith(source, 'visuals/bahrain.json'))
  })

  test('renders no visual section when a race has no visual metadata', () => {
    render(
      <RaceDetails
        race={createRace('race-1', 'Bahrain Grand Prix')}
        source={createSource()}
        selectedSessionCode={null}
        onSelectSession={vi.fn()}
        canOpenWorkspace={false}
        onOpenWorkspace={vi.fn()}
      />,
    )

    expect(document.querySelector('.library-details__visuals')).toBeNull()
    expect(document.querySelector('.race-globe')).toBeNull()
    expect(mockLoadCircuitPreview).not.toHaveBeenCalled()
  })

  test('keeps a globe-only layout when coordinates have no circuit pointer', () => {
    render(
      <RaceDetails
        race={createRace('race-1', 'Bahrain Grand Prix', { latitude: 26.0325, longitude: 50.5106 })}
        source={createSource()}
        selectedSessionCode={null}
        onSelectSession={vi.fn()}
        canOpenWorkspace={false}
        onOpenWorkspace={vi.fn()}
      />,
    )

    expect(document.querySelector('.library-details__visuals')).toBeTruthy()
    expect(document.querySelector('.race-globe')).toBeTruthy()
    expect(document.querySelector('.circuit-preview')).toBeNull()
  })

  test('loads only the selected race preview as selection changes', async () => {
    const source = createSource()
    const firstRace = createRace('race-1', 'Bahrain Grand Prix', {
      latitude: 26.0325,
      longitude: 50.5106,
      circuitPreview: 'visuals/bahrain.json',
    })
    const secondRace = createRace('race-2', 'Monaco Grand Prix', {
      latitude: 43.7389,
      longitude: 7.4194,
      circuitPreview: 'visuals/monaco.json',
    })
    mockLoadCircuitPreview.mockResolvedValue({ pathData: 'M 0 0 L 1 1', viewBox: '0 0 1 1' })

    function StatefulPage() {
      const [selectedRaceId, setSelectedRaceId] = useState<string | null>(null)
      return (
        <RaceLibraryPage
          catalog={createPageCatalog([firstRace, secondRace])}
          source={source}
          selectedRaceId={selectedRaceId}
          selectedSessionCode={null}
          isLoading={false}
          error={null}
          onSelectRace={setSelectedRaceId}
          onSelectSession={vi.fn()}
          onOpenWorkspace={vi.fn()}
        />
      )
    }

    render(<StatefulPage />)
    fireEvent.click(screen.getByRole('button', { name: /Bahrain Grand Prix/ }))
    await waitFor(() => expect(mockLoadCircuitPreview).toHaveBeenCalledWith(source, 'visuals/bahrain.json'))
    expect(mockLoadCircuitPreview).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: /Monaco Grand Prix/ }))
    await waitFor(() => expect(mockLoadCircuitPreview).toHaveBeenLastCalledWith(source, 'visuals/monaco.json'))
    expect(mockLoadCircuitPreview).toHaveBeenCalledTimes(2)
  })
})
