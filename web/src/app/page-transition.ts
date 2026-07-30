import { flushSync } from 'react-dom'

export type PageTransitionDirection = 'forward' | 'backward'

let activeTransition: ViewTransition | null = null

export function runPageTransition(direction: PageTransitionDirection, update: () => void): void {
  const startViewTransition = document.startViewTransition?.bind(document)
  if (startViewTransition === undefined || prefersReducedMotion()) {
    update()
    return
  }

  const root = document.documentElement
  root.dataset.pageTransitionDirection = direction
  try {
    const transition = startViewTransition(() => flushSync(update))
    activeTransition = transition
    const cleanUp = () => {
      if (activeTransition !== transition) return
      activeTransition = null
      delete root.dataset.pageTransitionDirection
    }
    void transition.finished.then(cleanUp, cleanUp)
  } catch {
    delete root.dataset.pageTransitionDirection
    update()
  }
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
