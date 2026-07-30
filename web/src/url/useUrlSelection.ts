import { useCallback, useEffect, useState } from 'react'
import { parseSelection, serializeSelection, type SelectionUpdate, type UrlSelection } from './adapter'

export type SetUrlSelection = (selection: SelectionUpdate) => void
export type UrlPopStateTransition = (
  selection: UrlSelection | null,
  update: () => void,
) => void

export function useUrlSelection(
  transitionPopState?: UrlPopStateTransition,
): readonly [UrlSelection | null, SetUrlSelection] {
  const readSelection = useCallback(() => parseSelection(new URLSearchParams(globalThis.location.search)), [])
  const [selection, setSelection] = useState<UrlSelection | null>(readSelection)

  useEffect(() => {
    const handlePopState = () => {
      const nextSelection = readSelection()
      const update = () => setSelection(nextSelection)
      if (transitionPopState === undefined) {
        update()
      } else {
        transitionPopState(nextSelection, update)
      }
    }
    globalThis.addEventListener('popstate', handlePopState)
    return () => globalThis.removeEventListener('popstate', handlePopState)
  }, [readSelection, transitionPopState])

  const updateSelection = useCallback((nextSelection: SelectionUpdate) => {
    const search = serializeSelection(nextSelection, globalThis.location.search)
    const nextLocation = `${globalThis.location.pathname}${search}${globalThis.location.hash}`
    globalThis.history.pushState(null, '', nextLocation)
    setSelection(parseSelection(new URLSearchParams(search)))
  }, [])

  return [selection, updateSelection]
}
