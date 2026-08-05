import { memo, useEffect, useMemo, useSyncExternalStore } from 'react'
import type { DriverMetadata, LapSectorSidecar, PenaltySidecar, QualifyingLapStatusSidecar, QualifyingSummary, SessionMode } from '../../../data/replay/types'
import type { ReplayController } from '../../../engine/replay'
import { LiveLeaderboard } from './LiveLeaderboard'
import { createThrottledReplayStore } from '../state/throttled-replay-store'

const LEADERBOARD_REFRESH_INTERVAL_MS = 1_000

export interface LiveLeaderboardPanelProps {
  readonly controller: ReplayController
  readonly drivers: readonly DriverMetadata[]
  readonly refreshKey: number
  readonly selectedDriverId?: string | null
  readonly onDriverSelect?: (driverId: string) => void
  readonly lapSectorSidecar?: LapSectorSidecar | null
  readonly penaltySidecar?: PenaltySidecar
  readonly sessionMode?: SessionMode
  readonly qualifyingSummary?: QualifyingSummary | null
  readonly qualifyingLapStatus?: QualifyingLapStatusSidecar | null
  readonly replayEndMs?: number | null
}

/** Keeps the table responsive without reconciling every animation frame. */
export const LiveLeaderboardPanel = memo(function LiveLeaderboardPanel({ controller, drivers, refreshKey, selectedDriverId = null, onDriverSelect, lapSectorSidecar, penaltySidecar, sessionMode = 'race', qualifyingSummary, qualifyingLapStatus, replayEndMs }: LiveLeaderboardPanelProps) {
  const store = useMemo(() => createThrottledReplayStore(controller, LEADERBOARD_REFRESH_INTERVAL_MS), [controller])
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)

  useEffect(() => {
    store.flush()
  }, [refreshKey, store])

  return <LiveLeaderboard snapshot={snapshot.replay} drivers={drivers} selectedDriverId={selectedDriverId} onDriverSelect={onDriverSelect} lapSectorSidecar={lapSectorSidecar} penaltySidecar={penaltySidecar} sessionMode={sessionMode} qualifyingSummary={qualifyingSummary} qualifyingLapStatus={qualifyingLapStatus} replayEndMs={replayEndMs} />
})
