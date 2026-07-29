import { useCallback, useEffect, useState } from 'react'
import { parseSelection, serializeSelection, type SelectionUpdate, type UrlSelection } from './adapter'

export type SetUrlSelection = (selection: SelectionUpdate) => void

export function useUrlSelection(): readonly [UrlSelection | null, SetUrlSelection] {
  const readSelection = useCallback(() => parseSelection(new URLSearchParams(globalThis.location.search)), [])
  const [selection, setSelection] = useState<UrlSelection | null>(readSelection)

  useEffect(() => {
    const handlePopState = () => setSelection(readSelection())
    globalThis.addEventListener('popstate', handlePopState)
    return () => globalThis.removeEventListener('popstate', handlePopState)
  }, [readSelection])

  const updateSelection = useCallback((nextSelection: SelectionUpdate) => {
    const search = serializeSelection(nextSelection, globalThis.location.search)
    const nextLocation = `${globalThis.location.pathname}${search}${globalThis.location.hash}`
    globalThis.history.pushState(null, '', nextLocation)
    setSelection(parseSelection(new URLSearchParams(search)))
  }, [])

  return [selection, updateSelection]
}
