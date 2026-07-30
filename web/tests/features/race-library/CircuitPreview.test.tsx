/**
 * @vitest-environment jsdom
 */
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { CircuitPreview } from '../../../src/features/race-library/CircuitPreview'
import type { ReplaySource } from '../../../src/data/replay/types'
import type { CircuitPreviewResult } from '../../../src/geo/circuit-preview'

vi.mock('../../../src/geo/circuit-preview', () => ({
  loadCircuitPreview: vi.fn(),
}))

import { loadCircuitPreview } from '../../../src/geo/circuit-preview'

const mockLoadCircuitPreview = vi.mocked(loadCircuitPreview)

function createSource(): ReplaySource {
  return { read: vi.fn() }
}

function createSuccessResult(): CircuitPreviewResult {
  return Object.freeze({
    pathData: 'M 0 0 L 100 0 L 100 100 L 0 100 Z',
    viewBox: '-10 -10 120 120',
  })
}

function createErrorResult(): CircuitPreviewResult {
  return Object.freeze({
    error: true as const,
    message: 'Circuit preview asset is missing.',
  })
}

/** Creates a deferred promise whose resolve/reject can be driven externally. */
function createDeferred<T>(): { promise: Promise<T>; resolve: (value: T) => void; reject: (reason: unknown) => void } {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  mockLoadCircuitPreview.mockReset()
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
})

afterEach(() => {
  cleanup()
  mockLoadCircuitPreview.mockReset()
  vi.restoreAllMocks()
})

describe('CircuitPreview', () => {
  describe('absent pointer (idle state)', () => {
    test('renders "Circuit preview unavailable" when previewPointer is null', () => {
      const source = createSource()
      render(<CircuitPreview source={source} previewPointer={null} circuitName="Monaco" />)

      expect(document.querySelector('.circuit-preview--idle')).toBeTruthy()
      expect(screen.getByText('Circuit preview unavailable')).toBeTruthy()
      expect(document.querySelector('svg')).toBeNull()
    })

    test('renders "Circuit preview unavailable" when previewPointer is undefined', () => {
      const source = createSource()
      render(<CircuitPreview source={source} previewPointer={undefined} circuitName="Monaco" />)

      expect(document.querySelector('.circuit-preview--idle')).toBeTruthy()
      expect(screen.getByText('Circuit preview unavailable')).toBeTruthy()
    })

    test('does not invoke the loader when previewPointer is null', () => {
      const source = createSource()
      render(<CircuitPreview source={source} previewPointer={null} circuitName="Monaco" />)

      expect(mockLoadCircuitPreview).not.toHaveBeenCalled()
    })

    test('renders a status message with role="status"', () => {
      const source = createSource()
      render(<CircuitPreview source={source} previewPointer={null} circuitName="Monaco" />)

      expect(screen.getByRole('status')).toBeTruthy()
    })
  })

  describe('loading state', () => {
    test('reserves a clean busy preview while the asset is being fetched', () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      expect(document.querySelector('.circuit-preview--loading')).toBeTruthy()
      expect(document.querySelector('.circuit-preview--loading')?.getAttribute('aria-busy')).toBe('true')
      expect(screen.queryByText('Loading Monaco circuit preview…')).toBeNull()
      expect(document.querySelector('svg')).toBeNull()
    })

    test('calls loadCircuitPreview with the source and pointer', () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      expect(mockLoadCircuitPreview).toHaveBeenCalledOnce()
      expect(mockLoadCircuitPreview).toHaveBeenCalledWith(source, 'visuals/monaco.json')
    })
  })

  describe('resolved state (success)', () => {
    test('renders an accessible SVG with the circuit name in aria-label', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      await act(async () => {
        deferred.resolve(createSuccessResult())
      })

      const svg = document.querySelector('svg.circuit-preview__canvas')
      expect(svg).toBeTruthy()
      expect(svg?.getAttribute('role')).toBe('img')
      expect(svg?.getAttribute('aria-label')).toBe('Monaco circuit preview')
    })

    test('renders the path with the resolved pathData', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      await act(async () => {
        deferred.resolve(createSuccessResult())
      })

      const path = document.querySelector('path.circuit-preview__path')
      expect(path).toBeTruthy()
      expect(path?.getAttribute('d')).toBe('M 0 0 L 100 0 L 100 100 L 0 100 Z')
    })

    test('sets the SVG viewBox from the resolved result', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      await act(async () => {
        deferred.resolve(createSuccessResult())
      })

      const svg = document.querySelector('svg.circuit-preview__canvas')
      expect(svg?.getAttribute('viewBox')).toBe('-22 -22 144 144')
    })

    test('preserves the generator orientation without applying a client-side rotation', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/portrait.json" circuitName="Portrait" />)

      await act(async () => {
        deferred.resolve(Object.freeze({
          pathData: 'M 0 0 L 100 0 L 100 200 L 0 200 Z',
          viewBox: '0 0 100 200',
        }))
      })

      expect(document.querySelector('.circuit-preview__canvas')?.getAttribute('viewBox'))
        .toBe('-10 -20 120 240')
      expect(document.querySelector('.circuit-preview__geometry')?.getAttribute('transform'))
        .toBeNull()
    })

    test('includes a title element for screen readers', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      await act(async () => {
        deferred.resolve(createSuccessResult())
      })

      const title = document.querySelector('svg title')
      expect(title).toBeTruthy()
      expect(title?.textContent).toBe('Monaco circuit preview')
    })

    test('applies the resolved BEM modifier to the container', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      await act(async () => {
        deferred.resolve(createSuccessResult())
      })

      expect(document.querySelector('.circuit-preview--resolved')).toBeTruthy()
      expect(document.querySelector('.circuit-preview--loading')).toBeNull()
    })

    test('sets preserveAspectRatio on the resolved SVG', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      await act(async () => {
        deferred.resolve(createSuccessResult())
      })

      const svg = document.querySelector('svg.circuit-preview__canvas')
      expect(svg?.getAttribute('preserveAspectRatio')).toBe('xMidYMid meet')
    })

    test('renders path with the CSS animation class for stroke-dasharray draw', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      await act(async () => {
        deferred.resolve(createSuccessResult())
      })

      const path = document.querySelector('path.circuit-preview__path')
      expect(path).toBeTruthy()
      expect(path?.classList.contains('circuit-preview__path')).toBe(true)
      expect(path?.getAttribute('pathLength')).toBeNull()
    })

    test('renders a separate animated glow path beneath the circuit line', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      await act(async () => {
        deferred.resolve(createSuccessResult())
      })

      const glow = document.querySelector('path.circuit-preview__glow')
      expect(glow?.getAttribute('d')).toBe('M 0 0 L 100 0 L 100 100 L 0 100 Z')
      expect(glow?.getAttribute('pathLength')).toBeNull()
      expect(glow?.getAttribute('aria-hidden')).toBe('true')
    })
  })

  describe('error state', () => {
    test('renders a descriptive error message when loading fails', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      await act(async () => {
        deferred.resolve(createErrorResult())
      })

      expect(document.querySelector('.circuit-preview--error')).toBeTruthy()
      expect(screen.getByText('Circuit preview asset is missing.')).toBeTruthy()
      expect(document.querySelector('svg')).toBeNull()
    })

    test('uses role="status" for the error message', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      await act(async () => {
        deferred.resolve(createErrorResult())
      })

      expect(screen.getByRole('status')).toBeTruthy()
    })
  })

  describe('stale-request protection', () => {
    test('ignores an earlier pointer resolution when a newer selection arrives', async () => {
      const firstDeferred = createDeferred<CircuitPreviewResult>()
      const secondDeferred = createDeferred<CircuitPreviewResult>()
      const secondResult: CircuitPreviewResult = Object.freeze({
        pathData: 'M 10 10 L 200 10 L 200 200 Z',
        viewBox: '0 0 210 210',
      })

      mockLoadCircuitPreview
        .mockReturnValueOnce(firstDeferred.promise)
        .mockReturnValueOnce(secondDeferred.promise)

      const source = createSource()
      const { rerender } = render(
        <CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />,
      )

      await act(async () => {
        rerender(
          <CircuitPreview source={source} previewPointer="visuals/silverstone.json" circuitName="Silverstone" />,
        )
      })

      await act(async () => {
        firstDeferred.resolve(createSuccessResult())
      })

      // The first (stale) resolution must NOT have replaced the loading state
      expect(document.querySelector('.circuit-preview--loading')).toBeTruthy()
      expect(document.querySelector('svg')).toBeNull()

      await act(async () => {
        secondDeferred.resolve(secondResult)
      })

      const path = document.querySelector('path.circuit-preview__path')
      expect(path).toBeTruthy()
      expect(path?.getAttribute('d')).toBe('M 10 10 L 200 10 L 200 200 Z')
    })

    test('transitions to idle when the pointer is removed after a pending load', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      const { rerender } = render(
        <CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />,
      )

      expect(document.querySelector('.circuit-preview--loading')).toBeTruthy()

      await act(async () => {
        rerender(
          <CircuitPreview source={source} previewPointer={null} circuitName="Monaco" />,
        )
      })

      expect(document.querySelector('.circuit-preview--idle')).toBeTruthy()
      expect(screen.getByText('Circuit preview unavailable')).toBeTruthy()

      // Resolve the stale request — it must not overwrite the idle state
      await act(async () => {
        deferred.resolve(createSuccessResult())
      })

      expect(document.querySelector('.circuit-preview--idle')).toBeTruthy()
      expect(document.querySelector('svg')).toBeNull()
    })
  })

  describe('pointer change behaviour', () => {
    test('issues a new load request when the previewPointer changes', async () => {
      const firstDeferred = createDeferred<CircuitPreviewResult>()
      const secondDeferred = createDeferred<CircuitPreviewResult>()

      mockLoadCircuitPreview
        .mockReturnValueOnce(firstDeferred.promise)
        .mockReturnValueOnce(secondDeferred.promise)

      const source = createSource()
      const { rerender } = render(
        <CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />,
      )

      expect(mockLoadCircuitPreview).toHaveBeenCalledTimes(1)

      await act(async () => {
        rerender(
          <CircuitPreview source={source} previewPointer="visuals/silverstone.json" circuitName="Silverstone" />,
        )
      })

      expect(mockLoadCircuitPreview).toHaveBeenCalledTimes(2)
      expect(mockLoadCircuitPreview).toHaveBeenLastCalledWith(source, 'visuals/silverstone.json')
    })

    test('remounts the SVG element when the resolved pathData changes', async () => {
      const firstResult: CircuitPreviewResult = Object.freeze({
        pathData: 'M 0 0 L 100 0 L 100 100 Z',
        viewBox: '0 0 100 100',
      })
      const secondResult: CircuitPreviewResult = Object.freeze({
        pathData: 'M 10 10 L 200 10 L 200 200 Z',
        viewBox: '0 0 210 210',
      })

      const firstDeferred = createDeferred<CircuitPreviewResult>()
      const secondDeferred = createDeferred<CircuitPreviewResult>()

      mockLoadCircuitPreview
        .mockReturnValueOnce(firstDeferred.promise)
        .mockReturnValueOnce(secondDeferred.promise)

      const source = createSource()
      const { rerender } = render(
        <CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />,
      )

      await act(async () => {
        firstDeferred.resolve(firstResult)
      })

      const firstSvg = document.querySelector('svg.circuit-preview__canvas')
      expect(firstSvg).toBeTruthy()

      await act(async () => {
        rerender(
          <CircuitPreview source={source} previewPointer="visuals/silverstone.json" circuitName="Silverstone" />,
        )
      })

      await act(async () => {
        secondDeferred.resolve(secondResult)
      })

      const secondSvg = document.querySelector('svg.circuit-preview__canvas')
      expect(secondSvg).toBeTruthy()
      // The SVG was remounted (different DOM node) because the pathData changed
      expect(secondSvg).not.toBe(firstSvg)
    })

    test('transitions from resolved to idle when pointer is removed', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      const { rerender } = render(
        <CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />,
      )

      await act(async () => {
        deferred.resolve(createSuccessResult())
      })

      expect(document.querySelector('.circuit-preview--resolved')).toBeTruthy()
      expect(document.querySelector('svg')).toBeTruthy()

      await act(async () => {
        rerender(
          <CircuitPreview source={source} previewPointer={null} circuitName="Monaco" />,
        )
      })

      expect(document.querySelector('.circuit-preview--idle')).toBeTruthy()
      expect(screen.getByText('Circuit preview unavailable')).toBeTruthy()
      expect(document.querySelector('svg')).toBeNull()
    })

    test('transitions from error to loading when a new pointer is provided', async () => {
      const firstDeferred = createDeferred<CircuitPreviewResult>()
      const secondDeferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview
        .mockReturnValueOnce(firstDeferred.promise)
        .mockReturnValueOnce(secondDeferred.promise)

      const source = createSource()
      const { rerender } = render(
        <CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />,
      )

      await act(async () => {
        firstDeferred.resolve(createErrorResult())
      })

      expect(document.querySelector('.circuit-preview--error')).toBeTruthy()
      expect(document.querySelector('svg')).toBeNull()

      await act(async () => {
        rerender(
          <CircuitPreview source={source} previewPointer="visuals/silverstone.json" circuitName="Silverstone" />,
        )
      })

      expect(document.querySelector('.circuit-preview--loading')).toBeTruthy()
      expect(screen.queryByText('Loading Silverstone circuit preview…')).toBeNull()
      expect(document.querySelector('.circuit-preview--loading')?.getAttribute('aria-busy')).toBe('true')
    })
  })

  describe('source change behaviour', () => {
    test('issues a new load request when the source prop changes', async () => {
      const firstDeferred = createDeferred<CircuitPreviewResult>()
      const secondDeferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview
        .mockReturnValueOnce(firstDeferred.promise)
        .mockReturnValueOnce(secondDeferred.promise)

      const sourceA = createSource()
      const sourceB = createSource()
      const { rerender } = render(
        <CircuitPreview source={sourceA} previewPointer="visuals/monaco.json" circuitName="Monaco" />,
      )

      expect(mockLoadCircuitPreview).toHaveBeenCalledTimes(1)
      expect(mockLoadCircuitPreview).toHaveBeenCalledWith(sourceA, 'visuals/monaco.json')

      await act(async () => {
        firstDeferred.resolve(createSuccessResult())
      })

      await act(async () => {
        rerender(
          <CircuitPreview source={sourceB} previewPointer="visuals/monaco.json" circuitName="Monaco" />,
        )
      })

      expect(mockLoadCircuitPreview).toHaveBeenCalledTimes(2)
      expect(mockLoadCircuitPreview).toHaveBeenLastCalledWith(sourceB, 'visuals/monaco.json')
    })

    test('ignores a stale resolution when the source changes mid-flight', async () => {
      const firstDeferred = createDeferred<CircuitPreviewResult>()
      const secondDeferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview
        .mockReturnValueOnce(firstDeferred.promise)
        .mockReturnValueOnce(secondDeferred.promise)

      const sourceA = createSource()
      const sourceB = createSource()
      const { rerender } = render(
        <CircuitPreview source={sourceA} previewPointer="visuals/monaco.json" circuitName="Monaco" />,
      )

      await act(async () => {
        rerender(
          <CircuitPreview source={sourceB} previewPointer="visuals/monaco.json" circuitName="Monaco" />,
        )
      })

      await act(async () => {
        firstDeferred.resolve(createSuccessResult())
      })

      // Stale resolution must be ignored — still loading
      expect(document.querySelector('.circuit-preview--loading')).toBeTruthy()
      expect(document.querySelector('svg')).toBeNull()

      await act(async () => {
        secondDeferred.resolve(createSuccessResult())
      })

      expect(document.querySelector('.circuit-preview--resolved')).toBeTruthy()
      expect(document.querySelector('svg')).toBeTruthy()
    })
  })

  describe('accessibility', () => {
    test('does not add a keyboard tab stop to the preview container', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      await act(async () => {
        deferred.resolve(createSuccessResult())
      })

      await waitFor(() => {
        expect(document.querySelector('svg.circuit-preview__canvas')).toBeTruthy()
      })

      const container = document.querySelector('.circuit-preview')
      expect(container?.getAttribute('tabindex')).toBeNull()

      const svg = document.querySelector('svg.circuit-preview__canvas')
      expect(svg).toBeTruthy()
      expect(svg?.getAttribute('tabindex')).toBeNull()
    })

    test('does not add a keyboard tab stop in idle state', () => {
      const source = createSource()
      render(<CircuitPreview source={source} previewPointer={null} circuitName="Monaco" />)

      const container = document.querySelector('.circuit-preview')
      expect(container?.getAttribute('tabindex')).toBeNull()
    })

    test('uses the circuit name in the accessible label for all states', () => {
      const source = createSource()

      const { unmount: unmount1 } = render(
        <CircuitPreview source={source} previewPointer={null} circuitName="Monaco" />,
      )
      expect(screen.getByText('Circuit preview unavailable')).toBeTruthy()
      unmount1()

      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const { unmount: unmount2 } = render(
        <CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />,
      )
      expect(screen.getByLabelText('Loading Monaco circuit preview')).toBeTruthy()
      expect(screen.queryByText('Loading Monaco circuit preview…')).toBeNull()
      unmount2()
    })

    test('uses the circuit name in the resolved SVG aria-label', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Silverstone" />)

      await act(async () => {
        deferred.resolve(createSuccessResult())
      })

      const svg = document.querySelector('svg.circuit-preview__canvas')
      expect(svg?.getAttribute('aria-label')).toBe('Silverstone circuit preview')
      const title = document.querySelector('svg title')
      expect(title?.textContent).toBe('Silverstone circuit preview')
    })
  })

  describe('BEM class structure', () => {
    test('applies the expected BEM classes in the idle state', () => {
      const source = createSource()
      render(<CircuitPreview source={source} previewPointer={null} circuitName="Monaco" />)

      expect(document.querySelector('.circuit-preview')).toBeTruthy()
      expect(document.querySelector('.circuit-preview--idle')).toBeTruthy()
      expect(document.querySelector('.circuit-preview__message')).toBeTruthy()
    })

    test('applies the expected BEM classes in the loading state', () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      expect(document.querySelector('.circuit-preview')).toBeTruthy()
      expect(document.querySelector('.circuit-preview--loading')).toBeTruthy()
      expect(document.querySelector('.circuit-preview__message')).toBeNull()
    })

    test('applies the expected BEM classes in the resolved state', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      await act(async () => {
        deferred.resolve(createSuccessResult())
      })

      expect(document.querySelector('.circuit-preview')).toBeTruthy()
      expect(document.querySelector('.circuit-preview--resolved')).toBeTruthy()
      expect(document.querySelector('.circuit-preview__canvas')).toBeTruthy()
      expect(document.querySelector('.circuit-preview__path')).toBeTruthy()
      expect(document.querySelector('.circuit-preview__message')).toBeNull()
    })

    test('applies the expected BEM classes in the error state', async () => {
      const deferred = createDeferred<CircuitPreviewResult>()
      mockLoadCircuitPreview.mockReturnValue(deferred.promise)

      const source = createSource()
      render(<CircuitPreview source={source} previewPointer="visuals/monaco.json" circuitName="Monaco" />)

      await act(async () => {
        deferred.resolve(createErrorResult())
      })

      expect(document.querySelector('.circuit-preview')).toBeTruthy()
      expect(document.querySelector('.circuit-preview--error')).toBeTruthy()
      expect(document.querySelector('.circuit-preview__message')).toBeTruthy()
    })
  })
})
