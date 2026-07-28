/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ReplayErrorBoundary } from '../../../../src/features/replay/shell/ReplayErrorBoundary'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
})

test('renders an Error message in an accessible alert and recovers on retry', () => {
  let shouldThrow = true
  const UnstableContent = () => {
    if (shouldThrow) throw new Error('Panel renderer failed')
    return <p>Recovered panel</p>
  }

  render(
    <ReplayErrorBoundary label="Telemetry panel">
      <UnstableContent />
    </ReplayErrorBoundary>,
  )

  expect(screen.getByRole('alert', { name: 'Telemetry panel error' }).textContent).toContain('Panel renderer failed')
  expect(screen.getByRole('heading', { name: 'Telemetry panel unavailable' })).toBeTruthy()

  shouldThrow = false
  fireEvent.click(screen.getByRole('button', { name: 'Retry telemetry panel' }))

  expect(screen.getByText('Recovered panel')).toBeTruthy()
  expect(screen.queryByRole('alert', { name: 'Telemetry panel error' })).toBeNull()
})

test('uses the generic fallback for non-Error thrown values', () => {
  const ThrowingContent = () => {
    throw 'renderer failed'
  }

  render(
    <ReplayErrorBoundary label="Leaderboard panel">
      <ThrowingContent />
    </ReplayErrorBoundary>,
  )

  expect(screen.getByRole('alert', { name: 'Leaderboard panel error' }).textContent).toContain('An unexpected rendering error occurred.')
  expect(screen.queryByText('renderer failed')).toBeNull()
})
