/**
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
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

import { ReplayWorkspace, type ReplayWorkspacePanel } from '../src/replay-ui/ReplayWorkspace'

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
  vi.restoreAllMocks()
  setViewportWidth(1024)
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
