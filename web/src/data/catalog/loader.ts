import { readJson, assertSafeRelativePath } from '../replay/source'
import type { ReplaySource } from '../replay/types'
import { parseCatalogV2 } from './guards'
import type { CatalogV2, LoadCatalogOptions } from './types'

export async function loadCatalog(options: LoadCatalogOptions): Promise<CatalogV2>
export async function loadCatalog(source: ReplaySource, year: number): Promise<CatalogV2>
export async function loadCatalog(
  optionsOrSource: LoadCatalogOptions | ReplaySource,
  requestedYear?: number,
): Promise<CatalogV2> {
  const options = normalizeLoadCatalogOptions(optionsOrSource, requestedYear)
  const year = options.year
  if (typeof year !== 'number' || !Number.isSafeInteger(year) || year < 1) throw new Error('catalog year must be a positive integer')

  const prefix = options.seasonsBase?.replace(/^\/+|\/+$/g, '')
  const relativePath = assertSafeRelativePath(`${prefix ? `${prefix}/` : ''}${year}/catalog.json`)
  const catalog = parseCatalogV2(await readJson(options.source, relativePath))
  if (catalog.year !== year) throw new Error('catalog year disagrees with requested year')
  return catalog
}

function isLoadCatalogOptions(value: LoadCatalogOptions | ReplaySource): value is LoadCatalogOptions {
  return 'source' in value
}

function normalizeLoadCatalogOptions(
  optionsOrSource: LoadCatalogOptions | ReplaySource,
  requestedYear: number | undefined,
): LoadCatalogOptions {
  if (isLoadCatalogOptions(optionsOrSource)) return optionsOrSource
  if (requestedYear === undefined) throw new Error('catalog year must be a positive integer')
  return { source: optionsOrSource, year: requestedYear }
}
