import type { ReplayController, ReplayControllerSnapshot } from '../../../engine/replay'

export interface ThrottledReplayStore {
  readonly getSnapshot: () => ReplayControllerSnapshot
  readonly subscribe: (listener: () => void) => () => void
  readonly flush: () => void
  readonly dispose: () => void
}

/** Coalesces playing snapshots for React consumers without changing controller sampling. */
export function createThrottledReplayStore(controller: ReplayController, intervalMs = 125): ThrottledReplayStore {
  let snapshot = controller.getSnapshot()
  let pending: ReplayControllerSnapshot | null = null
  let timeout: ReturnType<typeof setTimeout> | null = null
  let unsubscribeController: (() => void) | null = null
  let disposed = false
  let lastReplay = snapshot.replay
  const listeners = new Set<() => void>()

  const notify = () => listeners.forEach((listener) => listener())
  const publish = (next: ReplayControllerSnapshot) => {
    const retained = retainLastReplay(next)
    if (snapshot === retained) return
    snapshot = retained
    notify()
  }
  const retainLastReplay = (next: ReplayControllerSnapshot): ReplayControllerSnapshot => {
    if (next.replay !== null) {
      lastReplay = next.replay
      return next
    }
    return lastReplay === null ? next : Object.freeze({ ...next, replay: lastReplay })
  }
  const flush = () => {
    if (timeout !== null) clearTimeout(timeout)
    timeout = null
    const next = pending ?? controller.getSnapshot()
    pending = null
    publish(next)
  }
  const schedule = () => {
    if (timeout === null) timeout = setTimeout(flush, intervalMs)
  }
  const handleControllerChange = () => {
    if (disposed) return
    const next = controller.getSnapshot()
    if (shouldPublishImmediately(snapshot, next)) {
      pending = null
      if (timeout !== null) clearTimeout(timeout)
      timeout = null
      publish(next)
      return
    }
    pending = next
    schedule()
  }
  const connect = () => {
    if (unsubscribeController !== null || disposed) return
    snapshot = retainLastReplay(controller.getSnapshot())
    unsubscribeController = controller.subscribe(handleControllerChange)
  }
  const disconnect = () => {
    unsubscribeController?.()
    unsubscribeController = null
    if (timeout !== null) clearTimeout(timeout)
    timeout = null
    pending = null
  }

  return Object.freeze({
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      if (disposed) return () => undefined
      listeners.add(listener)
      connect()
      return () => {
        listeners.delete(listener)
        if (listeners.size === 0) disconnect()
      }
    },
    flush,
    dispose: () => {
      if (disposed) return
      disposed = true
      disconnect()
      listeners.clear()
    },
  })
}

function shouldPublishImmediately(previous: ReplayControllerSnapshot, next: ReplayControllerSnapshot): boolean {
  return previous.status !== next.status
    || previous.error !== next.error
    || previous.isPlaying !== next.isPlaying
    || !next.isPlaying
}
