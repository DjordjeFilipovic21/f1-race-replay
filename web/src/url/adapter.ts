export interface UrlSelection {
  readonly year: number | null
  readonly race: string | null
  readonly session: string | null
  readonly isMalformed: boolean
}

export type SelectionUpdate = Partial<Pick<UrlSelection, 'year' | 'race' | 'session'>> | null

const SELECTION_KEYS = ['year', 'race', 'session'] as const
const SAFE_SELECTION_VALUE = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

interface ParsedValue<T> {
  readonly value: T
  readonly isMalformed: boolean
}

export function parseSelection(params: URLSearchParams): UrlSelection | null {
  const hasSelectionParameter = SELECTION_KEYS.some((key) => params.has(key))
  if (!hasSelectionParameter) return null

  const year = parseYear(params)
  const race = parseText(params, 'race')
  const session = parseText(params, 'session')

  return {
    year: year.value,
    race: race.value,
    session: session.value,
    isMalformed: year.isMalformed || race.isMalformed || session.isMalformed,
  }
}

export function serializeSelection(
  selection: SelectionUpdate,
  existingSearch: string | URLSearchParams = '',
): string {
  const params = toSearchParams(existingSearch)
  SELECTION_KEYS.forEach((key) => params.delete(key))

  if (selection !== null) {
    setYear(params, selection.year)
    setText(params, 'race', selection.race)
    setText(params, 'session', selection.session)
  }

  const search = params.toString()
  return search.length === 0 ? '' : `?${search}`
}

function parseYear(params: URLSearchParams): ParsedValue<number | null> {
  const values = params.getAll('year')
  if (values.length === 0) return { value: null, isMalformed: false }
  if (values.length !== 1 || !/^\d+$/.test(values[0])) return { value: null, isMalformed: true }

  const year = Number(values[0])
  return Number.isSafeInteger(year) && year > 0
    ? { value: year, isMalformed: false }
    : { value: null, isMalformed: true }
}

function parseText(params: URLSearchParams, key: 'race' | 'session'): ParsedValue<string | null> {
  const values = params.getAll(key)
  if (values.length === 0) return { value: null, isMalformed: false }
  if (values.length !== 1) return { value: null, isMalformed: true }

  const normalized = values[0].trim()
  return SAFE_SELECTION_VALUE.test(normalized)
    ? { value: normalized, isMalformed: false }
    : { value: null, isMalformed: true }
}

function toSearchParams(existingSearch: string | URLSearchParams): URLSearchParams {
  if (existingSearch instanceof URLSearchParams) return new URLSearchParams(existingSearch)
  return new URLSearchParams(existingSearch.startsWith('?') ? existingSearch.slice(1) : existingSearch)
}

function setYear(params: URLSearchParams, year: number | null | undefined): void {
  if (typeof year === 'number' && Number.isSafeInteger(year) && year > 0) {
    params.set('year', String(year))
  }
}

function setText(params: URLSearchParams, key: 'race' | 'session', value: string | null | undefined): void {
  if (typeof value !== 'string') return
  const normalized = value.trim()
  if (normalized.length > 0) params.set(key, normalized)
}
