export type StoreListener = () => void

/** A minimal cached external store suitable for useSyncExternalStore adapters. */
export interface ReplayStore<T> {
  readonly getSnapshot: () => T
  readonly subscribe: (listener: StoreListener) => () => void
  readonly publish: (snapshot: T) => void
  readonly dispose: () => void
}

export function createReplayStore<T>(initialSnapshot: T): ReplayStore<T> {
  let snapshot = initialSnapshot
  let disposed = false
  const listeners = new Set<StoreListener>()

  const notify = (): void => { listeners.forEach((listener) => listener()) }

  return Object.freeze({
    getSnapshot: () => snapshot,
    subscribe: (listener: StoreListener) => {
      if (disposed) return () => undefined
      listeners.add(listener)
      return () => { listeners.delete(listener) }
    },
    publish: (nextSnapshot: T) => {
      if (disposed || Object.is(snapshot, nextSnapshot)) return
      snapshot = nextSnapshot
      notify()
    },
    dispose: () => {
      if (disposed) return
      disposed = true
      listeners.clear()
    },
  })
}
