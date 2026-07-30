/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { ReplayEntry } from '../../../../src/features/replay/entry/ReplayEntry'
import { useReplayEntry, type ReadyReplay } from '../../../../src/features/replay/entry/useReplayEntry'

vi.mock('../../../../src/features/replay/entry/useReplayEntry', () => ({ useReplayEntry: vi.fn() }))
vi.mock('../../../../src/features/replay/shell/ReplayControls', () => ({ ReplayControls: () => <p>Replay controls</p> }))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const entryProps = {
  browserBaseUrl: '/replay-data/browser/example/',
  browserPointerPath: 'sessions/race/browser-current.json',
  onChangeSession: vi.fn(),
}

test('owns the loading state and change-race action', () => {
  const retry = vi.fn()
  vi.mocked(useReplayEntry).mockReturnValue({ replay: null, error: null, retry })

  render(<ReplayEntry {...entryProps} />)

  expect(screen.getByRole('status', { name: 'Replay loading' })).toBeTruthy()
  expect(document.querySelector('.app-shell__grid')?.getAttribute('aria-hidden')).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: 'Change session' }))
  expect(entryProps.onChangeSession).toHaveBeenCalledOnce()
  expect(screen.queryByRole('button', { name: 'Retry loading' })).toBeNull()
})

test('owns loading retry and reports initialization errors', () => {
  const retry = vi.fn()
  vi.mocked(useReplayEntry).mockReturnValue({ replay: null, error: new Error('pointer unavailable'), retry })

  render(<ReplayEntry {...entryProps} />)

  expect(screen.getByRole('alert', { name: 'Replay loading error' }).textContent).toContain('pointer unavailable')
  fireEvent.click(screen.getByRole('button', { name: 'Retry loading' }))
  expect(retry).toHaveBeenCalledOnce()
})

test('composes ready replay controls inside the replay error boundary', () => {
  const replay = {} as ReadyReplay
  vi.mocked(useReplayEntry).mockReturnValue({ replay, error: null, retry: vi.fn() })

  render(<ReplayEntry {...entryProps} />)

  expect(screen.getByText('Replay controls')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Change session' })).toBeTruthy()
})
