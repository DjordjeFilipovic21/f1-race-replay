import type { ReplaySource } from './types'

export function assertSafeRelativePath(path: string): string {
  if (!/^(?!\/)(?!.*(?:^|\/)\.\.?\/)[A-Za-z0-9][A-Za-z0-9._-]*(?:\/[A-Za-z0-9][A-Za-z0-9._-]*)*$/.test(path)) {
    throw new Error(`Unsafe replay-data path: ${path}`)
  }
  return path
}

export function resolveRelativePath(baseFile: string, reference: string): string {
  const base = assertSafeRelativePath(baseFile).split('/')
  base.pop()
  return assertSafeRelativePath([...base, assertSafeRelativePath(reference)].filter(Boolean).join('/'))
}

export function createFetchSource(baseUrl: string, fetcher: typeof fetch = fetch): ReplaySource {
  const base = new URL(baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`, globalThis.location?.href)
  return {
    async read(path: string): Promise<Uint8Array> {
      const response = await fetcher(new URL(assertSafeRelativePath(path), base), { headers: { Accept: 'application/json' } })
      if (!response.ok) throw new Error(`Replay-data request failed: ${response.status}`)
      return new Uint8Array(await response.arrayBuffer())
    },
  }
}

export async function readJson(source: ReplaySource, path: string): Promise<unknown> {
  const bytes = await source.read(assertSafeRelativePath(path))
  try {
    return JSON.parse(new TextDecoder().decode(bytes)) as unknown
  } catch (error) {
    throw new Error(`Replay-data JSON is invalid at ${path}`, { cause: error })
  }
}
