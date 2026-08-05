import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type FormEvent } from 'react'
import type { DriverMetadata, LapSectorSidecar, LapStart, PenaltySidecar, PitLossModel, QualifyingLapStatusSidecar, QualifyingSummary, QualifyingTimeline, ReplayEvent, SeasonMetadata, SessionMode, StintSummary, TelemetryCapabilities, TimelineSummary, TrackAssets, WeatherSidecar } from '../../../data/replay/types'
import type { CoordinateInterpolationStrategy, ReplayController } from '../../../engine/replay'
import { createSessionCapabilities, isQualifyingSessionMode, isRaceSessionMode } from '../session-capabilities'
import { DriverInfoPanel } from '../panels/DriverInfoPanel'
import { DriverTelemetryPanel } from '../panels/DriverTelemetryPanel'
import { LapAnalysisPanel } from '../panels/LapAnalysisPanel'
import { LiveLeaderboardPanel } from '../panels/LiveLeaderboardPanel'
import { LiveTrackMap } from '../panels/LiveTrackMap'
import { RaceControlPanel, RACE_CONTROL_MESSAGE_DURATION_MS, RACE_CONTROL_MESSAGE_EXIT_DURATION_MS } from '../panels/RaceControlPanel'
import { WeatherPanel } from '../panels/WeatherPanel'
import { LiveTyreStrategyPanel } from '../panels/LiveTyreStrategyPanel'
import { LivePitLossPositionPanel } from '../panels/LivePitLossPositionPanel'
import { selectLapSectorData } from '../selectors/lap-sector-selectors'
import { selectSectorColours } from '../selectors/sector-colour-selectors'
import { PlaybackControls } from '../playback/PlaybackControls'
import { ReplayWorkspace, type ReplayWorkspacePanel } from '../workspace/ReplayWorkspace'
import { ReplayHeaderMetrics } from './ReplayHeaderMetrics'

export { parseElapsedParts } from '../playback/ExactTimeEditor'

export interface ReplayControlsProps {
  readonly controller: ReplayController
  readonly startMs: number
  readonly endMs: number
  readonly drivers: readonly DriverMetadata[]
  readonly lapStarts?: readonly LapStart[]
  readonly seasonMetadata?: SeasonMetadata
  readonly telemetryCapabilities?: TelemetryCapabilities
  readonly timelineSummary?: TimelineSummary
  readonly trackAssets: TrackAssets
  readonly coordinateInterpolation?: CoordinateInterpolationStrategy
  readonly lapSectorSidecar?: LapSectorSidecar
  readonly stintSummary?: StintSummary
  readonly pitLossModel?: PitLossModel
  readonly penaltySidecar?: PenaltySidecar
  readonly sessionMode?: SessionMode
  readonly qualifyingSummary?: QualifyingSummary
  readonly qualifyingLapStatus?: QualifyingLapStatusSidecar
  readonly qualifyingTimeline?: QualifyingTimeline
  readonly weatherSidecar?: WeatherSidecar | null
}

/** A presentational adapter over the controller's cached external store. */
export function ReplayControls({ controller, startMs, endMs, drivers, lapStarts, seasonMetadata, telemetryCapabilities, timelineSummary, trackAssets, lapSectorSidecar, stintSummary, pitLossModel, penaltySidecar, sessionMode = 'race', qualifyingSummary, qualifyingLapStatus, qualifyingTimeline, weatherSidecar }: ReplayControlsProps) {
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot)
  const [seekPreviewMs, setSeekPreviewMs] = useState<number | null>(null)
  const [leaderboardRefreshKey, setLeaderboardRefreshKey] = useState(0)
  const [explicitSelectedDriverId, setExplicitSelectedDriverId] = useState<string | null>(null)
  const [activeRaceControlMessage, setActiveRaceControlMessage] = useState<ReplayEvent | null>(null)
  const [isRaceControlMessageExiting, setRaceControlMessageExiting] = useState(false)
  const seekPreviewRef = useRef<number | null>(null)
  const raceControlTimeRef = useRef(snapshot.timeMs)
  const isReady = snapshot.status === 'ready'
  const displayedTimeMs = seekPreviewMs ?? snapshot.timeMs
  const elapsedMs = relativeElapsedMs(displayedTimeMs, startMs, endMs)
  const durationMs = relativeElapsedMs(endMs, startMs, endMs)
  const currentLap = currentLapNumber(snapshot.replay)
  const selectedDriverId = selectDriverId(explicitSelectedDriverId, snapshot.replay, drivers, sessionMode, qualifyingSummary)
  const sessionCapabilities = useMemo(
    () => createSessionCapabilities(sessionMode, { lapSectorSidecar, timelineSummary, stintSummary, pitLossModel, qualifyingSummary, qualifyingLapStatus, qualifyingTimeline }),
    [lapSectorSidecar, pitLossModel, qualifyingLapStatus, qualifyingSummary, qualifyingTimeline, sessionMode, stintSummary, timelineSummary],
  )
  const qualifyingStatusForSelectors = sessionCapabilities.isQualifyingLike ? qualifyingLapStatus : undefined
  const lapSectorSelection = useMemo(
    () => selectLapSectorData(lapSectorSidecar, snapshot.timeMs, selectedDriverId ?? '', qualifyingStatusForSelectors),
    [lapSectorSidecar, qualifyingStatusForSelectors, snapshot.timeMs, selectedDriverId],
  )
  const sectorColourSelection = useMemo(
    () => selectSectorColours(lapSectorSidecar, snapshot.timeMs, selectedDriverId ?? '', qualifyingStatusForSelectors),
    [lapSectorSidecar, qualifyingStatusForSelectors, snapshot.timeMs, selectedDriverId],
  )
  const totalLaps = useMemo(() => deriveTotalLaps(lapStarts), [lapStarts])

  useEffect(() => {
    const previousTimeMs = raceControlTimeRef.current
    raceControlTimeRef.current = snapshot.timeMs
    if (snapshot.timeMs < previousTimeMs) {
      setActiveRaceControlMessage(null)
      setRaceControlMessageExiting(false)
      return
    }
    const latestEvent = snapshot.crossedEvents.at(-1)
    if (latestEvent !== undefined) {
      setRaceControlMessageExiting(false)
      setActiveRaceControlMessage(latestEvent)
    }
  }, [snapshot.crossedEvents, snapshot.timeMs])

  useEffect(() => {
    if (activeRaceControlMessage === null) return
    const timeout = window.setTimeout(() => setRaceControlMessageExiting(true), RACE_CONTROL_MESSAGE_DURATION_MS)
    return () => window.clearTimeout(timeout)
  }, [activeRaceControlMessage])

  useEffect(() => {
    if (!isRaceControlMessageExiting) return
    const timeout = window.setTimeout(() => {
      setActiveRaceControlMessage(null)
      setRaceControlMessageExiting(false)
    }, RACE_CONTROL_MESSAGE_EXIT_DURATION_MS)
    return () => window.clearTimeout(timeout)
  }, [isRaceControlMessageExiting])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!isSpaceKey(event) || event.repeat || isEditableTarget(event.target) || snapshot.status !== 'ready') return
      event.preventDefault()
      if (snapshot.isPlaying) controller.pause()
      else controller.start()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [controller, snapshot.isPlaying, snapshot.status])

  const handleSeekPreview = (event: FormEvent<HTMLInputElement>) => {
    const value = event.currentTarget.valueAsNumber
    seekPreviewRef.current = value
    setSeekPreviewMs(value)
  }

  const commitSeek = () => {
    const value = seekPreviewRef.current
    if (value === null) return
    seekPreviewRef.current = null
    setSeekPreviewMs(null)
    controller.seek(value)
    setLeaderboardRefreshKey((revision) => revision + 1)
  }

  const seek = (timeMs: number) => {
    seekPreviewRef.current = null
    setSeekPreviewMs(null)
    controller.seek(timeMs)
    setLeaderboardRefreshKey((revision) => revision + 1)
  }

  const panels = useMemo<readonly ReplayWorkspacePanel[]>(() => {
    const commonPanels: ReplayWorkspacePanel[] = [
      {
        id: 'player', label: 'Player', columns: 1,
        element: <PlaybackControls controller={controller} currentLap={currentLap} displayedTimeMs={displayedTimeMs} durationMs={durationMs} elapsedMs={elapsedMs} endMs={endMs} isReady={isReady} lapStarts={lapStarts} onCommitSeek={commitSeek} onSeek={seek} onSeekPreview={handleSeekPreview} snapshot={snapshot} startMs={startMs} timelineSummary={sessionCapabilities.canShowRaceTimeline ? timelineSummary : undefined} qualifyingTimeline={sessionCapabilities.canShowQualifyingTimeline ? qualifyingTimeline : undefined} sessionMode={sessionMode} qualifyingPhaseBoundaries={sessionCapabilities.isQualifyingLike ? (lapSectorSidecar?.contractVersion === 'v2' ? lapSectorSidecar.phaseBoundaries : []) : undefined} />,
      },
      {
        id: 'track-map', label: 'Track map', columns: 2,
        element: <LiveTrackMap trackAssets={trackAssets} controller={controller} drivers={drivers} selectedDriverId={selectedDriverId} sessionMode={sessionMode} lapSectorSidecar={lapSectorSidecar} qualifyingSummary={qualifyingSummary} qualifyingLapStatus={qualifyingLapStatus} qualifyingTimeline={sessionCapabilities.isQualifyingLike ? qualifyingTimeline : undefined} />,
      },
      {
        id: 'race-control', label: `${sessionCapabilities.label} control`, columns: 1,
        element: <RaceControlPanel snapshot={snapshot} activeMessage={activeRaceControlMessage} isMessageExiting={isRaceControlMessageExiting} sessionMode={sessionMode} />,
      },
      {
        id: 'weather', label: 'Weather', columns: 1,
        element: <WeatherPanel snapshot={snapshot} weatherSidecar={weatherSidecar} />,
      },
      {
        id: 'driver', label: 'Driver', columns: 1,
        element: <DriverInfoPanel drivers={drivers} selectedDriverId={selectedDriverId} snapshot={snapshot.replay} sessionMode={sessionMode} />,
      },
      {
        id: 'telemetry', label: 'Telemetry', columns: 1,
        element: <DriverTelemetryPanel drivers={drivers} selectedDriverId={selectedDriverId} seasonMetadata={seasonMetadata} telemetryCapabilities={telemetryCapabilities} snapshot={snapshot.replay} />,
      },
      {
        id: 'lap-analysis', label: 'Lap analysis', columns: 1,
        element: <LapAnalysisPanel drivers={drivers} selectedDriverId={selectedDriverId} lapSector={lapSectorSelection} sectorColours={sectorColourSelection} />,
      },
    ]

    const modePanels: ReplayWorkspacePanel[] = sessionCapabilities.isRaceLike
      ? [
        {
          id: 'leaderboard' as const, label: 'Leaderboard', columns: 1 as const,
          element: <LiveLeaderboardPanel controller={controller} drivers={drivers} refreshKey={leaderboardRefreshKey} selectedDriverId={selectedDriverId} onDriverSelect={setExplicitSelectedDriverId} lapSectorSidecar={lapSectorSidecar} penaltySidecar={penaltySidecar} sessionMode={sessionMode} replayEndMs={endMs} />,
        },
        ...commonPanels,
        ...(sessionCapabilities.canShowTyreStrategy ? [{
          id: 'strategy' as const, label: 'Strategy', columns: 2 as const,
          element: <LiveTyreStrategyPanel controller={controller} drivers={drivers} refreshKey={leaderboardRefreshKey} selectedDriverId={selectedDriverId} stintSummary={stintSummary} totalLaps={totalLaps} sessionMode={sessionMode} />,
        }] : []),
        ...(sessionCapabilities.canShowPitLoss ? [{
          id: 'pit-loss-position' as const, label: 'Pit loss position', columns: 1 as const,
          element: <LivePitLossPositionPanel controller={controller} drivers={drivers} refreshKey={leaderboardRefreshKey} selectedDriverId={selectedDriverId} pitLossModel={pitLossModel} />,
        }] : []),
      ]
      : [...commonPanels]

    if (!sessionCapabilities.isRaceLike && (sessionMode === 'practice' || sessionMode === 'testing') && stintSummary != null) {
      modePanels.push({
        id: 'strategy',
        label: 'Tyre runs',
        columns: 2,
        element: <LiveTyreStrategyPanel controller={controller} drivers={drivers} refreshKey={leaderboardRefreshKey} selectedDriverId={selectedDriverId} stintSummary={stintSummary} totalLaps={totalLaps} sessionMode={sessionMode} />,
      })
    }

    if (!sessionCapabilities.canShowQualifyingClassification) return modePanels
    return [
      ...modePanels,
      {
        id: 'leaderboard' as const,
        label: `${sessionCapabilities.label} classification`,
        columns: 1 as const,
          element: <LiveLeaderboardPanel controller={controller} drivers={drivers} refreshKey={leaderboardRefreshKey} selectedDriverId={selectedDriverId} onDriverSelect={setExplicitSelectedDriverId} lapSectorSidecar={lapSectorSidecar} sessionMode={sessionMode} replayEndMs={endMs} qualifyingSummary={qualifyingSummary} qualifyingLapStatus={qualifyingLapStatus} />,
      },
    ]
  }, [activeRaceControlMessage, commitSeek, controller, currentLap, displayedTimeMs, drivers, durationMs, elapsedMs, handleSeekPreview, isReady, isRaceControlMessageExiting, lapSectorSelection, lapSectorSidecar, lapStarts, leaderboardRefreshKey, penaltySidecar, pitLossModel, qualifyingLapStatus, qualifyingSummary, qualifyingTimeline, seasonMetadata, sectorColourSelection, sessionCapabilities, sessionMode, snapshot, startMs, seek, stintSummary, telemetryCapabilities, timelineSummary, totalLaps, trackAssets, weatherSidecar])

  return (
    <section className="replay-panel" aria-labelledby="replay-panel-title">
      <ReplayHeaderMetrics sessionMode={sessionMode} />
      <ReplayWorkspace panels={panels} />
    </section>
  )
}

function currentLapNumber(replay: ReturnType<ReplayController['getSnapshot']>['replay']): number | null {
  if (replay === null) return null
  const leaderId = replay.leaderboardOrder?.[0]
  const leaderLap = leaderId === undefined ? null : replay.drivers[leaderId]?.lap
  const validLaps = Object.values(replay.drivers)
    .map((driver) => driver.lap)
    .filter((lap): lap is number => typeof lap === 'number' && Number.isInteger(lap) && lap > 0)
  const lap = typeof leaderLap === 'number' && Number.isInteger(leaderLap) && leaderLap > 0
    ? leaderLap
    : Math.max(0, ...validLaps)
  return lap < 1 ? null : lap
}

function relativeElapsedMs(timeMs: number, startMs: number, endMs: number): number {
  return Math.min(Math.max(timeMs - startMs, 0), Math.max(endMs - startMs, 0))
}

function isSpaceKey(event: KeyboardEvent): boolean {
  return event.key === ' ' || event.code === 'Space'
}

function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT')
}

export function selectDriverId(explicitSelectedDriverId: string | null, replay: ReturnType<ReplayController['getSnapshot']>['replay'], drivers: readonly DriverMetadata[], sessionMode: SessionMode = 'race', _qualifyingSummary?: QualifyingSummary): string | null {
  if (explicitSelectedDriverId !== null) return explicitSelectedDriverId
  if (isRaceSessionMode(sessionMode)) return replay?.leaderboardOrder?.[0] ?? drivers[0]?.id ?? null
  if (isQualifyingSessionMode(sessionMode)) return drivers[0]?.id ?? null
  return drivers[0]?.id ?? null
}

export function deriveTotalLaps(lapStarts: readonly LapStart[] | undefined): number | null {
  if (lapStarts === undefined || lapStarts.length === 0) return null
  let max = -Infinity
  for (const entry of lapStarts) {
    if (typeof entry.lap === 'number' && Number.isInteger(entry.lap) && entry.lap > max) {
      max = entry.lap
    }
  }
  return max > 0 ? max : null
}
