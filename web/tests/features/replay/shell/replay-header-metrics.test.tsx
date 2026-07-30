/**
 * @vitest-environment jsdom
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { ReplayHeaderMetrics } from '../../../../src/features/replay/shell/ReplayHeaderMetrics'

afterEach(cleanup)

test('renders the workspace hero without the retired status label', () => {
  render(<ReplayHeaderMetrics />)

  expect(screen.getByRole('heading', { name: 'F1 Race Replay' })).toBeTruthy()
  expect(screen.getByText('Replay workspace')).toBeTruthy()
  expect(screen.queryByText('Interactive race data')).toBeNull()
  expect(screen.queryByText(/Telemetry, timing and race control/)).toBeNull()
})
