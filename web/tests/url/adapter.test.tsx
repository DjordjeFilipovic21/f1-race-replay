/**
 * @vitest-environment jsdom
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, expect, test, vi } from 'vitest'
import { parseSelection, serializeSelection } from '../../src/url/adapter'
import { useUrlSelection } from '../../src/url/useUrlSelection'

afterEach(() => {
  window.history.replaceState(null, '', '/')
  vi.restoreAllMocks()
})

test('parses a complete positive year, race, and session selection', () => {
  expect(parseSelection(new URLSearchParams('year=2024&race=monza&session=R'))).toEqual({
    year: 2024,
    race: 'monza',
    session: 'R',
    isMalformed: false,
  })
})

test('returns null fields for missing or invalid individual parameters', () => {
  expect(parseSelection(new URLSearchParams('year=0&race=&session=R'))).toEqual({
    year: null,
    race: null,
    session: 'R',
    isMalformed: true,
  })
  expect(parseSelection(new URLSearchParams())).toBeNull()
})

test('accepts a valid partial selection without marking it malformed', () => {
  expect(parseSelection(new URLSearchParams('year=2024'))).toEqual({
    year: 2024,
    race: null,
    session: null,
    isMalformed: false,
  })
})

test('marks blank and unsafe selection values as malformed', () => {
  expect(parseSelection(new URLSearchParams('year=2024&race=../bahrain&session='))).toEqual({
    year: 2024,
    race: null,
    session: null,
    isMalformed: true,
  })
})

test('serializes only present values and omits empty values', () => {
  expect(serializeSelection({ year: 2024, race: 'monza', session: null })).toBe('?year=2024&race=monza')
  expect(serializeSelection({ year: null, race: '', session: undefined })).toBe('')
})

test('preserves unrelated query parameters while replacing selection values', () => {
  expect(serializeSelection({ year: 2024, race: 'monza', session: 'R' }, '?trajectory=linear&year=2023')).toBe(
    '?trajectory=linear&year=2024&race=monza&session=R',
  )
})

function SelectionProbe() {
  const [selection, setSelection] = useUrlSelection()
  const currentSelection = selection ?? { year: null, race: null, session: null, isMalformed: false }

  return (
    <>
      <output data-testid="selection">{JSON.stringify(currentSelection)}</output>
      <button type="button" onClick={() => setSelection({ year: 2024, race: 'monza', session: 'R' })}>Select</button>
    </>
  )
}

test('pushes a selection and follows browser popstate changes', () => {
  window.history.replaceState(null, '', '/?trajectory=linear')
  const pushState = vi.spyOn(window.history, 'pushState')
  render(<SelectionProbe />)

  fireEvent.click(screen.getByRole('button', { name: 'Select' }))
  expect(pushState).toHaveBeenCalledWith(null, '', '/?trajectory=linear&year=2024&race=monza&session=R')
  expect(screen.getByTestId('selection').textContent).toBe(JSON.stringify({ year: 2024, race: 'monza', session: 'R', isMalformed: false }))

  window.history.pushState(null, '', '/?trajectory=linear&year=2025&race=spa&session=Q')
  act(() => window.dispatchEvent(new PopStateEvent('popstate')))
  expect(screen.getByTestId('selection').textContent).toBe(JSON.stringify({ year: 2025, race: 'spa', session: 'Q', isMalformed: false }))
})

test('removes the popstate listener on unmount after StrictMode effect replay', () => {
  const removeEventListener = vi.spyOn(window, 'removeEventListener')
  const { unmount } = render(<StrictMode><SelectionProbe /></StrictMode>)

  unmount()

  expect(removeEventListener).toHaveBeenCalledWith('popstate', expect.any(Function))
})
