import { useCallback, useEffect, useState } from 'react'
import { resolveSessionBrowserPointer, selectReplaySession } from '../data/catalog/guards'
import type { CatalogSelection, CatalogV2 } from '../data/catalog/types'
import { RaceLibraryPage } from '../features/race-library/RaceLibraryPage'
import { useSeasonCatalog } from '../features/race-library/useSeasonCatalog'
import { ReplayEntry } from '../features/replay/entry/ReplayEntry'
import { useUrlSelection } from '../url/useUrlSelection'
import type { UrlSelection } from '../url/adapter'
import { runPageTransition } from './page-transition'

const DEFAULT_YEAR = 2026
const DEFAULT_AVAILABLE_YEARS = [DEFAULT_YEAR] as const
const DEFAULT_SEASONS_BASE_URL = '/replay-data/seasons/'
const INVALID_SELECTION_MESSAGE = 'Choose a listed race and a session marked “Ready to replay”.'

interface ReplayTarget {
  readonly selection: CatalogSelection
  readonly browserBaseUrl: string
  readonly browserPointerPath: string
}

interface LocalSelection {
  readonly year: number
  readonly race: string | null
  readonly session: string | null
}

export default function App() {
  const seasonsBaseUrl = import.meta.env.VITE_REPLAY_SEASONS_BASE_URL ?? DEFAULT_SEASONS_BASE_URL
  const transitionPopState = useCallback((selection: UrlSelection | null, update: () => void) => {
    runPageTransition(isCompleteSelection(selection) ? 'forward' : 'backward', update)
  }, [])
  const [urlSelection, setUrlSelection] = useUrlSelection(transitionPopState)
  const [localSelection, setLocalSelection] = useState<LocalSelection>(() => toLocalSelection(urlSelection))
  const requestedYear = urlSelection?.year ?? localSelection.year
  const availableYears = resolveAvailableYears(import.meta.env.VITE_REPLAY_SEASON_YEARS, requestedYear)
  const { catalog: loadedCatalog, source, isLoading, error, retry } = useSeasonCatalog({ seasonsBaseUrl, year: requestedYear })
  const catalog = loadedCatalog?.year === requestedYear ? loadedCatalog : null
  const urlReplayTarget = resolveReplayTarget(catalog, urlSelection, seasonsBaseUrl)
  const localReplayTarget = resolveReplayTarget(catalog, localSelection, seasonsBaseUrl)
  const hasUnavailableUrlSelection = isCompleteSelection(urlSelection) && urlReplayTarget === null

  useEffect(() => {
    if (urlSelection !== null) setLocalSelection(toLocalSelection(urlSelection))
  }, [urlSelection])

  function handleSelectRace(raceId: string | null): void {
    const updateSelection = () => setLocalSelection((current) => ({ ...current, race: raceId, session: null }))
    const changesPage = (localSelection.race === null) !== (raceId === null)
    if (!changesPage) {
      updateSelection()
      return
    }
    runPageTransition(raceId === null ? 'backward' : 'forward', updateSelection)
  }

  function handleSelectYear(year: number): void {
    setLocalSelection({ year, race: null, session: null })
  }

  function handleSelectSession(sessionCode: string | null): void {
    setLocalSelection((current) => ({ ...current, session: sessionCode }))
  }

  function handleOpenWorkspace(): void {
    if (localReplayTarget === null) return
    runPageTransition('forward', () => {
      setUrlSelection({
        year: catalog?.year ?? localSelection.year,
        race: localReplayTarget.selection.race.race_id,
        session: localReplayTarget.selection.session.session_code,
      })
    })
  }

  function handleChangeSession(): void {
    runPageTransition('backward', () => {
      if (urlSelection !== null) setLocalSelection(toLocalSelection(urlSelection))
      setUrlSelection(null)
    })
  }

  if (urlReplayTarget !== null) {
    return (
      <ReplayEntry
        browserBaseUrl={urlReplayTarget.browserBaseUrl}
        browserPointerPath={urlReplayTarget.browserPointerPath}
        onChangeSession={handleChangeSession}
      />
    )
  }

  return (
    <RaceLibraryPage
      catalog={catalog}
      source={source}
      availableYears={availableYears}
      selectedYear={requestedYear}
      selectedRaceId={localSelection.race}
      selectedSessionCode={localSelection.session}
      isLoading={isLoading}
      error={error}
      onSelectYear={handleSelectYear}
      onSelectRace={handleSelectRace}
      onSelectSession={handleSelectSession}
      onOpenWorkspace={handleOpenWorkspace}
      onRetry={retry}
      selectionError={catalog !== null && (urlSelection?.isMalformed === true || hasUnavailableUrlSelection)
        ? INVALID_SELECTION_MESSAGE
        : undefined}
    />
  )
}

function resolveReplayTarget(
  catalog: CatalogV2 | null,
  selection: UrlSelection | LocalSelection | null,
  seasonsBaseUrl: string,
): ReplayTarget | null {
  if (catalog === null || !isCompleteSelection(selection) || selection.year !== catalog.year) return null

  const replaySelection = selectReplaySession(catalog, selection.race, selection.session)
  if (replaySelection === null || replaySelection.session.browser_pointer === null) return null

  try {
    const pointer = resolveSessionBrowserPointer(
      replaySelection.session.browser_pointer,
      replaySelection.race.race_id,
      replaySelection.session.session_code,
    )
    return {
      selection: replaySelection,
      browserBaseUrl: `${withTrailingSlash(seasonsBaseUrl)}${catalog.year}/${pointer.browserBasePath}/`,
      browserPointerPath: pointer.pointerPath,
    }
  } catch {
    return null
  }
}

function isCompleteSelection(selection: UrlSelection | LocalSelection | null): selection is {
  readonly year: number
  readonly race: string
  readonly session: string
} {
  return selection !== null
    && typeof selection.year === 'number'
    && typeof selection.race === 'string'
    && typeof selection.session === 'string'
}

function toLocalSelection(selection: UrlSelection | null): LocalSelection {
  return {
    year: selection?.year ?? DEFAULT_YEAR,
    race: selection?.race ?? null,
    session: selection?.session ?? null,
  }
}

function withTrailingSlash(value: string): string {
  return value.endsWith('/') ? value : `${value}/`
}

function resolveAvailableYears(configuredYears: string | undefined, requestedYear: number): readonly number[] {
  const parsedYears = configuredYears?.split(',')
    .map((value) => Number.parseInt(value.trim(), 10))
    .filter((value) => Number.isInteger(value) && value > 0) ?? []
  return [...new Set([...DEFAULT_AVAILABLE_YEARS, ...parsedYears, requestedYear])].sort((left, right) => right - left)
}
