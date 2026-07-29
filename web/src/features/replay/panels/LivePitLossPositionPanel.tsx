import { memo, useEffect, useMemo, useSyncExternalStore } from 'react'
import type { DriverMetadata, PitLossModel } from '../../../data/replay/types'
import type { ReplayController } from '../../../engine/replay'
import { createThrottledReplayStore } from '../state/throttled-replay-store'
import { PitLossPositionPanel } from './PitLossPositionPanel'

const PIT_LOSS_POSITION_REFRESH_INTERVAL_MS = 1_000

export interface LivePitLossPositionPanelProps {
  readonly controller: ReplayController
  readonly drivers: readonly DriverMetadata[]
  readonly refreshKey: number
  readonly selectedDriverId: string | null
  readonly pitLossModel?: PitLossModel | null
}

/**
 * Throttles the replay snapshot consumed by the pit loss position panel
 * to 1 Hz wall-clock during playback, mirroring the live leaderboard
 * pattern. Seek-driven `refreshKey` changes flush immediately; driver-selection
 * changes re-render from the latest retained throttled snapshot without any
 * additional fetching.
 */
export const LivePitLossPositionPanel = memo(function LivePitLossPositionPanel({
  controller,
  drivers,
  refreshKey,
  selectedDriverId,
  pitLossModel = null,
}: LivePitLossPositionPanelProps) {
  const store = useMemo(
    () => createThrottledReplayStore(controller, PIT_LOSS_POSITION_REFRESH_INTERVAL_MS),
    [controller],
  )
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot)

  useEffect(() => {
    store.flush()
  }, [refreshKey, store])

  return (
    <PitLossPositionPanel
      drivers={drivers}
      selectedDriverId={selectedDriverId}
      snapshot={snapshot.replay}
      pitLossModel={pitLossModel}
    />
  )
})
