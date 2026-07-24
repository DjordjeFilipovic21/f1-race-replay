import type { JsonObject, JsonValue } from './types'

export type ObjectValue = Record<string, unknown>

export function object(value: unknown, label: string): ObjectValue {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be an object`)
  return value as ObjectValue
}

export function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`)
  return value
}

export function string(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value) throw new Error(`${label} must be a non-empty string`)
  return value
}

export function finite(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`${label} must be finite`)
  return value
}

export function integer(value: unknown, label: string, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    throw new Error(`${label} must be an integer from ${minimum} to ${maximum}`)
  }
  return value as number
}

export function nullable<T>(value: unknown, parse: (entry: unknown) => T): T | null {
  return value === null ? null : parse(value)
}

export function freeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze)
    Object.freeze(value)
  }
  return value
}

export function exact(value: ObjectValue, required: readonly string[], optional: readonly string[], label: string): void {
  for (const field of required) if (!(field in value)) throw new Error(`${label}.${field} is required`)
  const allowed = new Set([...required, ...optional])
  for (const field of Object.keys(value)) if (!allowed.has(field)) throw new Error(`${label}.${field} is not allowed`)
}

export function jsonObject(value: unknown, label: string): JsonObject {
  const item = object(value, label)
  return freeze(Object.fromEntries(
    Object.entries(item).map(([key, entry]) => [key, jsonValue(entry, `${label}.${key}`)]),
  )) as JsonObject
}

function jsonValue(value: unknown, label: string): JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value
  if (typeof value === 'number') return finite(value, label)
  if (Array.isArray(value)) return freeze(value.map((entry, index) => jsonValue(entry, `${label}[${index}]`)))
  return jsonObject(value, label)
}
