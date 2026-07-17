/**
 * @vitest-environment jsdom
 */
import { act, render } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, expect, test, vi } from 'vitest'
import App from '../src/App'
import { loadReplayIndex } from '../src/replay-data/loader'
import { createReplayController, type ReplayController, type ReplayControllerSnapshot } from '../src/replay-engine'
import type { ReplayIndex } from '../src/replay-data/types'

vi.mock('../src/replay-data/loader', () => ({ loadReplayIndex: vi.fn() }))
vi.mock('../src/replay-engine', () => ({ createReplayController: vi.fn() }))

interface Deferred<T> {
  readonly promise: Promise<T>
  readonly resolve: (value: T) => void
}

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((promiseResolve) => { resolve = promiseResolve })
  return { promise, resolve }
}

const index = {
  manifest: { chunks: [{ startMs: 0, endMs: 3000 }] },
} as unknown as ReplayIndex

function createController(): ReplayController {
  const snapshot: ReplayControllerSnapshot = {
    status: 'loading', timeMs: 0, speed: 1, isPlaying: false, replay: null, crossedEvents: [], error: null,
  }
  return {
    getSnapshot: () => snapshot,
    subscribe: () => () => undefined,
    start: vi.fn(), pause: vi.fn(), seek: vi.fn(), setSpeed: vi.fn(), retry: vi.fn(async () => undefined), dispose: vi.fn(),
  }
}

afterEach(() => vi.restoreAllMocks())

test('does not create a controller for StrictMode’s stale index resolution and disposes the active controller', async () => {
  const firstLoad = createDeferred<ReplayIndex>()
  const activeLoad = createDeferred<ReplayIndex>()
  const activeController = createController()
  const loadReplayIndexMock = vi.mocked(loadReplayIndex)
  const createReplayControllerMock = vi.mocked(createReplayController)
  loadReplayIndexMock.mockReturnValueOnce(firstLoad.promise).mockReturnValueOnce(activeLoad.promise)
  createReplayControllerMock.mockReturnValue(activeController)

  const { unmount } = render(<StrictMode><App /></StrictMode>)

  await act(async () => { firstLoad.resolve(index) })
  expect(createReplayControllerMock).not.toHaveBeenCalled()

  await act(async () => { activeLoad.resolve(index) })
  expect(createReplayControllerMock).toHaveBeenCalledOnce()

  unmount()
  expect(activeController.dispose).toHaveBeenCalledOnce()
})
