import { memo, useEffect, useRef, useState } from 'react'
import type { ReplaySource } from '../../data/replay/types'
import { loadCircuitPreview, type CircuitPreviewSuccess } from '../../geo/circuit-preview'

interface IdlePhase {
  readonly phase: 'idle'
}

interface LoadingPhase {
  readonly phase: 'loading'
}

interface ResolvedPhase {
  readonly phase: 'resolved'
  readonly result: CircuitPreviewSuccess
}

interface ErrorPhase {
  readonly phase: 'error'
  readonly message: string
}

type CircuitPreviewState = IdlePhase | LoadingPhase | ResolvedPhase | ErrorPhase

export interface CircuitPreviewProps {
  readonly source: ReplaySource
  readonly previewPointer: string | null | undefined
  readonly circuitName: string
}

/**
 * Accessible circuit preview that renders an SVG track outline with a one-time
 * draw animation driven by CSS classes.
 *
 * - Accepts an explicit `ReplaySource` and optional `previewPointer` string.
 * - No hidden fetch dependencies: the caller provides the source and pointer.
 * - Stale-request protection: a monotonically increasing request ID ensures
 *   that an earlier pointer resolution cannot overwrite a newer selection.
 * - The loader returns a discriminated result; the component handles loading,
 *   error, success, and absent-pointer fallback.
 * - The SVG path draw animation is CSS-class/state-driven: a React `key` on
 *   the SVG element remounts it when the resolved path changes, replaying the
 *   CSS animation exactly once. No JS path-length measurement is required.
 * - Reduced-motion is handled by a CSS `@media (prefers-reduced-motion)` query;
 *   no duplicate JS reduced-motion detection is needed.
 * - The preview is non-interactive; it does not add a keyboard tab stop.
 */
export const CircuitPreview = memo(function CircuitPreview({
  source,
  previewPointer,
  circuitName,
}: CircuitPreviewProps) {
  const [state, setState] = useState<CircuitPreviewState>(deriveInitialState(previewPointer))
  const requestIdRef = useRef(0)

  useEffect(() => {
    const thisRequest = ++requestIdRef.current

    if (typeof previewPointer !== 'string') {
      setState({ phase: 'idle' })
      return () => {
        requestIdRef.current += 1
      }
    }

    setState({ phase: 'loading' })

    loadCircuitPreview(source, previewPointer).then((result) => {
      if (requestIdRef.current !== thisRequest) return
      if ('error' in result) {
        setState({ phase: 'error', message: result.message })
      } else {
        setState({ phase: 'resolved', result })
      }
    })

    return () => {
      requestIdRef.current += 1
    }
  }, [source, previewPointer])

  return (
    <div className={`circuit-preview circuit-preview--${state.phase}`}>
      {state.phase === 'resolved' ? (
        <svg
          key={state.result.pathData}
          className="circuit-preview__canvas"
          role="img"
          aria-label={`${circuitName} circuit preview`}
          viewBox={state.result.viewBox}
          preserveAspectRatio="xMidYMid meet"
        >
          <title>{`${circuitName} circuit preview`}</title>
          <path className="circuit-preview__path" d={state.result.pathData} />
        </svg>
      ) : (
        <p className="circuit-preview__message" role="status">
          {describeStatusMessage(state, circuitName)}
        </p>
      )}
    </div>
  )
})

function deriveInitialState(previewPointer: string | null | undefined): CircuitPreviewState {
  return typeof previewPointer === 'string' ? { phase: 'loading' } : { phase: 'idle' }
}

function describeStatusMessage(state: IdlePhase | LoadingPhase | ErrorPhase, circuitName: string): string {
  switch (state.phase) {
    case 'idle':
      return 'Circuit preview unavailable'
    case 'loading':
      return `Loading ${circuitName} circuit preview…`
    case 'error':
      return state.message
  }
}
