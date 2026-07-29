/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { SessionSelector } from '../../../src/features/race-library/SessionSelector'
import type { CatalogV2Session } from '../../../src/data/catalog/types'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
})

function createReadySession(overrides: Partial<CatalogV2Session> = {}): CatalogV2Session {
  return {
    session_code: 'R',
    session_name: 'Race',
    generation_id: 'gen-1',
    delivery_version: 'v1',
    outcome: 'classified',
    validated: true,
    canonical_pointer: 'canonical/race-1/sessions/r/manifest.json',
    browser_pointer: 'browser/race-1/sessions/r/browser-current.json',
    ...overrides,
  }
}

function createUnvalidatedSession(overrides: Partial<CatalogV2Session> = {}): CatalogV2Session {
  return {
    session_code: 'FP1',
    session_name: 'Practice 1',
    generation_id: null,
    delivery_version: null,
    outcome: 'pending',
    validated: false,
    canonical_pointer: null,
    browser_pointer: null,
    ...overrides,
  }
}

describe('SessionSelector', () => {
  test('renders session buttons for each session', () => {
    const sessions = [
      createReadySession({ session_code: 'FP1', session_name: 'Practice 1' }),
      createReadySession({ session_code: 'FP2', session_name: 'Practice 2' }),
      createReadySession({ session_code: 'R', session_name: 'Race' }),
    ]
    render(
      <SessionSelector
        sessions={sessions}
        selectedSessionCode={null}
        onSelectSession={vi.fn()}
      />
    )

    expect(screen.getByRole('radiogroup', { name: 'Available sessions' })).toBeTruthy()
    expect(screen.getAllByRole('radio')).toHaveLength(3)
    expect(screen.getByRole('radio', { name: /Practice 1/ })).toBeTruthy()
    expect(screen.getByRole('radio', { name: /Practice 2/ })).toBeTruthy()
    expect(screen.getByRole('radio', { name: /Race/ })).toBeTruthy()
  })

  test('enables replay-ready sessions', () => {
    const sessions = [createReadySession()]
    render(
      <SessionSelector
        sessions={sessions}
        selectedSessionCode={null}
        onSelectSession={vi.fn()}
      />
    )

    const sessionButton = screen.getByRole('radio', { name: /Race/ })
    expect((sessionButton as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByText('Ready to replay')).toBeTruthy()
  })

  test('disables non-replay-ready sessions with explanation', () => {
    const sessions = [createUnvalidatedSession()]
    render(
      <SessionSelector
        sessions={sessions}
        selectedSessionCode={null}
        onSelectSession={vi.fn()}
      />
    )

    const sessionButton = screen.getByRole('radio', { name: /Practice 1.*not yet available/ })
    expect((sessionButton as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('Awaiting validation')).toBeTruthy()
    expect(screen.getByText('Unavailable')).toBeTruthy()
  })

  test('calls onSelectSession when a ready session is clicked', () => {
    const onSelectSession = vi.fn()
    const sessions = [createReadySession()]
    render(
      <SessionSelector
        sessions={sessions}
        selectedSessionCode={null}
        onSelectSession={onSelectSession}
      />
    )

    fireEvent.click(screen.getByRole('radio', { name: /Race/ }))
    expect(onSelectSession).toHaveBeenCalledWith('R')
  })

  test('does not call onSelectSession when a disabled session is clicked', () => {
    const onSelectSession = vi.fn()
    const sessions = [createUnvalidatedSession()]
    render(
      <SessionSelector
        sessions={sessions}
        selectedSessionCode={null}
        onSelectSession={onSelectSession}
      />
    )

    fireEvent.click(screen.getByRole('radio', { name: /Practice 1/ }))
    expect(onSelectSession).not.toHaveBeenCalled()
  })

  test('marks selected session with aria-checked', () => {
    const sessions = [
      createReadySession({ session_code: 'FP1', session_name: 'Practice 1' }),
      createReadySession({ session_code: 'R', session_name: 'Race' }),
    ]
    render(
      <SessionSelector
        sessions={sessions}
        selectedSessionCode="R"
        onSelectSession={vi.fn()}
      />
    )

    const practiceButton = screen.getByRole('radio', { name: /Practice 1/ })
    const raceButton = screen.getByRole('radio', { name: /Race/ })
    expect(practiceButton.getAttribute('aria-checked')).toBe('false')
    expect(raceButton.getAttribute('aria-checked')).toBe('true')
  })

  test('uses a roving tab stop and skips unavailable sessions with radio keys', () => {
    const onSelectSession = vi.fn()
    const sessions = [
      createUnvalidatedSession({ session_code: 'FP1', session_name: 'Practice 1' }),
      createReadySession({ session_code: 'Q', session_name: 'Qualifying' }),
      createUnvalidatedSession({ session_code: 'FP2', session_name: 'Practice 2' }),
      createReadySession({ session_code: 'R', session_name: 'Race' }),
      createReadySession({ session_code: 'S', session_name: 'Sprint' }),
    ]
    render(
      <SessionSelector
        sessions={sessions}
        selectedSessionCode="Q"
        onSelectSession={onSelectSession}
      />
    )

    const qualifyingButton = screen.getByRole('radio', { name: /Qualifying/ })
    const raceButton = screen.getByRole('radio', { name: /Race/ })
    const sprintButton = screen.getByRole('radio', { name: /Sprint/ })
    const practiceButton = screen.getByRole('radio', { name: /Practice 1/ })

    expect(qualifyingButton.getAttribute('tabindex')).toBe('0')
    expect(raceButton.getAttribute('tabindex')).toBe('-1')
    expect(practiceButton.getAttribute('tabindex')).toBe('-1')

    qualifyingButton.focus()
    fireEvent.keyDown(qualifyingButton, { key: 'ArrowRight' })
    expect(onSelectSession).toHaveBeenCalledWith('R')
    expect(document.activeElement).toBe(raceButton)

    fireEvent.keyDown(raceButton, { key: 'End' })
    expect(onSelectSession).toHaveBeenCalledWith('S')
    expect(document.activeElement).toBe(sprintButton)

    fireEvent.keyDown(sprintButton, { key: 'Home' })
    expect(onSelectSession).toHaveBeenCalledWith('Q')
    expect(document.activeElement).toBe(qualifyingButton)

    fireEvent.keyDown(qualifyingButton, { key: 'ArrowLeft' })
    expect(onSelectSession).toHaveBeenCalledWith('S')
    expect(document.activeElement).toBe(sprintButton)

    fireEvent.keyDown(sprintButton, { key: 'ArrowUp' })
    expect(onSelectSession).toHaveBeenCalledWith('R')
    expect(document.activeElement).toBe(raceButton)
  })

  test('shows ready status text for replay-ready sessions', () => {
    const sessions = [createReadySession({ session_code: 'SPR', session_name: 'Sprint' })]
    render(
      <SessionSelector
        sessions={sessions}
        selectedSessionCode={null}
        onSelectSession={vi.fn()}
      />
    )

    expect(screen.getByText('Ready to replay')).toBeTruthy()
  })
})
