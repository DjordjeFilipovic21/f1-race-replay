import { useEffect, useState } from 'react'
import { resolveSessionBrowserPointer, selectReplaySession } from '../data/catalog/guards'
import type { CatalogSelection, CatalogV2 } from '../data/catalog/types'
import { RaceLibraryPage } from '../features/race-library/RaceLibraryPage'
import { useSeasonCatalog } from '../features/race-library/useSeasonCatalog'
import { ReplayEntry } from '../features/replay/entry/ReplayEntry'
import { useUrlSelection } from '../url/useUrlSelection'
import type { UrlSelection } from '../url/adapter'

const DEFAULT_YEAR = 2024
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
  const [urlSelection, setUrlSelection] = useUrlSelection()
  const [localSelection, setLocalSelection] = useState<LocalSelection>(() => toLocalSelection(urlSelection))
  const requestedYear = urlSelection?.year ?? localSelection.year
  const { catalog: loadedCatalog, isLoading, error, retry } = useSeasonCatalog({ seasonsBaseUrl, year: requestedYear })
  const catalog = loadedCatalog?.year === requestedYear ? loadedCatalog : null
  const urlReplayTarget = resolveReplayTarget(catalog, urlSelection, seasonsBaseUrl)
  const localReplayTarget = resolveReplayTarget(catalog, localSelection, seasonsBaseUrl)
  const hasUnavailableUrlSelection = isCompleteSelection(urlSelection) && urlReplayTarget === null

  useEffect(() => {
    if (urlSelection !== null) setLocalSelection(toLocalSelection(urlSelection))
  }, [urlSelection])

  function handleSelectRace(raceId: string | null): void {
    setLocalSelection((current) => ({ ...current, race: raceId, session: null }))
  }

  function handleSelectSession(sessionCode: string | null): void {
    setLocalSelection((current) => ({ ...current, session: sessionCode }))
  }

  function handleOpenWorkspace(): void {
    if (localReplayTarget === null) return
    setUrlSelection({
      year: catalog?.year ?? localSelection.year,
      race: localReplayTarget.selection.race.race_id,
      session: localReplayTarget.selection.session.session_code,
    })
  }

  function handleChangeRace(): void {
    if (urlSelection !== null) setLocalSelection(toLocalSelection(urlSelection))
    setUrlSelection(null)
  }

  if (urlReplayTarget !== null) {
    return (
      <ReplayEntry
        browserBaseUrl={urlReplayTarget.browserBaseUrl}
        browserPointerPath={urlReplayTarget.browserPointerPath}
        onChangeRace={handleChangeRace}
      />
    )
  }

  return (
    <RaceLibraryPage
      catalog={catalog}
      selectedRaceId={localSelection.race}
      selectedSessionCode={localSelection.session}
      isLoading={isLoading}
      error={error}
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
