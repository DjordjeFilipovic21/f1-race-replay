import { useCallback, useEffect, useState } from 'react'
import { loadReplayIndex } from '../../../data/replay/loader'
import { createFetchSource } from '../../../data/replay/source'
import type { DriverMetadata, LapSectorSidecar, LapStart, PenaltySidecar, PitLossModel, QualifyingLapStatusSidecar, QualifyingSummary, QualifyingTimeline, ReplayIndex, SeasonMetadata, SessionMode, StintSummary, TelemetryCapabilities, TimelineSummary, TrackAssets, WeatherSidecar } from '../../../data/replay/types'
import { createReplayController, type CoordinateInterpolationStrategy, type ReplayController } from '../../../engine/replay'
import { isQualifyingSessionMode } from '../session-capabilities'

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
  readonly sessionMode: SessionMode
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
  readonly qualifyingSummary?: QualifyingSummary
  readonly qualifyingLapStatus?: QualifyingLapStatusSidecar
  readonly qualifyingTimeline?: QualifyingTimeline
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
        const startMs = selectReplayStartMs(index)
        controller = createReplayController({
          index,
          coordinateInterpolation,
          ...(startMs === index.manifest.chunks[0]?.startMs ? {} : { initialTimeMs: startMs }),
        })
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
    startMs: selectReplayStartMs(index),
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
    sessionMode: index.manifest.sessionMode,
    ...(index.qualifyingSummary === undefined ? {} : { qualifyingSummary: index.qualifyingSummary }),
    ...(index.qualifyingLapStatus === undefined ? {} : { qualifyingLapStatus: index.qualifyingLapStatus }),
    ...(index.qualifyingTimeline === undefined ? {} : { qualifyingTimeline: index.qualifyingTimeline }),
    ...(index.weatherSidecar === undefined ? {} : { weatherSidecar: index.weatherSidecar }),
    trackAssets: index.trackAssets,
    coordinateInterpolation,
  })
}

/** Keeps absolute engine timestamps while clipping qualifying presentation to Q1. */
export function selectReplayStartMs(index: ReplayIndex): number {
  if (!isQualifyingSessionMode(index.manifest.sessionMode) || index.lapSectorSidecar?.contractVersion !== 'v2') return index.manifest.chunks[0]?.startMs ?? 0
  return index.lapSectorSidecar.phaseBoundaries.find(({ phase }) => phase === 'Q1')?.startMs ?? index.manifest.chunks[0]?.startMs ?? 0
}

function getRequestedInterpolation(): CoordinateInterpolationStrategy {
  const requestedTrajectory = new URLSearchParams(globalThis.location?.search).get('trajectory')
  return requestedTrajectory === 'linear' ? 'linear' : 'smooth'
}
