import { memo, useEffect, useMemo, useSyncExternalStore } from 'react'
import type { DriverMetadata } from '../replay-data/types'
import type { ReplayController } from '../replay-engine'
import { LiveLeaderboard } from './LiveLeaderboard'
import { createThrottledReplayStore } from './throttled-replay-store'

const LEADERBOARD_REFRESH_INTERVAL_MS = 1_000

export interface LiveLeaderboardPanelProps {
  readonly controller: ReplayController
  readonly drivers: readonly DriverMetadata[]
  readonly refreshKey: number
  readonly selectedDriverId?: string | null
  readonly onDriverSelect?: (driverId: string) => void
}

/** Keeps the table responsive without reconciling every animation frame. */
export const LiveLeaderboardPanel = memo(function LiveLeaderboardPanel({ controller, drivers, refreshKey, selectedDriverId = null, onDriverSelect }: LiveLeaderboardPanelProps) {
  const store = useMemo(() => createThrottledReplayStore(controller, LEADERBOARD_REFRESH_INTERVAL_MS), [controller])
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)

  useEffect(() => {
    store.flush()
  }, [refreshKey, store])

  return <LiveLeaderboard snapshot={snapshot.replay} drivers={drivers} selectedDriverId={selectedDriverId} onDriverSelect={onDriverSelect} />
})
