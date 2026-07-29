import { assertSafeRelativePath } from '../replay/source'
import { array, exact, freeze, integer, nullable, object, string, type ObjectValue } from '../replay/value-guards'
import type { BrowserPointerResolution, CatalogSelection, CatalogV2, CatalogV2Race, CatalogV2Session } from './types'

const SAFE_COMPONENT = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

export function parseCatalogV2(value: unknown): CatalogV2 {
  const item = object(value, 'catalog')
  exact(item, ['schemaVersion', 'year', 'atomicAcrossRaces', 'races'], [], 'catalog')
  if (item.schemaVersion !== 2) throw new Error('catalog schemaVersion must be exactly 2')

  const year = integer(item.year, 'catalog.year', 1)
  const races = array(item.races, 'catalog.races').map((race, index) => parseCatalogV2Race(race, index))
  const raceIds = races.map(({ race_id }) => race_id)
  if (new Set(raceIds).size !== raceIds.length) throw new Error('catalog races must not contain duplicate race_id values')

  return freeze({
    schemaVersion: 2,
    year,
    atomicAcrossRaces: boolean(item.atomicAcrossRaces, 'catalog.atomicAcrossRaces'),
    races: freeze(races),
  })
}

export function parseCatalogV2Race(value: unknown, index?: number): CatalogV2Race {
  const label = index === undefined ? 'race' : `catalog.races[${index}]`
  const item = object(value, label)
  exact(item, ['race_id', 'round_number', 'event_name', 'sessions'], ['country', 'location', 'event_date'], label)

  const sessions = array(item.sessions, `${label}.sessions`).map((session, sessionIndex) => parseCatalogV2Session(session, label, sessionIndex))
  const sessionCodes = sessions.map(({ session_code }) => session_code)
  if (new Set(sessionCodes).size !== sessionCodes.length) throw new Error(`${label}.sessions must not contain duplicate session_code values`)

  return freeze({
    race_id: safeComponent(item.race_id, `${label}.race_id`),
    round_number: integer(item.round_number, `${label}.round_number`, 1),
    event_name: requiredText(item.event_name, `${label}.event_name`),
    ...optionalText(item, 'country', label),
    ...optionalText(item, 'location', label),
    ...optionalText(item, 'event_date', label),
    sessions: freeze(sessions),
  })
}

export function parseCatalogV2Session(value: unknown, raceLabel = 'race', index?: number): CatalogV2Session {
  const label = index === undefined ? `${raceLabel}.session` : `${raceLabel}.sessions[${index}]`
  const item = object(value, label)
  exact(item, [
    'session_code', 'session_name', 'generation_id', 'delivery_version', 'outcome', 'validated',
    'canonical_pointer', 'browser_pointer',
  ], [], label)

  const session: CatalogV2Session = freeze({
    session_code: safeComponent(item.session_code, `${label}.session_code`).toLowerCase(),
    session_name: requiredText(item.session_name, `${label}.session_name`),
    generation_id: nullable(item.generation_id, (entry) => safeComponent(entry, `${label}.generation_id`)),
    delivery_version: nullable(item.delivery_version, (entry) => safeComponent(entry, `${label}.delivery_version`)),
    outcome: safeComponent(item.outcome, `${label}.outcome`),
    validated: boolean(item.validated, `${label}.validated`),
    canonical_pointer: nullable(item.canonical_pointer, (entry) => safePointer(entry, `${label}.canonical_pointer`)),
    browser_pointer: nullable(item.browser_pointer, (entry) => safePointer(entry, `${label}.browser_pointer`)),
  })

  validateSessionReferences(session, label)
  return session
}

export function isSessionReplayReady(value: unknown): value is CatalogV2Session {
  if (!isObject(value)) return false
  return value.validated === true
    && isSafeComponentValue(value.generation_id)
    && isSafeComponentValue(value.delivery_version)
    && isSafePointerValue(value.canonical_pointer)
    && isSafePointerValue(value.browser_pointer)
}

export function getReplayReadySessions(race: CatalogV2Race): readonly CatalogV2Session[] {
  return race.sessions.filter(isSessionReplayReady)
}

export function selectRace(catalog: CatalogV2, raceId: string | null | undefined): CatalogV2Race | null {
  if (typeof raceId !== 'string' || !raceId) return null
  return catalog.races.find((race) => race.race_id === raceId) ?? null
}

export function selectSession(race: CatalogV2Race, sessionCode: string | null | undefined): CatalogV2Session | null {
  if (typeof sessionCode !== 'string' || !sessionCode) return null
  const normalized = sessionCode.toLowerCase()
  return race.sessions.find((session) => session.session_code === normalized) ?? null
}

export function selectReplaySession(catalog: CatalogV2, raceId: string | null | undefined, sessionCode: string | null | undefined): CatalogSelection | null {
  const race = selectRace(catalog, raceId)
  if (!race) return null
  const session = selectSession(race, sessionCode)
  if (!session || !isSessionReplayReady(session)) return null
  return freeze({ race, session })
}

export function resolveBrowserPointer(
  browserPointer: string,
  raceId: string,
  sessionCode: string,
): BrowserPointerResolution {
  const expectedRaceId = safeComponent(raceId, 'race_id')
  const expectedSessionCode = safeComponent(sessionCode, 'session_code').toLowerCase()
  const path = assertSafeRelativePath(browserPointer)
  const parts = path.split('/')
  if (parts.length !== 5 || parts[0] !== 'browser' || parts[2] !== 'sessions' || parts[4] !== 'browser-current.json') {
    throw new Error('browser_pointer must identify a session browser-current.json path')
  }
  if (parts[1] !== expectedRaceId) throw new Error('browser_pointer race identity disagrees with race_id')
  if (parts[3].toLowerCase() !== expectedSessionCode) throw new Error('browser_pointer session identity disagrees with session_code')

  return freeze({
    browserBasePath: `browser/${expectedRaceId}`,
    pointerPath: `sessions/${expectedSessionCode}/browser-current.json`,
  })
}

/** Explicit alias for callers resolving a catalog session's browser pointer. */
export const resolveSessionBrowserPointer = resolveBrowserPointer

function validateSessionReferences(session: CatalogV2Session, label: string): void {
  const pointers = [session.canonical_pointer, session.browser_pointer]
  if (pointers.some((pointer) => pointer !== null) && pointers.some((pointer) => pointer === null)) {
    throw new Error(`${label} canonical_pointer and browser_pointer must be provided together`)
  }
  if (pointers.some((pointer) => pointer !== null)
    && (session.generation_id === null || session.delivery_version === null)) {
    throw new Error(`${label} pointers require generation_id and delivery_version`)
  }
  if (!session.validated && pointers.some((pointer) => pointer !== null)) {
    throw new Error(`${label} unvalidated sessions must not claim pointer paths`)
  }
  if (session.validated && !isSessionReplayReady(session)) {
    throw new Error(`${label} validated sessions require complete artifact references`)
  }
}

function optionalText(item: ObjectValue, field: string, label: string): Partial<Record<string, string | null>> {
  if (!(field in item)) return {}
  if (item[field] === null) return { [field]: null }
  return { [field]: requiredText(item[field], `${label}.${field}`) }
}

function requiredText(value: unknown, label: string): string {
  const text = string(value, label).trim()
  if (!text) throw new Error(`${label} must be non-blank`)
  return text
}

function safeComponent(value: unknown, label: string): string {
  const text = requiredText(value, label)
  if (!SAFE_COMPONENT.test(text)) throw new Error(`${label} must be a safe identifier`)
  return text
}

function safePointer(value: unknown, label: string): string {
  const text = requiredText(value, label)
  try { return assertSafeRelativePath(text) } catch (error) { throw new Error(`${label} must be a safe relative POSIX path`, { cause: error }) }
}

function boolean(value: unknown, label: string): boolean {
  if (typeof value !== 'boolean') throw new Error(`${label} must be a boolean`)
  return value
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isSafeComponentValue(value: unknown): value is string {
  return typeof value === 'string' && SAFE_COMPONENT.test(value)
}

function isSafePointerValue(value: unknown): value is string {
  if (typeof value !== 'string') return false
  try { assertSafeRelativePath(value); return true } catch { return false }
}
