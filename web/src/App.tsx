import { useEffect, useState } from 'react'
import { loadReplayIndex } from './replay-data/loader'
import { createFetchSource } from './replay-data/source'
import { createReplayController, type ReplayController } from './replay-engine'
import type { DriverMetadata } from './replay-data/types'
import { ReplayControls } from './replay-ui/ReplayControls'

interface ReadyReplay {
  readonly controller: ReplayController
  readonly startMs: number
  readonly endMs: number
  readonly drivers: readonly DriverMetadata[]
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
    void loadReplayIndex({ source: createFetchSource(baseUrl), pointerPath: 'browser-current.json' }).then(
      (index) => {
        if (stale) return
        controller = createReplayController({ index })
        const chunks = index.manifest.chunks
        setReplay({ controller, startMs: chunks[0].startMs, endMs: chunks[chunks.length - 1].endMs, drivers: index.manifest.drivers })
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
      {replay !== null && <ReplayControls {...replay} />}
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
