import { useCallback, useEffect, useState } from 'react'
import { loadReplayIndex } from '../../../data/replay/loader'
import { createFetchSource } from '../../../data/replay/source'
import type { DriverMetadata, LapSectorSidecar, LapStart, PenaltySidecar, PitLossModel, ReplayIndex, SeasonMetadata, StintSummary, TelemetryCapabilities, TimelineSummary, TrackAssets, WeatherSidecar } from '../../../data/replay/types'
import { createReplayController, type CoordinateInterpolationStrategy, type ReplayController } from '../../../engine/replay'

export interface ReplayEntryOptions {
  readonly browserBaseUrl: string
  readonly browserPointerPath: string
  readonly coordinateInterpolation?: CoordinateInterpolationStrategy
}

export interface ReadyReplay {
  readonly controller: ReplayController
  readonly startMs: number
  readonly endMs: number
  readonly drivers: readonly DriverMetadata[]
  readonly lapStarts?: readonly LapStart[]
  readonly seasonMetadata?: SeasonMetadata
  readonly telemetryCapabilities?: TelemetryCapabilities
  readonly timelineSummary?: TimelineSummary
  readonly trackAssets: TrackAssets
  readonly coordinateInterpolation: CoordinateInterpolationStrategy
  readonly lapSectorSidecar?: LapSectorSidecar
  readonly stintSummary?: StintSummary
  readonly pitLossModel?: PitLossModel
  readonly penaltySidecar?: PenaltySidecar
  readonly weatherSidecar?: WeatherSidecar
}

export interface ReplayEntryState {
  readonly replay: ReadyReplay | null
  readonly error: unknown | null
  readonly retry: () => void
}

export function useReplayEntry({ browserBaseUrl, browserPointerPath, coordinateInterpolation: requestedInterpolation }: ReplayEntryOptions): ReplayEntryState {
  const [attempt, setAttempt] = useState(0)
  const [replay, setReplay] = useState<ReadyReplay | null>(null)
  const [error, setError] = useState<unknown | null>(null)

  useEffect(() => {
    let stale = false
    let controller: ReplayController | null = null
    setReplay(null)
    setError(null)

    const coordinateInterpolation = requestedInterpolation ?? getRequestedInterpolation()
    const initialize = async (): Promise<void> => {
      try {
        const index = await loadReplayIndex({ source: createFetchSource(browserBaseUrl), pointerPath: browserPointerPath })
        if (stale) return
        controller = createReplayController({ index, coordinateInterpolation })
        setReplay(createReadyReplay(index, controller, coordinateInterpolation))
      } catch (loadError: unknown) {
        if (!stale) setError(loadError)
      }
    }

    void initialize()

    return () => {
      stale = true
      controller?.dispose()
    }
  }, [attempt, browserPointerPath, requestedInterpolation, browserBaseUrl])

  const retry = useCallback(() => setAttempt((value) => value + 1), [])
  return { replay, error, retry }
}

function createReadyReplay(index: ReplayIndex, controller: ReplayController, coordinateInterpolation: CoordinateInterpolationStrategy): ReadyReplay {
  const firstChunk = index.manifest.chunks[0]
  const lastChunk = index.manifest.chunks.at(-1)
  if (firstChunk === undefined || lastChunk === undefined) throw new Error('Replay manifest has no chunks')
  return Object.freeze({
    controller,
    startMs: firstChunk.startMs,
    endMs: lastChunk.endMs,
    drivers: index.manifest.drivers,
    lapStarts: index.manifest.lapStarts,
    ...(index.seasonMetadata === undefined ? {} : { seasonMetadata: index.seasonMetadata }),
    ...(index.telemetryCapabilities === undefined ? {} : { telemetryCapabilities: index.telemetryCapabilities }),
    ...(index.timelineSummary === undefined ? {} : { timelineSummary: index.timelineSummary }),
    ...(index.lapSectorSidecar === undefined ? {} : { lapSectorSidecar: index.lapSectorSidecar }),
    ...(index.stintSummary === undefined ? {} : { stintSummary: index.stintSummary }),
    ...(index.pitLossModel === undefined ? {} : { pitLossModel: index.pitLossModel }),
    ...(index.penaltySidecar === undefined ? {} : { penaltySidecar: index.penaltySidecar }),
    ...(index.weatherSidecar === undefined ? {} : { weatherSidecar: index.weatherSidecar }),
    trackAssets: index.trackAssets,
    coordinateInterpolation,
  })
}

function getRequestedInterpolation(): CoordinateInterpolationStrategy {
  const requestedTrajectory = new URLSearchParams(globalThis.location?.search).get('trajectory')
  return requestedTrajectory === 'linear' ? 'linear' : 'smooth'
}
