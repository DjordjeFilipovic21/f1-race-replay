import { useCallback, useEffect, useMemo, useState } from 'react'
import { loadCatalog } from '../../data/catalog/loader'
import { createFetchSource } from '../../data/replay/source'
import type { CatalogV2 } from '../../data/catalog/types'
import type { ReplaySource } from '../../data/replay/types'

export interface UseSeasonCatalogOptions {
  readonly seasonsBaseUrl: string
  readonly year: number
}

export interface SeasonCatalogState {
  readonly catalog: CatalogV2 | null
  /** Source rooted at the requested season, for small catalog-adjacent assets. */
  readonly source: ReplaySource
  readonly isLoading: boolean
  readonly error: Error | null
  readonly retry: () => void
}

export function useSeasonCatalog({ seasonsBaseUrl, year }: UseSeasonCatalogOptions): SeasonCatalogState {
  const [attempt, setAttempt] = useState(0)
  const [catalog, setCatalog] = useState<CatalogV2 | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const catalogSource = useMemo(() => createFetchSource(seasonsBaseUrl), [seasonsBaseUrl])
  const source = useMemo(() => createFetchSource(resolveSeasonUrl(seasonsBaseUrl, year)), [seasonsBaseUrl, year])

  useEffect(() => {
    let stale = false
    setCatalog(null)
    setIsLoading(true)
    setError(null)

    const load = async (): Promise<void> => {
      try {
        const loadedCatalog = await loadCatalog({
          source: catalogSource,
          year,
        })
        if (stale) return
        setCatalog(loadedCatalog)
      } catch (loadError: unknown) {
        if (stale) return
        setError(loadError instanceof Error ? loadError : new Error('Season catalog could not be loaded'))
      } finally {
        if (!stale) setIsLoading(false)
      }
    }

    void load()
    return () => {
      stale = true
    }
  }, [attempt, catalogSource, year])

  const retry = useCallback(() => setAttempt((value) => value + 1), [])
  return { catalog, source, isLoading, error, retry }
}

function resolveSeasonUrl(seasonsBaseUrl: string, year: number): string {
  const baseUrl = seasonsBaseUrl.endsWith('/') ? seasonsBaseUrl : `${seasonsBaseUrl}/`
  return `${baseUrl}${year}/`
}
