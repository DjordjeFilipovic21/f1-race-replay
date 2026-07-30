/**
 * @vitest-environment jsdom
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, test, vi } from 'vitest'

interface DragDropProviderProps {
  readonly children: ReactNode
  readonly onDragStart?: (event: unknown) => void
  readonly onDragMove?: (event: unknown) => void
  readonly onDragEnd?: (event: unknown) => void
}

interface SortableOptions {
  readonly id: string
  readonly disabled?: boolean
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
const sortableCalls = vi.hoisted(() => [] as SortableOptions[])

vi.mock('@dnd-kit/react', () => ({
  DragDropProvider: ({ children, onDragStart, onDragMove, onDragEnd }: DragDropProviderProps) => <div>
    {children}
    <button type="button" onClick={() => { mockDragSource = dragEvent.operation.source; onDragStart?.(dragEvent) }}>Start drag</button>
    <button type="button" onClick={() => onDragMove?.(dragEvent)}>Preview drop</button>
    <button type="button" onClick={() => onDragMove?.(trackLaneDragEvent)}>Preview Track lane</button>
    <button type="button" onClick={() => onDragMove?.({ ...dragEvent, operation: { ...dragEvent.operation, source: null } })}>Invalidate preview</button>
    <button type="button" onClick={() => { onDragEnd?.(dragEvent); mockDragSource = null }}>Commit drop</button>
    <button type="button" onClick={() => { onDragEnd?.({ ...dragEvent, canceled: true }); mockDragSource = null }}>Cancel drag</button>
  </div>,
  DragOverlay: ({ children }: { readonly children: ReactNode | ((source: { readonly id: string; readonly index: number }) => ReactNode) }) => <>{typeof children === 'function' && mockDragSource !== null ? children(mockDragSource) : typeof children === 'function' ? null : children}</>,
}))

vi.mock('@dnd-kit/react/sortable', () => ({
  isSortable: (value: unknown): value is { readonly id: unknown; readonly index: number } => typeof value === 'object' && value !== null && 'index' in value,
  useSortable: (options: SortableOptions) => {
    sortableCalls.push(options)
    return { handleRef: () => undefined, ref: () => undefined }
  },
}))

import { animateReplayPanelFlip, computeReplayPanelFlipKeyframes, ReplayWorkspace, type ReplayWorkspacePanel } from '../../../../src/features/replay/workspace/ReplayWorkspace'
import type { ReplayWorkspaceStorage } from '../../../../src/features/replay/workspace/replay-workspace-preferences'

const appWorkspaceStyles = readFileSync(resolve(process.cwd(), 'src/styles/app-workspace.css'), 'utf8')
const responsiveStyles = readFileSync(resolve(process.cwd(), 'src/styles/responsive.css'), 'utf8')
const trackMapStyles = readFileSync(resolve(process.cwd(), 'src/styles/track-map.css'), 'utf8')

const panels: readonly ReplayWorkspacePanel[] = [
  { id: 'player', label: 'Player', columns: 1, element: <p>Player content</p> },
  { id: 'track-map', label: 'Track map', columns: 2, element: <p>Track content</p> },
  { id: 'leaderboard', label: 'Leaderboard', columns: 1, element: <p>Leaderboard content</p> },
  { id: 'driver', label: 'Driver', columns: 1, element: <p>Driver content</p> },
  { id: 'telemetry', label: 'Telemetry', columns: 1, element: <p>Telemetry content</p> },
]

afterEach(() => {
  cleanup()
  mockDragSource = null
  sortableCalls.length = 0
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

test('starts an unlocking FLIP below the final panel position', () => {
  expect(computeReplayPanelFlipKeyframes(
    { left: 100, top: 280, width: 100, height: 50 },
    { left: 120, top: 300, width: 200, height: 100 },
    'from-below',
  )).toEqual([
    { transform: 'translate(-20px, 20px) scale(0.5, 0.5)' },
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

test('renders prospective cross-column order and normalizes a visually equivalent commit to default', () => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    bottom: 200, height: 100, left: 0, right: 400, toJSON: () => ({}), top: 0, width: 400, x: 0, y: 0,
  })
  const { rerender } = render(<ReplayWorkspace panels={panels} />)

  fireEvent.click(screen.getByRole('button', { name: 'Preview drop' }))
   expect(document.querySelector('.replay-workspace__drop-preview')?.textContent).toContain('Drop telemetry panel')
  expect(document.querySelector('.replay-workspace__lane-highlight')).toBeTruthy()
  expect((document.querySelector('.replay-workspace__drop-preview') as HTMLElement).style.left).toBe('')
   expect(screen.getByRole('region', { name: 'Telemetry' }).style.getPropertyValue('--replay-panel-desktop-column')).toBe('4')

  fireEvent.click(screen.getByRole('button', { name: 'Invalidate preview' }))
  expect(document.querySelector('.replay-workspace__drop-preview')).toBeNull()
   expect(workspacePanelLabels()).toEqual(['Track map', 'Player', 'Leaderboard', 'Driver', 'Telemetry'])
   expect(screen.getByRole('region', { name: 'Telemetry' }).style.getPropertyValue('--replay-panel-desktop-column')).toBe('2')

   const driver = screen.getByRole('region', { name: 'Telemetry' })
  const workspace = document.querySelector('.replay-workspace') as HTMLElement
  setLayoutSlot(driver, workspace, { height: 120, left: 103, top: 160, width: 91 })
  fireEvent.click(screen.getByRole('button', { name: 'Preview Track lane' }))
   expect(workspacePanelLabels()).toEqual(['Track map', 'Telemetry', 'Player', 'Leaderboard', 'Driver'])
   expect(screen.getByRole('region', { name: 'Telemetry' }).style.getPropertyValue('--replay-panel-desktop-column')).toBe('2')
  expect((document.querySelector('.replay-workspace__drop-preview') as HTMLElement).style.cssText).toContain('left: 103px')
  expect((document.querySelector('.replay-workspace__drop-preview') as HTMLElement).style.cssText).toContain('top: 160px')

  setLayoutSlot(driver, workspace, { height: 160, left: 103, top: 220, width: 91 })
  rerender(<ReplayWorkspace panels={panels} />)
  expect((document.querySelector('.replay-workspace__drop-preview') as HTMLElement).style.cssText).toContain('top: 220px')
  expect((document.querySelector('.replay-workspace__drop-preview') as HTMLElement).style.cssText).toContain('height: 160px')
  fireEvent.click(screen.getByRole('button', { name: 'Commit drop' }))
  expect(document.querySelector('.replay-workspace__drop-preview')).toBeNull()
   expect(workspacePanelLabels()).toEqual(['Track map', 'Player', 'Leaderboard', 'Driver', 'Telemetry'])
   expect(screen.getByRole('region', { name: 'Telemetry' }).style.getPropertyValue('--replay-panel-desktop-column')).toBe('2')
  expect(screen.getByText('Default')).toBeTruthy()
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
  expect(document.querySelector('.replay-panel-drag-snapshot')?.getAttribute('data-workspace-mode')).toBe('unlocked')

  fireEvent.click(screen.getByRole('button', { name: 'Commit drop' }))
  expect(screen.getByRole('region', { name: 'Telemetry' }).classList.contains('replay-panel-frame--drag-source')).toBe(false)
  expect(document.querySelector('.replay-panel-drag-snapshot')).toBeNull()
})

test('keeps the default layout when a panel is released without a drag move', () => {
  render(<ReplayWorkspace panels={panels} />)

  fireEvent.click(screen.getByRole('button', { name: 'Start drag' }))
  fireEvent.click(screen.getByRole('button', { name: 'Commit drop' }))

  expect(screen.getByText('Default')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Reset layout' }).hasAttribute('disabled')).toBe(true)
})

test('keeps the default layout when a drag is canceled', () => {
  render(<ReplayWorkspace panels={panels} />)

  fireEvent.click(screen.getByRole('button', { name: 'Start drag' }))
  fireEvent.click(screen.getByRole('button', { name: 'Preview drop' }))
  fireEvent.click(screen.getByRole('button', { name: 'Cancel drag' }))

  expect(screen.getByText('Default')).toBeTruthy()
  expect(document.querySelector('.replay-workspace__drop-preview')).toBeNull()
})

test('commits a valid preview even when its center overlaps the source slot geometry', () => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    bottom: 400, height: 400, left: 0, right: 400, toJSON: () => ({}), top: 0, width: 400, x: 0, y: 0,
  })
  render(<ReplayWorkspace panels={panels} />)

  fireEvent.click(screen.getByRole('button', { name: 'Start drag' }))
  fireEvent.click(screen.getByRole('button', { name: 'Preview drop' }))
  fireEvent.click(screen.getByRole('button', { name: 'Commit drop' }))

  expect(screen.getByText('Custom')).toBeTruthy()
  expect(screen.getByRole('region', { name: 'Telemetry' }).style.getPropertyValue('--replay-panel-desktop-column')).toBe('4')
})

test('keeps the default layout when zero-sized geometry cannot resolve a destination', () => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    bottom: 0, height: 0, left: 0, right: 0, toJSON: () => ({}), top: 0, width: 0, x: 0, y: 0,
  })
  render(<ReplayWorkspace panels={panels} />)

  fireEvent.click(screen.getByRole('button', { name: 'Start drag' }))
  fireEvent.click(screen.getByRole('button', { name: 'Preview drop' }))
  fireEvent.click(screen.getByRole('button', { name: 'Commit drop' }))

  expect(screen.getByText('Default')).toBeTruthy()
  expect(document.querySelector('.replay-workspace__drop-preview')).toBeNull()
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

test('marks persisted panel choices as custom and resets them to default', () => {
  const storage = memoryStorage()
  const { unmount } = render(<ReplayWorkspace panels={panels} storage={storage} />)
  fireEvent.click(screen.getByRole('button', { name: 'Unpin Player panel' }))

  expect(screen.getByText('Custom')).toBeTruthy()
  expect(screen.getByText('4/5 panels')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Reset layout' }).hasAttribute('disabled')).toBe(false)
  unmount()

  render(<ReplayWorkspace panels={panels} storage={storage} />)
  expect(screen.queryByRole('region', { name: 'Player' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Reset layout' }))

  expect(screen.getByText('Default')).toBeTruthy()
  expect(screen.getByText('5/5 panels')).toBeTruthy()
  expect(screen.getByRole('region', { name: 'Player' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Reset layout' }).hasAttribute('disabled')).toBe(true)
})

test('animates visible panels from a custom layout back to their default positions', () => {
  const previousAnimateDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'animate')
  const animate = vi.fn(() => ({
    addEventListener: vi.fn(),
    cancel: vi.fn(),
    finished: new Promise<never>(() => undefined),
    removeEventListener: vi.fn(),
  }) as unknown as Animation)
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const panelIndex = panels.findIndex((panel) => panel.label === this.getAttribute('aria-label'))
    const visiblePanels = document.querySelectorAll('.replay-workspace > .replay-panel-frame').length
    const left = Math.max(0, panelIndex - (panels.length - visiblePanels)) * 100
    return { bottom: 100, height: 100, left, right: left + 100, toJSON: () => ({}), top: 0, width: 100, x: left, y: 0 } as DOMRect
  })

  try {
    render(<ReplayWorkspace panels={panels} />)
    fireEvent.click(screen.getByRole('button', { name: 'Unpin Player panel' }))
    animate.mockClear()

    fireEvent.click(screen.getByRole('button', { name: 'Reset layout' }))

    expect(animate).toHaveBeenCalled()
    expect(animate).toHaveBeenCalledWith(expect.any(Array), expect.objectContaining({ duration: 240 }))
  } finally {
    restorePrototypeDescriptor(HTMLElement.prototype, 'animate', previousAnimateDescriptor)
  }
})

test('resets immediately without FLIP animation when reduced motion is preferred', () => {
  const previousAnimateDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'animate')
  const previousMatchMediaDescriptor = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  const animate = vi.fn()
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate })
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn(() => ({ matches: true })) })

  try {
    render(<ReplayWorkspace panels={panels} />)
    fireEvent.click(screen.getByRole('button', { name: 'Unpin Player panel' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reset layout' }))

    expect(screen.getByRole('region', { name: 'Player' })).toBeTruthy()
    expect(animate).not.toHaveBeenCalled()
  } finally {
    restorePrototypeDescriptor(HTMLElement.prototype, 'animate', previousAnimateDescriptor)
    restorePrototypeDescriptor(window, 'matchMedia', previousMatchMediaDescriptor)
  }
})

test('restores a committed drag position from persisted preferences', () => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    bottom: 200, height: 100, left: 0, right: 400, toJSON: () => ({}), top: 0, width: 400, x: 0, y: 0,
  })
  const storage = memoryStorage()
  const { unmount } = render(<ReplayWorkspace panels={panels} storage={storage} />)

  fireEvent.click(screen.getByRole('button', { name: 'Preview drop' }))
  fireEvent.click(screen.getByRole('button', { name: 'Commit drop' }))
  expect(screen.getByRole('region', { name: 'Telemetry' }).style.getPropertyValue('--replay-panel-desktop-column')).toBe('4')
  unmount()

  render(<ReplayWorkspace panels={panels} storage={storage} />)
  expect(screen.getByRole('region', { name: 'Telemetry' }).style.getPropertyValue('--replay-panel-desktop-column')).toBe('4')
})

test('locks drag and pin editing without changing the saved custom layout', () => {
  const storage = memoryStorage()
  render(<ReplayWorkspace panels={panels} storage={storage} />)

  fireEvent.click(screen.getByRole('button', { name: 'Unpin Player panel' }))
  const lockToggle = screen.getByRole('button', { name: 'Lock workspace' })
  expect(lockToggle.getAttribute('aria-pressed')).toBe('false')
  expect(lockToggle.getAttribute('title')).toBe('Lock workspace')
  fireEvent.click(screen.getByRole('button', { name: 'Lock workspace' }))

  const workspace = document.querySelector('.replay-workspace') as HTMLElement
  expect(workspace.dataset.workspaceMode).toBe('locked')
  const unlockToggle = screen.getByRole('button', { name: 'Unlock workspace' })
  expect(unlockToggle.getAttribute('aria-pressed')).toBe('true')
  expect(unlockToggle.getAttribute('title')).toBe('Unlock workspace')
  expect(screen.queryByRole('region', { name: 'Player' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Move Telemetry panel' }).hasAttribute('disabled')).toBe(true)

  fireEvent.click(screen.getByRole('button', { name: 'Panel Manager' }))
  const manager = screen.getByRole('dialog', { name: 'Panel Manager' })
  const pinPlayer = within(manager).getByRole('button', { name: 'Pin Player panel' })
  expect(pinPlayer.hasAttribute('disabled')).toBe(true)
  fireEvent.click(pinPlayer)
  expect(screen.queryByRole('region', { name: 'Player' })).toBeNull()
  const telemetryRegion = screen.getByRole('region', { name: 'Telemetry' })
  const unpinTelemetry = within(telemetryRegion).getByRole('button', { name: 'Unpin Telemetry panel' })
  expect(unpinTelemetry.hasAttribute('disabled')).toBe(true)
  fireEvent.click(unpinTelemetry)
  expect(screen.getByRole('region', { name: 'Telemetry' })).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: 'Start drag' }))
  fireEvent.click(screen.getByRole('button', { name: 'Preview drop' }))
  fireEvent.click(screen.getByRole('button', { name: 'Commit drop' }))
  expect(document.querySelector('.replay-workspace__drop-preview')).toBeNull()
  expect(screen.getByRole('region', { name: 'Telemetry' }).style.getPropertyValue('--replay-panel-desktop-column')).toBe('2')

  fireEvent.click(screen.getByRole('button', { name: 'Unlock workspace' }))
  expect(workspace.dataset.workspaceMode).toBe('unlocked')
  expect(screen.queryByRole('region', { name: 'Player' })).toBeNull()
})

test('restores pin and drag editing after unlocking without changing the layout', () => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    bottom: 200, height: 100, left: 0, right: 400, toJSON: () => ({}), top: 0, width: 400, x: 0, y: 0,
  })
  render(<ReplayWorkspace panels={panels} />)

  fireEvent.click(screen.getByRole('button', { name: 'Lock workspace' }))
  fireEvent.click(screen.getByRole('button', { name: 'Unlock workspace' }))
  fireEvent.click(screen.getByRole('button', { name: 'Unpin Player panel' }))
  expect(screen.queryByRole('region', { name: 'Player' })).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Panel Manager' }))
  fireEvent.click(within(screen.getByRole('dialog', { name: 'Panel Manager' })).getByRole('button', { name: 'Pin Player panel' }))
  expect(screen.getByRole('region', { name: 'Player' })).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: 'Preview drop' }))
  expect(document.querySelector('.replay-workspace__drop-preview')).toBeTruthy()
})

test('passes lock state to every sortable panel and clears active drag state without committing', () => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    bottom: 200, height: 100, left: 0, right: 400, toJSON: () => ({}), top: 0, width: 400, x: 0, y: 0,
  })
  render(<ReplayWorkspace panels={panels} />)

  expectLatestSortableDisabled(false)
  fireEvent.click(screen.getByRole('button', { name: 'Start drag' }))
  fireEvent.click(screen.getByRole('button', { name: 'Preview drop' }))
  expect(screen.getByRole('region', { name: 'Telemetry' }).classList.contains('replay-panel-frame--drag-source')).toBe(true)
  expect(document.querySelector('.replay-panel-drag-snapshot')).toBeTruthy()
  expect(document.querySelector('.replay-workspace__drop-preview')).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: 'Lock workspace' }))
  expectLatestSortableDisabled(true)
  expect(screen.getByRole('region', { name: 'Telemetry' }).classList.contains('replay-panel-frame--drag-source')).toBe(false)
  expect(document.querySelector('.replay-panel-drag-snapshot')).toBeNull()
  expect(document.querySelector('.replay-workspace__drop-preview')).toBeNull()
  expect(screen.getByRole('region', { name: 'Telemetry' }).style.getPropertyValue('--replay-panel-desktop-column')).toBe('2')

  fireEvent.click(screen.getByRole('button', { name: 'Commit drop' }))
  expect(screen.getByText('Default')).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: 'Unlock workspace' }))
  expectLatestSortableDisabled(false)
})

test('persists locked mode across workspace remounts', () => {
  const storage = memoryStorage()
  const { unmount } = render(<ReplayWorkspace panels={panels} storage={storage} />)

  fireEvent.click(screen.getByRole('button', { name: 'Lock workspace' }))
  unmount()
  render(<ReplayWorkspace panels={panels} storage={storage} />)

  expect(document.querySelector('.replay-workspace')?.getAttribute('data-workspace-mode')).toBe('locked')
  expect(screen.getByRole('button', { name: 'Unlock workspace' })).toBeTruthy()
})

test('switches the shared workspace gap metric with lock mode', () => {
  render(<ReplayWorkspace panels={panels} />)

  const workspace = document.querySelector('.replay-workspace') as HTMLElement
  expect(workspace.style.getPropertyValue('--replay-workspace-gap')).toBe('12px')
  expect(workspace.classList.contains('replay-workspace--unlocked')).toBe(true)
  expect(workspace.dataset.workspaceMode).toBe('unlocked')

  fireEvent.click(screen.getByRole('button', { name: 'Lock workspace' }))
  expect(workspace.style.getPropertyValue('--replay-workspace-gap')).toBe('0px')
  expect(workspace.classList.contains('replay-workspace--locked')).toBe(true)
  expect(workspace.dataset.workspaceMode).toBe('locked')
  expect(workspace.querySelector('.replay-panel-frame__header')).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: 'Unlock workspace' }))
  expect(workspace.style.getPropertyValue('--replay-workspace-gap')).toBe('12px')
  expect(workspace.classList.contains('replay-workspace--unlocked')).toBe(true)
  expect(workspace.dataset.workspaceMode).toBe('unlocked')
})

test('separates the toolbar from the workspace surface', () => {
  const managerRule = appWorkspaceStyles.match(/\.replay-workspace__manager\s*\{([^}]*)\}/)?.[1] ?? ''

  expect(managerRule).toContain('border-bottom: 1px solid var(--border)')
  expect(managerRule).toContain('padding: .25rem 0 1rem')
})

test('animates panel geometry when switching workspace mode', () => {
  const previousAnimateDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'animate')
  const animatedRowSpans: string[] = []
  const animate = vi.fn(function (this: HTMLElement) {
    animatedRowSpans.push(this.style.getPropertyValue('--replay-panel-row-span'))
    return {
    addEventListener: vi.fn(),
    cancel: vi.fn(),
    finished: new Promise<never>(() => undefined),
    removeEventListener: vi.fn(),
    } as unknown as Animation
  })
  Object.defineProperty(HTMLElement.prototype, 'animate', { configurable: true, value: animate })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const index = panels.findIndex((panel) => panel.label === this.getAttribute('aria-label'))
    const isLocked = this.closest('.replay-workspace')?.getAttribute('data-workspace-mode') === 'locked'
    const top = Math.max(index, 0) * (isLocked ? 90 : 100)
    return { bottom: top + 80, height: 80, left: 0, right: 100, toJSON: () => ({}), top, width: 100, x: 0, y: top } as DOMRect
  })

  try {
    render(<ReplayWorkspace panels={panels} />)
    fireEvent.click(screen.getByRole('button', { name: 'Lock workspace' }))

    expect(animate).toHaveBeenCalled()
    expect(animate).toHaveBeenCalledWith(expect.any(Array), expect.objectContaining({ duration: 420 }))
    expect(animatedRowSpans.every((rowSpan) => rowSpan === '10')).toBe(true)
  } finally {
    restorePrototypeDescriptor(HTMLElement.prototype, 'animate', previousAnimateDescriptor)
  }
})

test('recalculates panel row spans atomically when the mode changes the grid gap', () => {
  // Arrange - measure each panel once so a mode change cannot rely on a later observer callback.
  const previousResizeObserver = globalThis.ResizeObserver
  const previousScrollHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight')
  let observeCalls = 0
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    bottom: 28, height: 28, left: 0, right: 100, toJSON: () => ({}), top: 0, width: 100, x: 0, y: 0,
  } as DOMRect)
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', { configurable: true, get: () => 44 })
  class ResizeObserverProbe implements ResizeObserver {
    constructor(private readonly callback: ResizeObserverCallback) {}

    disconnect(): void {}

    observe(): void {
      observeCalls += 1
      this.callback([], this)
    }

    unobserve(): void {}
  }
  Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, value: ResizeObserverProbe })

  try {
    render(<ReplayWorkspace panels={panels} />)
    const telemetryRegion = screen.getByRole('region', { name: 'Telemetry' })

    // Act - switch to zero-gap locked mode, then return to the normal gap.
    expect(observeCalls).toBe(panels.length)
    expect(telemetryRegion.style.getPropertyValue('--replay-panel-row-span')).toBe('3')
    fireEvent.click(screen.getByRole('button', { name: 'Lock workspace' }))
    expect(telemetryRegion.style.getPropertyValue('--replay-panel-row-span')).toBe('6')
    fireEvent.click(screen.getByRole('button', { name: 'Unlock workspace' }))

    // Assert - each transition synchronously reuses the measured height with the active mode's metric.
    expect(observeCalls).toBe(panels.length)
    expect(telemetryRegion.style.getPropertyValue('--replay-panel-row-span')).toBe('3')
  } finally {
    restorePrototypeDescriptor(HTMLElement.prototype, 'scrollHeight', previousScrollHeight)
    if (previousResizeObserver === undefined) Reflect.deleteProperty(globalThis, 'ResizeObserver')
    else Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, value: previousResizeObserver })
  }
})

test('collapses only locked drag chrome while retaining semantic panel content', () => {
  // Arrange - identify the locked CSS markers and render a panel with semantic content.
  const lockedHeaderRule = appWorkspaceStyles.match(/\.replay-workspace--locked \.replay-panel-frame__header\s*\{([^}]*)\}/)?.[1] ?? ''
  const lockedBodyRule = appWorkspaceStyles.match(/\.replay-workspace--locked \.replay-panel-frame__body\s*\{([^}]*)\}/)?.[1] ?? ''
  render(<ReplayWorkspace panels={panels} />)

  // Act - enter locked mode.
  fireEvent.click(screen.getByRole('button', { name: 'Lock workspace' }))

  // Assert - CSS hides header chrome, while the semantic controls and panel body remain in the DOM.
  const telemetryRegion = screen.getByRole('region', { name: 'Telemetry' })
  expect(lockedHeaderRule).toContain('max-height: 0')
  expect(lockedHeaderRule).toContain('opacity: 0')
  expect(lockedHeaderRule).toContain('pointer-events: none')
  expect(lockedBodyRule).toContain('background-color: #101316')
  expect(within(telemetryRegion).getByRole('button', { name: 'Move Telemetry panel' })).toBeTruthy()
  expect(within(telemetryRegion).getByText('Telemetry content')).toBeTruthy()
})

test('swaps drag chrome for internal gradient headers across workspace modes', () => {
  const gradientHeaders = ':is(.race-control-panel__header, .driver-telemetry-panel__header, .lap-analysis-panel__header, .tyre-strategy-panel__header, .pit-loss-position-panel__header)'
  const unlockedHeaderScope = ":is(.replay-workspace, .replay-panel-drag-snapshot)[data-workspace-mode='unlocked']"

  expect(appWorkspaceStyles).toContain(`${unlockedHeaderScope} ${gradientHeaders} { max-height: 0; opacity: 0;`)
  expect(appWorkspaceStyles).toContain(`${unlockedHeaderScope} .race-control-panel { padding-top: .75rem; }`)
  expect(appWorkspaceStyles).toContain(`:is(.replay-workspace, .replay-panel-drag-snapshot) ${gradientHeaders} { max-height: 6rem; opacity: 1;`)
  expect(appWorkspaceStyles).not.toContain('transition: max-height')
  expect(appWorkspaceStyles).not.toContain('padding-block 420ms')
})

test('marks each panel frame with a stable id and scopes locked surface merging away from Player', () => {
  const lockedSurfaceRule = appWorkspaceStyles.match(/\.replay-workspace--locked, \.replay-workspace--locked \.replay-panel-frame, \.replay-workspace--locked \.replay-workspace__empty\s*\{([^}]*)\}/)?.[1] ?? ''
  const nonPlayerBodyRule = appWorkspaceStyles.match(/\.replay-workspace--locked \.replay-panel-frame:not\(\[data-panel-id='player'\]\) \.replay-panel-frame__body\s*\{([^}]*)\}/)?.[1] ?? ''
  const lockedTrackMapRule = appWorkspaceStyles.match(/\.replay-workspace--locked \.replay-panel-frame\[data-panel-id='track-map'\] \.live-track-map__svg\s*\{([^}]*)\}/)?.[1] ?? ''
  const nonPlayerContentRule = appWorkspaceStyles.match(/\.replay-workspace--locked \.replay-panel-frame:not\(\[data-panel-id='player'\]\) \.replay-panel-frame__body > :not\(\.replay-error-boundary\)\s*\{([^}]*)\}/)?.[1] ?? ''
  render(<ReplayWorkspace panels={panels} />)

  expect(screen.getByRole('region', { name: 'Player' }).getAttribute('data-panel-id')).toBe('player')
  expect(screen.getByRole('region', { name: 'Telemetry' }).getAttribute('data-panel-id')).toBe('telemetry')
  expect(lockedSurfaceRule).toContain('background-color: #101316')
  expect(nonPlayerBodyRule).toContain('background-color: #101316')
  expect(nonPlayerBodyRule).toContain('padding: .5rem')
  expect(lockedTrackMapRule).toContain('border: 0')
  expect(lockedTrackMapRule).toContain('box-shadow: none')
  expect(trackMapStyles).toContain('rgb(27 35 41 / 0%) 68%')
  expect(nonPlayerContentRule).toContain('background: transparent !important')
  expect(nonPlayerContentRule).toContain('border: 0 !important')
  expect(nonPlayerContentRule).toContain('box-shadow: none !important')
  expect(appWorkspaceStyles).toContain(".replay-panel-frame__body > :not(.replay-error-boundary)")
  expect(appWorkspaceStyles).toContain(".replay-workspace--locked .replay-panel-frame__body { background-color: #101316;")
})

test('keeps responsive masonry CSS tied to the shared gap and row metrics', () => {
  // Arrange - extract the workspace rules from the supported responsive media queries.
  const tabletRule = responsiveStyles.match(/@media \(min-width: 768px\) \{[\s\S]*?\.replay-workspace\s*\{([^}]*)\}/)?.[1] ?? ''
  const desktopRule = responsiveStyles.match(/@media \(min-width: 1024px\) \{[\s\S]*?\.replay-workspace\s*\{([^}]*)\}/)?.[1] ?? ''

  // Act - inspect the declarations used by responsive workspace placement.
  const responsiveMetrics = `${tabletRule}${desktopRule}`

  // Assert - CSS and TypeScript use the same gap/row metrics and lane counts at each responsive tier.
  expect(responsiveMetrics).toContain('gap: var(--replay-workspace-gap)')
  expect(responsiveMetrics).toContain('grid-auto-rows: var(--replay-workspace-row-height)')
  expect(tabletRule).toContain('grid-template-columns: repeat(2, minmax(0, 1fr))')
  expect(desktopRule).toContain('grid-template-columns: repeat(4, minmax(0, 1fr))')
})

test('disables reset while locked and preserves the custom layout', () => {
  render(<ReplayWorkspace panels={panels} />)

  fireEvent.click(screen.getByRole('button', { name: 'Unpin Player panel' }))
  fireEvent.click(screen.getByRole('button', { name: 'Lock workspace' }))
  const resetLayout = screen.getByRole('button', { name: 'Reset layout' })

  expect(resetLayout.hasAttribute('disabled')).toBe(true)
  fireEvent.click(resetLayout)
  expect(screen.queryByRole('region', { name: 'Player' })).toBeNull()
  expect(screen.getByText('Custom')).toBeTruthy()
  expect(document.querySelector('.replay-workspace')?.getAttribute('data-workspace-mode')).toBe('locked')
  expect(screen.getByRole('button', { name: 'Unlock workspace' })).toBeTruthy()
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

test('requests fullscreen on the exact .replay-workspace grid when the toolbar button is activated', () => {
  const fs = mockFullscreenApi()
  try {
    render(<ReplayWorkspace panels={panels} />)
    const workspace = document.querySelector('.replay-workspace') as HTMLElement
    const workspaceRequestFullscreen = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(workspace, 'requestFullscreen', { configurable: true, value: workspaceRequestFullscreen })

    const button = screen.getByRole('button', { name: 'Enter fullscreen' })
    expect(button.hasAttribute('disabled')).toBe(false)
    fireEvent.click(button)

    expect(workspaceRequestFullscreen).toHaveBeenCalledTimes(1)
    expect(fs.requestFullscreen).not.toHaveBeenCalled()
  } finally {
    fs.restore()
  }
})

test('tracks fullscreen state through fullscreenchange and disables the entry control while active', () => {
  const fs = mockFullscreenApi()
  try {
    render(<ReplayWorkspace panels={panels} />)
    const workspace = document.querySelector('.replay-workspace') as HTMLElement
    const button = screen.getByRole('button', { name: 'Enter fullscreen' })
    expect(button.hasAttribute('disabled')).toBe(false)

    act(() => fs.setFullscreenElement(workspace))
    expect(button.hasAttribute('disabled')).toBe(true)

    act(() => fs.setFullscreenElement(null))
    expect(button.hasAttribute('disabled')).toBe(false)
  } finally {
    fs.restore()
  }
})

test('ignores fullscreenchange for a different element and does not disable the workspace entry control', () => {
  const fs = mockFullscreenApi()
  try {
    render(<ReplayWorkspace panels={panels} />)
    const foreignElement = document.createElement('div')
    const button = screen.getByRole('button', { name: 'Enter fullscreen' })

    act(() => fs.setFullscreenElement(foreignElement))
    expect(button.hasAttribute('disabled')).toBe(false)
  } finally {
    fs.restore()
  }
})

test('disables the fullscreen entry button when the API is unavailable', () => {
  const fs = mockFullscreenApi(false)
  try {
    render(<ReplayWorkspace panels={panels} />)
    const button = screen.getByRole('button', { name: 'Enter fullscreen' })
    expect(button.hasAttribute('disabled')).toBe(true)

    fireEvent.click(button)
    expect(fs.requestFullscreen).not.toHaveBeenCalled()
  } finally {
    fs.restore()
  }
})

test('reports a rejected fullscreen request without an unhandled rejection', async () => {
  const previousEnabledDescriptor = Object.getOwnPropertyDescriptor(document, 'fullscreenEnabled')
  const previousElementDescriptor = Object.getOwnPropertyDescriptor(document, 'fullscreenElement')
  const previousRequestFullscreen = Object.getOwnPropertyDescriptor(Element.prototype, 'requestFullscreen')
  const rejection = new DOMException('Fullscreen rejected', 'NotAllowedError')

  Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: true, writable: true })
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null, writable: true })
  Object.defineProperty(Element.prototype, 'requestFullscreen', { configurable: true, value: vi.fn().mockRejectedValue(rejection) })

  try {
    render(<ReplayWorkspace panels={panels} />)
    fireEvent.click(screen.getByRole('button', { name: 'Enter fullscreen' }))
    await act(async () => {})
    expect(screen.getByRole('alert').textContent).toBe('Fullscreen could not be started.')
  } finally {
    if (previousEnabledDescriptor === undefined) Reflect.deleteProperty(document, 'fullscreenEnabled')
    else Object.defineProperty(document, 'fullscreenEnabled', previousEnabledDescriptor)
    if (previousElementDescriptor === undefined) Reflect.deleteProperty(document, 'fullscreenElement')
    else Object.defineProperty(document, 'fullscreenElement', previousElementDescriptor)
    if (previousRequestFullscreen === undefined) Reflect.deleteProperty(Element.prototype, 'requestFullscreen')
    else Object.defineProperty(Element.prototype, 'requestFullscreen', previousRequestFullscreen)
  }
})

test('preserves locked mode and the custom layout through a fullscreen entry and native-exit cycle', () => {
  const fs = mockFullscreenApi()
  try {
    const storage = memoryStorage()
    render(<ReplayWorkspace panels={panels} storage={storage} />)

    fireEvent.click(screen.getByRole('button', { name: 'Unpin Player panel' }))
    fireEvent.click(screen.getByRole('button', { name: 'Lock workspace' }))
    const workspace = document.querySelector('.replay-workspace') as HTMLElement
    expect(workspace.dataset.workspaceMode).toBe('locked')
    expect(screen.queryByRole('region', { name: 'Player' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Enter fullscreen' }))
    act(() => fs.setFullscreenElement(workspace))
    expect(workspace.dataset.workspaceMode).toBe('locked')
    expect(screen.queryByRole('region', { name: 'Player' })).toBeNull()

    act(() => fs.setFullscreenElement(null))
    expect(workspace.dataset.workspaceMode).toBe('locked')
    expect(screen.queryByRole('region', { name: 'Player' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Unlock workspace' })).toBeTruthy()
  } finally {
    fs.restore()
  }
})

test('removes the fullscreenchange listener when the workspace unmounts', () => {
  const fs = mockFullscreenApi()
  try {
    const removeSpy = vi.spyOn(document, 'removeEventListener')
    const { unmount } = render(<ReplayWorkspace panels={panels} />)

    unmount()

    expect(removeSpy).toHaveBeenCalledWith('fullscreenchange', expect.any(Function))
    removeSpy.mockRestore()
  } finally {
    fs.restore()
  }
})

function workspacePanelLabels(): string[] {
  return Array.from(document.querySelectorAll('.replay-workspace > .replay-panel-frame')).map((panel) => panel.getAttribute('aria-label') ?? '')
}

function mockFullscreenApi(enabled = true) {
  const previousEnabledDescriptor = Object.getOwnPropertyDescriptor(document, 'fullscreenEnabled')
  const previousElementDescriptor = Object.getOwnPropertyDescriptor(document, 'fullscreenElement')
  const previousRequestFullscreen = Object.getOwnPropertyDescriptor(Element.prototype, 'requestFullscreen')

  Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: enabled, writable: true })
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null, writable: true })
  const requestFullscreen = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(Element.prototype, 'requestFullscreen', { configurable: true, value: requestFullscreen })

  const setFullscreenElement = (element: Element | null) => {
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: element, writable: true })
    document.dispatchEvent(new Event('fullscreenchange'))
  }

  const restore = () => {
    if (previousEnabledDescriptor === undefined) Reflect.deleteProperty(document, 'fullscreenEnabled')
    else Object.defineProperty(document, 'fullscreenEnabled', previousEnabledDescriptor)
    if (previousElementDescriptor === undefined) Reflect.deleteProperty(document, 'fullscreenElement')
    else Object.defineProperty(document, 'fullscreenElement', previousElementDescriptor)
    if (previousRequestFullscreen === undefined) Reflect.deleteProperty(Element.prototype, 'requestFullscreen')
    else Object.defineProperty(Element.prototype, 'requestFullscreen', previousRequestFullscreen)
  }

  return { requestFullscreen, setFullscreenElement, restore }
}

function expectLatestSortableDisabled(disabled: boolean): void {
  const latestById = new Map(sortableCalls.map(({ id, disabled: value }) => [id, value]))
  expect(panels.map(({ id }) => latestById.get(id))).toEqual(panels.map(() => disabled))
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
   expect(desktopPreview?.style.getPropertyValue('--replay-preview-column')).toBe('4')

  setViewportWidth(800)
  fireEvent(window, new Event('resize'))

  const tabletPreview = document.querySelector<HTMLElement>('.replay-workspace__drop-preview')
  expect(tabletPreview?.style.getPropertyValue('--replay-preview-column-count')).toBe('2')
  expect(tabletPreview?.style.getPropertyValue('--replay-preview-column')).toBe('2')
})

function setViewportWidth(width: number): void {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width, writable: true })
}

function restorePrototypeDescriptor(target: object, property: PropertyKey, descriptor: PropertyDescriptor | undefined): void {
  if (descriptor === undefined) Reflect.deleteProperty(target, property)
  else Object.defineProperty(target, property, descriptor)
}

function memoryStorage(): ReplayWorkspaceStorage {
  const values = new Map<string, string>()
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value) },
  }
}
