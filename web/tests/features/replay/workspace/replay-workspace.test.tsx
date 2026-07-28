/**
 * @vitest-environment jsdom
 */
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, test, vi } from 'vitest'

interface DragDropProviderProps {
  readonly children: ReactNode
  readonly onDragStart?: (event: unknown) => void
  readonly onDragMove?: (event: unknown) => void
  readonly onDragEnd?: (event: unknown) => void
}

const dragEvent = {
  canceled: false,
  operation: {
    source: { id: 'telemetry', index: 4 },
    shape: { current: { center: { x: 350, y: 350 } } },
  },
}

const trackLaneDragEvent = {
  canceled: false,
  operation: {
    source: { id: 'telemetry', index: 4 },
    shape: { current: { center: { x: 160, y: 350 } } },
  },
}

let mockDragSource: { readonly id: string; readonly index: number } | null = null

vi.mock('@dnd-kit/react', () => ({
  DragDropProvider: ({ children, onDragStart, onDragMove, onDragEnd }: DragDropProviderProps) => <div>
    {children}
    <button type="button" onClick={() => { mockDragSource = dragEvent.operation.source; onDragStart?.(dragEvent) }}>Start drag</button>
    <button type="button" onClick={() => onDragMove?.(dragEvent)}>Preview drop</button>
    <button type="button" onClick={() => onDragMove?.(trackLaneDragEvent)}>Preview Track lane</button>
    <button type="button" onClick={() => onDragMove?.({ ...dragEvent, operation: { ...dragEvent.operation, source: null } })}>Invalidate preview</button>
    <button type="button" onClick={() => { onDragEnd?.(dragEvent); mockDragSource = null }}>Commit drop</button>
  </div>,
  DragOverlay: ({ children }: { readonly children: ReactNode | ((source: { readonly id: string; readonly index: number }) => ReactNode) }) => <>{typeof children === 'function' && mockDragSource !== null ? children(mockDragSource) : typeof children === 'function' ? null : children}</>,
}))

vi.mock('@dnd-kit/react/sortable', () => ({
  isSortable: (value: unknown): value is { readonly id: unknown; readonly index: number } => typeof value === 'object' && value !== null && 'index' in value,
  useSortable: () => ({ handleRef: () => undefined, ref: () => undefined }),
}))

import { animateReplayPanelFlip, computeReplayPanelFlipKeyframes, ReplayWorkspace, type ReplayWorkspacePanel } from '../../../../src/features/replay/workspace/ReplayWorkspace'

const panels: readonly ReplayWorkspacePanel[] = [
  { id: 'player', label: 'Player', columns: 1, element: <p>Player content</p> },
  { id: 'track-map', label: 'Track map', columns: 2, element: <p>Track content</p> },
  { id: 'leaderboard', label: 'Leaderboard', columns: 1, element: <p>Leaderboard content</p> },
  { id: 'driver', label: 'Driver', columns: 1, element: <p>Driver content</p> },
  { id: 'telemetry', label: 'Telemetry', columns: 2, element: <p>Telemetry content</p> },
]

afterEach(() => {
  cleanup()
  mockDragSource = null
  vi.useRealTimers()
  vi.restoreAllMocks()
  setViewportWidth(1024)
})

test('computes a FLIP translation and size correction from the captured rectangles', () => {
  expect(computeReplayPanelFlipKeyframes(
    { left: 120, top: 300, width: 200, height: 100 },
    { left: 100, top: 280, width: 100, height: 50 },
  )).toEqual([
    { transform: 'translate(20px, 20px) scale(2, 2)' },
    { transform: 'translate(0px, 0px) scale(1, 1)' },
  ])
})

test('invokes WAAPI with restrained FLIP timing when available', () => {
  const previousDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'animate')
  const animation = {
    finished: Promise.resolve(),
  } as unknown as Animation
  const animate = vi.fn(() => animation)
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate })
  try {
    const element = document.createElement('div')
    expect(animateReplayPanelFlip(element, { left: 10, top: 20, width: 100, height: 80 }, { left: 0, top: 0, width: 100, height: 80 })).toBe(animation)
    expect(animate).toHaveBeenCalledWith([
      { transform: 'translate(10px, 20px) scale(1, 1)' },
      { transform: 'translate(0px, 0px) scale(1, 1)' },
    ], { duration: 240, easing: 'cubic-bezier(0.22, 1, 0.36, 1)', fill: 'forwards', id: 'replay-panel-flip' })
  } finally {
    restorePrototypeDescriptor(HTMLElement.prototype, 'animate', previousDescriptor)
  }
})

test('falls back to immediate CSS layout when WAAPI is unavailable', () => {
  const previousDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'animate')
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: undefined })
  try {
    expect(animateReplayPanelFlip(document.createElement('div'), { left: 10, top: 20, width: 100, height: 80 }, { left: 0, top: 0, width: 100, height: 80 })).toBeNull()
  } finally {
    restorePrototypeDescriptor(HTMLElement.prototype, 'animate', previousDescriptor)
  }
})

test('skips FLIP WAAPI motion when reduced motion is preferred', () => {
  const previousAnimateDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'animate')
  const previousMatchMediaDescriptor = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  const animate = vi.fn()
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate })
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn(() => ({ matches: true })) })
  try {
    expect(animateReplayPanelFlip(document.createElement('div'), { left: 10, top: 20, width: 100, height: 80 }, { left: 0, top: 0, width: 100, height: 80 })).toBeNull()
    expect(animate).not.toHaveBeenCalled()
  } finally {
    restorePrototypeDescriptor(HTMLElement.prototype, 'animate', previousAnimateDescriptor)
    restorePrototypeDescriptor(window, 'matchMedia', previousMatchMediaDescriptor)
  }
})

test('cancels prior FLIP animations before replacing them on a rapid unpin', () => {
  const previousAnimateDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'animate')
  const animations: Array<{ readonly cancel: ReturnType<typeof vi.fn> }> = []
  const animate = vi.fn(() => {
    const record = {
      addEventListener: vi.fn(),
      cancel: vi.fn(),
      finished: new Promise<never>(() => undefined),
      removeEventListener: vi.fn(),
    }
    animations.push(record)
    return record as unknown as Animation
  })
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate })
  const panelPositions = {
    Player: 0,
    'Track map': 1,
    Leaderboard: 2,
    Driver: 3,
    Telemetry: 4,
  }
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const label = this.getAttribute('aria-label')
    const position = panelPositions[label as keyof typeof panelPositions]
    const visibleCount = document.querySelectorAll('.replay-workspace > .replay-panel-frame').length
    const compactPosition = label === null || position === undefined ? 0 : Math.max(0, position - (5 - visibleCount))
    return { bottom: 100, height: 100, left: compactPosition * 100, right: 100, toJSON: () => ({}), top: 0, width: 100, x: compactPosition * 100, y: 0 } as DOMRect
  })
  try {
    render(<ReplayWorkspace panels={panels} />)
    fireEvent.click(screen.getByRole('button', { name: 'Unpin Player panel' }))
    const firstAnimations = [...animations]
    expect(firstAnimations.length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: 'Unpin Track map panel' }))

    expect(firstAnimations.every(({ cancel }) => cancel.mock.calls.length === 1)).toBe(true)
  } finally {
    restorePrototypeDescriptor(HTMLElement.prototype, 'animate', previousAnimateDescriptor)
  }
})

test('renders prospective cross-column order, restores invalid previews, and commits the exact displayed destination', () => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    bottom: 200, height: 100, left: 0, right: 400, toJSON: () => ({}), top: 0, width: 400, x: 0, y: 0,
  })
  const { rerender } = render(<ReplayWorkspace panels={panels} />)

  fireEvent.click(screen.getByRole('button', { name: 'Preview drop' }))
   expect(document.querySelector('.replay-workspace__drop-preview')?.textContent).toContain('Drop telemetry panel')
  expect(document.querySelector('.replay-workspace__lane-highlight')).toBeTruthy()
  expect((document.querySelector('.replay-workspace__drop-preview') as HTMLElement).style.left).toBe('')
   expect(screen.getByRole('region', { name: 'Telemetry' }).style.getPropertyValue('--replay-panel-desktop-column')).toBe('3')

  fireEvent.click(screen.getByRole('button', { name: 'Invalidate preview' }))
  expect(document.querySelector('.replay-workspace__drop-preview')).toBeNull()
   expect(workspacePanelLabels()).toEqual(['Player', 'Track map', 'Leaderboard', 'Driver', 'Telemetry'])
   expect(screen.getByRole('region', { name: 'Telemetry' }).style.getPropertyValue('--replay-panel-desktop-column')).toBe('1')

   const driver = screen.getByRole('region', { name: 'Telemetry' })
  const workspace = document.querySelector('.replay-workspace') as HTMLElement
  setLayoutSlot(driver, workspace, { height: 120, left: 103, top: 160, width: 91 })
  fireEvent.click(screen.getByRole('button', { name: 'Preview Track lane' }))
   expect(workspacePanelLabels()).toEqual(['Player', 'Track map', 'Telemetry', 'Leaderboard', 'Driver'])
   expect(screen.getByRole('region', { name: 'Telemetry' }).style.getPropertyValue('--replay-panel-desktop-column')).toBe('2')
  expect((document.querySelector('.replay-workspace__drop-preview') as HTMLElement).style.cssText).toContain('left: 103px')
  expect((document.querySelector('.replay-workspace__drop-preview') as HTMLElement).style.cssText).toContain('top: 160px')

  setLayoutSlot(driver, workspace, { height: 160, left: 103, top: 220, width: 91 })
  rerender(<ReplayWorkspace panels={panels} />)
  expect((document.querySelector('.replay-workspace__drop-preview') as HTMLElement).style.cssText).toContain('top: 220px')
  expect((document.querySelector('.replay-workspace__drop-preview') as HTMLElement).style.cssText).toContain('height: 160px')
  fireEvent.click(screen.getByRole('button', { name: 'Commit drop' }))
  expect(document.querySelector('.replay-workspace__drop-preview')).toBeNull()
   expect(workspacePanelLabels()).toEqual(['Player', 'Track map', 'Telemetry', 'Leaderboard', 'Driver'])
   expect(screen.getByRole('region', { name: 'Telemetry' }).style.getPropertyValue('--replay-panel-desktop-column')).toBe('2')
})

test('shows a static panel snapshot and blurs the source while dragging', () => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    bottom: 200, height: 100, left: 0, right: 400, toJSON: () => ({}), top: 0, width: 400, x: 0, y: 0,
  })
  render(<ReplayWorkspace panels={panels} />)

  fireEvent.click(screen.getByRole('button', { name: 'Start drag' }))

  expect(screen.getByRole('region', { name: 'Telemetry' }).classList.contains('replay-panel-frame--drag-source')).toBe(true)
  expect(document.querySelector('.replay-panel-drag-snapshot')?.textContent).toContain('Telemetry content')
  expect(document.querySelector('.replay-panel-drag-snapshot')?.getAttribute('aria-hidden')).toBe('true')

  fireEvent.click(screen.getByRole('button', { name: 'Commit drop' }))
  expect(screen.getByRole('region', { name: 'Telemetry' }).classList.contains('replay-panel-frame--drag-source')).toBe(false)
  expect(document.querySelector('.replay-panel-drag-snapshot')).toBeNull()
})

test('unpins a panel immediately and restores it from Panel Manager', () => {
  render(<ReplayWorkspace panels={panels} />)

  const unpinButton = screen.getByRole('button', { name: 'Unpin Player panel' })
  expect(unpinButton.classList.contains('replay-panel-unpin')).toBe(true)
  expect(unpinButton.hasAttribute('aria-pressed')).toBe(false)
  fireEvent.click(unpinButton)

  expect(screen.queryByRole('region', { name: 'Player' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Panel Manager' }).getAttribute('aria-expanded')).toBe('false')

  fireEvent.click(screen.getByRole('button', { name: 'Panel Manager' }))
  const manager = screen.getByRole('dialog', { name: 'Panel Manager' })
  expect(within(manager).getByText('Player')).toBeTruthy()
  const pinPlayer = within(manager).getByRole('button', { name: 'Pin Player panel' })
  expect(pinPlayer.getAttribute('aria-pressed')).toBe('false')
  fireEvent.click(pinPlayer)

  expect(screen.getByRole('region', { name: 'Player' })).toBeTruthy()
})

test('animates only a panel newly pinned through Panel Manager', () => {
  render(<ReplayWorkspace panels={panels} />)
  expect(screen.getByRole('region', { name: 'Player' }).classList.contains('replay-panel-frame--entering')).toBe(false)

  fireEvent.click(screen.getByRole('button', { name: 'Unpin Player panel' }))
  fireEvent.click(screen.getByRole('button', { name: 'Panel Manager' }))
  fireEvent.click(screen.getByRole('button', { name: 'Pin Player panel' }))

  expect(screen.getByRole('region', { name: 'Player' }).classList.contains('replay-panel-frame--entering')).toBe(true)
})

test('keeps multiple unpin snapshots inert while the workspace removes panels immediately', () => {
  vi.useFakeTimers()
  try {
    render(<ReplayWorkspace panels={panels} />)

    fireEvent.click(screen.getByRole('button', { name: 'Unpin Player panel' }))
    fireEvent.click(screen.getByRole('button', { name: 'Unpin Track map panel' }))

    expect(screen.queryByRole('region', { name: 'Player' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Track map' })).toBeNull()
    const snapshots = document.querySelectorAll<HTMLElement>('.replay-panel-exit-snapshot')
    expect(snapshots).toHaveLength(2)
    snapshots.forEach((snapshot) => {
      expect(snapshot.getAttribute('aria-hidden')).toBe('true')
      expect(snapshot.hasAttribute('inert')).toBe(true)
    })

    act(() => vi.advanceTimersByTime(180))
    expect(document.querySelectorAll('.replay-panel-exit-snapshot')).toHaveLength(0)
  } finally {
    vi.useRealTimers()
  }
})

test('cleans pin motion immediately when reduced motion is preferred', () => {
  const previousDescriptor = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn(() => ({ matches: true })) })
  try {
    render(<ReplayWorkspace panels={panels} />)
    fireEvent.click(screen.getByRole('button', { name: 'Unpin Player panel' }))
    expect(document.querySelector('.replay-panel-exit-snapshot')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Panel Manager' }))
    fireEvent.click(screen.getByRole('button', { name: 'Pin Player panel' }))
    expect(screen.getByRole('region', { name: 'Player' }).classList.contains('replay-panel-frame--entering')).toBe(false)
  } finally {
    if (previousDescriptor === undefined) Reflect.deleteProperty(window, 'matchMedia')
    else Object.defineProperty(window, 'matchMedia', previousDescriptor)
  }
})

test('opens Panel Manager from the empty workspace and dismisses it with Escape or an outside click', () => {
  render(<ReplayWorkspace panels={panels} />)

  panels.forEach((panel) => fireEvent.click(screen.getByRole('button', { name: `Unpin ${panel.label} panel` })))
  expect(screen.getByRole('status').textContent).toContain('No panels pinned')

  fireEvent.click(screen.getByRole('button', { name: 'Open Panel Manager' }))
  expect(screen.getByRole('dialog', { name: 'Panel Manager' })).toBeTruthy()
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close Panel Manager' }))
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog', { name: 'Panel Manager' })).toBeNull()
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Panel Manager' }))

  fireEvent.click(screen.getByRole('button', { name: 'Panel Manager' }))
  fireEvent.click(screen.getByRole('button', { name: 'Close Panel Manager' }))
  expect(screen.queryByRole('dialog', { name: 'Panel Manager' })).toBeNull()
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Panel Manager' }))

  fireEvent.click(screen.getByRole('button', { name: 'Panel Manager' }))
  fireEvent.pointerDown(document.body)
  expect(screen.queryByRole('dialog', { name: 'Panel Manager' })).toBeNull()
})

test('contains a panel render failure and retries only the failed panel', () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
  let shouldThrow = true
  const UnstablePanel = () => {
    if (shouldThrow) throw new Error('Telemetry renderer failed')
    return <p>Recovered content</p>
  }
  const panelsWithFailure: readonly ReplayWorkspacePanel[] = [
    { ...panels[0], element: <UnstablePanel /> },
    panels[1],
  ]

  render(<ReplayWorkspace panels={panelsWithFailure} />)

  expect(screen.getByRole('alert', { name: 'Player panel error' }).textContent).toContain('Telemetry renderer failed')
  expect(screen.getByText('Track content')).toBeTruthy()

  shouldThrow = false
  fireEvent.click(screen.getByRole('button', { name: 'Retry player panel' }))

  expect(screen.getByText('Recovered content')).toBeTruthy()
  expect(screen.queryByRole('alert', { name: 'Player panel error' })).toBeNull()
})

function workspacePanelLabels(): string[] {
  return Array.from(document.querySelectorAll('.replay-workspace > .replay-panel-frame')).map((panel) => panel.getAttribute('aria-label') ?? '')
}

function setLayoutSlot(element: HTMLElement, workspace: HTMLElement, slot: { readonly height: number; readonly left: number; readonly top: number; readonly width: number }): void {
  Object.defineProperties(element, {
    offsetHeight: { configurable: true, value: slot.height },
    offsetLeft: { configurable: true, value: slot.left },
    offsetParent: { configurable: true, value: workspace },
    offsetTop: { configurable: true, value: slot.top },
    offsetWidth: { configurable: true, value: slot.width },
  })
}

test('recomputes the active drop preview when the workspace breakpoint changes', () => {
  setViewportWidth(1200)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    bottom: 200, height: 100, left: 0, right: 400, toJSON: () => ({}), top: 0, width: 400, x: 0, y: 0,
  })
  render(<ReplayWorkspace panels={panels} />)

  fireEvent.click(screen.getByRole('button', { name: 'Preview drop' }))
  const desktopPreview = document.querySelector<HTMLElement>('.replay-workspace__drop-preview')
  expect(desktopPreview?.style.getPropertyValue('--replay-preview-column-count')).toBe('4')
   expect(desktopPreview?.style.getPropertyValue('--replay-preview-column')).toBe('3')

  setViewportWidth(800)
  fireEvent(window, new Event('resize'))

  const tabletPreview = document.querySelector<HTMLElement>('.replay-workspace__drop-preview')
  expect(tabletPreview?.style.getPropertyValue('--replay-preview-column-count')).toBe('2')
  expect(tabletPreview?.style.getPropertyValue('--replay-preview-column')).toBe('1')
})

function setViewportWidth(width: number): void {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width, writable: true })
}

function restorePrototypeDescriptor(target: object, property: PropertyKey, descriptor: PropertyDescriptor | undefined): void {
  if (descriptor === undefined) Reflect.deleteProperty(target, property)
  else Object.defineProperty(target, property, descriptor)
}
