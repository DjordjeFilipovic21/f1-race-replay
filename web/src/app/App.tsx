import { useEffect, useState } from 'react'
import { loadReplayIndex } from '../data/replay/loader'
import { createFetchSource } from '../data/replay/source'
import { createReplayController, type CoordinateInterpolationStrategy, type ReplayController } from '../engine/replay'
import type { DriverMetadata, LapSectorSidecar, LapStart, PitLossModel, StintSummary, TimelineSummary, TrackAssets } from '../data/replay/types'
import { ReplayControls } from '../features/replay/shell/ReplayControls'
import { ReplayErrorBoundary } from '../features/replay/shell/ReplayErrorBoundary'

interface ReadyReplay {
  readonly controller: ReplayController
  readonly startMs: number
  readonly endMs: number
  readonly drivers: readonly DriverMetadata[]
  readonly lapStarts?: readonly LapStart[]
  readonly timelineSummary?: TimelineSummary
  readonly trackAssets: TrackAssets
  readonly coordinateInterpolation: CoordinateInterpolationStrategy
  readonly lapSectorSidecar?: LapSectorSidecar
  readonly stintSummary?: StintSummary
  readonly pitLossModel?: PitLossModel
}

export default function App() {
  const [attempt, setAttempt] = useState(0)
  const [replay, setReplay] = useState<ReadyReplay | null>(null)
  const [error, setError] = useState<unknown | null>(null)

  useEffect(() => {
    let stale = false
    let controller: ReplayController | null = null
    setReplay(null)
    setError(null)

    const baseUrl = import.meta.env.VITE_REPLAY_DATA_BASE_URL ?? '/replay-data/'
    const requestedTrajectory = new URLSearchParams(globalThis.location.search).get('trajectory')
    const coordinateInterpolation: CoordinateInterpolationStrategy = requestedTrajectory === 'linear' ? 'linear' : 'smooth'
    void loadReplayIndex({ source: createFetchSource(baseUrl), pointerPath: 'browser-current.json' }).then(
      (index) => {
        if (stale) return
        controller = createReplayController({ index, coordinateInterpolation })
        const chunks = index.manifest.chunks
        setReplay({ controller, startMs: chunks[0].startMs, endMs: chunks[chunks.length - 1].endMs, drivers: index.manifest.drivers, lapStarts: index.manifest.lapStarts, ...(index.timelineSummary === undefined ? {} : { timelineSummary: index.timelineSummary }), ...(index.lapSectorSidecar === undefined ? {} : { lapSectorSidecar: index.lapSectorSidecar }), ...(index.stintSummary === undefined ? {} : { stintSummary: index.stintSummary }), ...(index.pitLossModel === undefined ? {} : { pitLossModel: index.pitLossModel }), trackAssets: index.trackAssets, coordinateInterpolation })
      },
      (loadError: unknown) => {
        if (!stale) setError(loadError)
      },
    )

    return () => {
      stale = true
      controller?.dispose()
    }
  }, [attempt])

  return (
    <main className="app-shell">
      {replay !== null && <ReplayErrorBoundary label="Replay workspace"><ReplayControls {...replay} /></ReplayErrorBoundary>}
      {replay === null && error === null && <p className="app-diagnostic" role="status" aria-label="Replay loading">Loading replay data…</p>}
      {error !== null && (
        <section className="app-diagnostic app-diagnostic--error" role="alert" aria-label="Replay loading error">
          <p>Replay data could not be initialized: {error instanceof Error ? error.message : 'Unknown error'}</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>Retry loading</button>
        </section>
      )}
    </main>
  )
}
