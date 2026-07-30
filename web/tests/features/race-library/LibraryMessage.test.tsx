/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { LibraryMessage } from '../../../src/features/race-library/LibraryMessage'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
})

describe('LibraryMessage', () => {
  test('renders loading state with status role', () => {
    render(
      <LibraryMessage
        variant="loading"
        title="Loading Season"
        message="Fetching the race catalog…"
      />
    )

    expect(screen.getByRole('status', { name: 'Loading Season' })).toBeTruthy()
    expect(screen.getByText('Loading Season')).toBeTruthy()
    expect(screen.getByText('Fetching the race catalog…')).toBeTruthy()
  })

  test('renders error state with alert role', () => {
    render(
      <LibraryMessage
        variant="error"
        title="Unable to Load Season"
        message="Network timeout occurred"
      />
    )

    expect(screen.getByRole('alert', { name: 'Unable to Load Season' })).toBeTruthy()
    expect(screen.getByText('Unable to Load Season')).toBeTruthy()
    expect(screen.getByText('Network timeout occurred')).toBeTruthy()
  })

  test('renders error state with retry button when onRetry is provided', () => {
    const onRetry = vi.fn()
    render(
      <LibraryMessage
        variant="error"
        title="Unable to Load Season"
        message="Network timeout occurred"
        onRetry={onRetry}
      />
    )

    const retryButton = screen.getByRole('button', { name: 'Retry' })
    expect(retryButton).toBeTruthy()

    fireEvent.click(retryButton)
    expect(onRetry).toHaveBeenCalledOnce()
  })

  test('renders error state without retry button when onRetry is not provided', () => {
    render(
      <LibraryMessage
        variant="error"
        title="Unable to Load Season"
        message="Network timeout occurred"
      />
    )

    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  test('renders empty state with status role', () => {
    render(
      <LibraryMessage
        variant="empty"
        title="No Races Available"
        message="This season has no races in the catalog yet."
      />
    )

    expect(screen.getByRole('status', { name: 'No Races Available' })).toBeTruthy()
    expect(screen.getByText('No Races Available')).toBeTruthy()
    expect(screen.getByText('This season has no races in the catalog yet.')).toBeTruthy()
  })

  test('uses correct CSS classes for each variant', () => {
    const { unmount: unmount1 } = render(
      <LibraryMessage variant="loading" title="Loading" message="Loading..." />
    )
    expect(document.querySelector('.library-loading')).toBeTruthy()
    expect(document.querySelector('.library-loading__title')).toBeTruthy()
    expect(document.querySelector('.library-loading__message')).toBeTruthy()
    unmount1()

    const { unmount: unmount2 } = render(
      <LibraryMessage variant="error" title="Error" message="Error..." />
    )
    expect(document.querySelector('.library-error')).toBeTruthy()
    expect(document.querySelector('.library-error__title')).toBeTruthy()
    expect(document.querySelector('.library-error__message')).toBeTruthy()
    unmount2()

    render(
      <LibraryMessage variant="empty" title="Empty" message="Empty..." />
    )
    expect(document.querySelector('.library-empty')).toBeTruthy()
    expect(document.querySelector('.library-empty__title')).toBeTruthy()
    expect(document.querySelector('.library-empty__message')).toBeTruthy()
  })
})
