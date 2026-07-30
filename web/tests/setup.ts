import { afterEach, beforeEach } from 'vitest'

class ResizeObserverStub implements ResizeObserver {
  disconnect(): void {}
  observe(): void {}
  unobserve(): void {}
}

beforeEach(clearBrowserStorage)
afterEach(clearBrowserStorage)

function clearBrowserStorage(): void {
  try {
    window.localStorage?.clear()
  } catch {
    // Storage can be unavailable in local jsdom or restricted browser contexts.
  }
}

if (globalThis.ResizeObserver === undefined) {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: ResizeObserverStub,
    writable: true,
  })
}

if (globalThis.HTMLCanvasElement !== undefined) {
  Object.defineProperty(globalThis.HTMLCanvasElement.prototype, 'getContext', {
    configurable: true,
    value: () => null,
    writable: true,
  })
}
