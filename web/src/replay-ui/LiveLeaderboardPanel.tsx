import { memo, useEffect, useMemo, useSyncExternalStore } from 'react'
import type { DriverMetadata } from '../replay-data/types'
import type { ReplayController } from '../replay-engine'
import { LiveLeaderboard } from './LiveLeaderboard'
import { createThrottledReplayStore } from './throttled-replay-store'

export interface LiveLeaderboardPanelProps {
  readonly controller: ReplayController
  readonly drivers: readonly DriverMetadata[]
  readonly refreshKey: number
}

/** Keeps the table responsive without reconciling every animation frame. */
export const LiveLeaderboardPanel = memo(function LiveLeaderboardPanel({ controller, drivers, refreshKey }: LiveLeaderboardPanelProps) {
  const store = useMemo(() => createThrottledReplayStore(controller), [controller])
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)

  useEffect(() => {
    store.flush()
  }, [refreshKey, store])

  return <LiveLeaderboard snapshot={snapshot.replay} drivers={drivers} />
})
