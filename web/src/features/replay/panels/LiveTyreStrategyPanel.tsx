import { memo, useEffect, useMemo, useSyncExternalStore } from 'react'
import type { DriverMetadata, PitLossModel, StintSummary } from '../../../data/replay/types'
import type { ReplayController } from '../../../engine/replay'
import { createThrottledReplayStore } from '../state/throttled-replay-store'
import { TyreStrategyPanel } from './TyreStrategyPanel'

const STRATEGY_REFRESH_INTERVAL_MS = 1_000

export interface LiveTyreStrategyPanelProps {
  readonly controller: ReplayController
  readonly drivers: readonly DriverMetadata[]
  readonly refreshKey: number
  readonly selectedDriverId: string | null
  readonly stintSummary?: StintSummary | null
  readonly pitLossModel?: PitLossModel | null
  readonly totalLaps?: number | null
}

/**
 * Throttles the replay snapshot consumed by the selected-driver tyre strategy
 * panel to 1 Hz wall-clock during playback, mirroring the live leaderboard
 * pattern. Seek-driven `refreshKey` changes flush immediately; driver-selection
 * changes re-render from the latest retained throttled snapshot without any
 * additional fetching.
 */
export const LiveTyreStrategyPanel = memo(function LiveTyreStrategyPanel({
  controller,
  drivers,
  refreshKey,
  selectedDriverId,
  stintSummary = null,
  pitLossModel = null,
  totalLaps = null,
}: LiveTyreStrategyPanelProps) {
  const store = useMemo(
    () => createThrottledReplayStore(controller, STRATEGY_REFRESH_INTERVAL_MS),
    [controller],
  )
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)

  useEffect(() => {
    store.flush()
  }, [refreshKey, store])

  return (
    <TyreStrategyPanel
      drivers={drivers}
      selectedDriverId={selectedDriverId}
      snapshot={snapshot.replay}
      stintSummary={stintSummary}
      pitLossModel={pitLossModel}
      totalLaps={totalLaps}
    />
  )
})
