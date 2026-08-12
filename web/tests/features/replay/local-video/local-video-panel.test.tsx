/**
 * @vitest-environment jsdom
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { ReplayController, ReplayControllerSnapshot } from '../../../../src/engine/replay'
import type { LocalVideoAdapterSnapshot, LocalVideoMediaError } from '../../../../src/features/replay/local-video/local-video-adapter'
import type { LocalVideoSyncCoordinatorSnapshot } from '../../../../src/features/replay/local-video/local-video-sync-coordinator'
import type { LocalVideoSyncModel } from '../../../../src/features/replay/local-video/local-video-sync-model'

const harness = vi.hoisted(() => {
  const hoistedReplaySnapshot: ReplayControllerSnapshot = {
    status: 'ready', timeMs: 1_500, speed: 1, isPlaying: false, committedSeekRevision: 0, crossedEvents: [], error: null,
    replay: null,
  }
  const hoistedSyncedModel: LocalVideoSyncModel = {
    status: 'synced', anchor: { replayTimeMs: 1_500, videoTimeMs: 500, rate: 1 },
  }
  const createSnapshot = (overrides: Partial<LocalVideoSyncCoordinatorSnapshot> = {}): LocalVideoSyncCoordinatorSnapshot => {
    const video: LocalVideoAdapterSnapshot = {
      currentTimeMs: 500, durationMs: null, metadataReady: false, isPlaying: false, isEnded: false, playbackRate: 1, sourceUrl: null, error: null,
    }
    return {
      status: 'unsynced', syncStatus: 'unsynced', isLinked: false, model: { status: 'unsynced', anchor: null },
      mapping: { status: 'unsynced' }, videoMapping: { status: 'unsynced' }, mappedVideoTimeMs: null, replay: hoistedReplaySnapshot, video, error: null, ...overrides,
    }
  }
  let coordinatorListener: (() => void) | null = null
  let adapterListener: (() => void) | null = null
  let snapshot = createSnapshot()

  const adapter = {
    getSnapshot: () => snapshot.video,
    subscribe: vi.fn((listener: () => void) => {
      adapterListener = listener
      return () => { adapterListener = null }
    }),
    setFile: vi.fn(),
    play: vi.fn(async () => true),
    pause: vi.fn(),
    seek: vi.fn(),
    setRate: vi.fn(),
    dispose: vi.fn(),
  }
  const coordinator = {
    getSnapshot: () => snapshot,
    subscribe: vi.fn((listener: () => void) => {
      coordinatorListener = listener
      return () => { coordinatorListener = null }
    }),
    alignCurrent: vi.fn(() => {
      snapshot = {
        ...snapshot,
        status: 'synced',
        syncStatus: 'synced',
        isLinked: true,
        model: hoistedSyncedModel,
        mapping: { status: 'mapped', timeMs: snapshot.video.currentTimeMs },
        mappedVideoTimeMs: snapshot.video.currentTimeMs,
      }
      coordinatorListener?.()
      return hoistedSyncedModel
    }),
    align: vi.fn(),
    adjustAlignment: vi.fn((deltaMs: number) => {
      snapshot = {
        ...snapshot,
        status: 'synced',
        syncStatus: 'synced',
        isLinked: true,
        model: {
          status: 'synced',
          anchor: { replayTimeMs: 1_500, videoTimeMs: 500 + deltaMs, rate: 1 },
        },
      }
      coordinatorListener?.()
      return snapshot.model
    }),
    adjust: vi.fn(),
    reset: vi.fn(() => {
      snapshot = {
        ...snapshot,
        status: 'unsynced',
        syncStatus: 'unsynced',
        isLinked: false,
        model: { status: 'unsynced', anchor: null },
        mapping: { status: 'unsynced' },
        mappedVideoTimeMs: null,
      }
      coordinatorListener?.()
    }),
    start: vi.fn(),
    pause: vi.fn(),
    seek: vi.fn(),
    setSpeed: vi.fn(),
    commitVideoSeek: vi.fn(() => ({ status: 'mapped', timeMs: 1_500 })),
    dispose: vi.fn(),
  }

  return {
    adapter,
    coordinator,
    replaySnapshot: hoistedReplaySnapshot,
    syncedModel: hoistedSyncedModel,
    createLocalVideoAdapter: vi.fn(() => adapter),
    createLocalVideoSyncCoordinator: vi.fn(() => coordinator),
    setSnapshot: (next: Partial<LocalVideoSyncCoordinatorSnapshot>) => {
      snapshot = createSnapshot({ ...snapshot, ...next })
      coordinatorListener?.()
      adapterListener?.()
    },
    reset: () => {
      snapshot = createSnapshot()
      coordinatorListener = null
      adapterListener = null
      vi.clearAllMocks()
    },
  }
})

vi.mock('../../../../src/features/replay/local-video/local-video-adapter', () => ({
  createLocalVideoAdapter: harness.createLocalVideoAdapter,
}))
vi.mock('../../../../src/features/replay/local-video/local-video-sync-coordinator', () => ({
  createLocalVideoSyncCoordinator: harness.createLocalVideoSyncCoordinator,
}))

import { LocalVideoPanel } from '../../../../src/features/replay/local-video/LocalVideoPanel'

const replaySnapshot = harness.replaySnapshot
const syncedModel = harness.syncedModel

const localVideoStyles = readFileSync(resolve(process.cwd(), 'src/styles/panels.css'), 'utf8')
const responsiveStyles = readFileSync(resolve(process.cwd(), 'src/styles/responsive.css'), 'utf8')

afterEach(() => {
  cleanup()
  harness.reset()
})

test('renders the local import without header description, privacy note, or upload controls', () => {
  renderPanel()
  const panel = localVideoPanel()

  expect(panel.getByRole('heading', { name: 'Local video replay' })).toBeTruthy()
  expect(panel.getByRole('button', { name: 'Select local video' })).toBeTruthy()
  expect(panel.getByLabelText('Local video file')).toBeTruthy()
  expect(panel.queryByText('Browser-only source')).toBeNull()
  expect(panel.queryByText(/No file path or video bytes are sent or saved/i)).toBeNull()
  expect(panel.queryByRole('textbox')).toBeNull()
  expect(panel.queryByRole('button', { name: /upload/i })).toBeNull()
})

test('shows native video controls and omits removed custom transport controls', () => {
  renderPanel()
  const panel = localVideoPanel()
  const video = panel.getByLabelText('Selected local replay video')

  expect(video.hasAttribute('controls')).toBe(true)
  expect(panel.queryByRole('slider')).toBeNull()
  expect(panel.queryByRole('button', { name: /play linked playback/i })).toBeNull()
  expect(panel.queryByRole('button', { name: /local video playback speed/i })).toBeNull()
  expect(panel.queryByRole('button', { name: 'Align current positions' })).toBeNull()
  expect(panel.queryByRole('button', { name: 'Reset alignment' })).toBeNull()
  expect(panel.queryByRole('group', { name: 'Fine sync adjustment' })).toBeNull()
})

test('keeps the file picker button visible before selection and after clearing', () => {
  renderPanel()
  const panel = localVideoPanel()
  const input = panel.getByLabelText('Local video file')

  fireEvent.change(input, { target: { files: [videoFile()] } })
  expect(panel.queryByRole('button', { name: 'Select local video' })).toBeNull()
  expect(panel.getByText('race.mp4').parentElement?.textContent).toContain('Selected:')

  fireEvent.click(panel.getByRole('button', { name: 'Clear and reselect' }))
  expect(panel.getByRole('button', { name: 'Select local video' })).toBeTruthy()
  expect(panel.queryByText('race.mp4')).toBeNull()
  expect(panel.getByText('Select a video to begin.')).toBeTruthy()
})

test('gives the visible file picker button focus and delegates activation to the hidden input', () => {
  renderPanel()
  const panel = localVideoPanel()
  const button = panel.getByRole('button', { name: 'Select local video' })
  const input = panel.getByLabelText('Local video file') as HTMLInputElement
  const click = vi.spyOn(input, 'click')

  button.focus()
  expect(document.activeElement).toBe(button)
  fireEvent.click(button)
  expect(click).toHaveBeenCalledOnce()
})

test('keeps Sync disabled until metadata is ready, then persists the current anchors', () => {
  const storage = memoryStorage()
  renderPanel(storage)
  const panel = localVideoPanel()

  expect((panel.getByRole('button', { name: 'Sync local video' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(panel.getByLabelText('Local video file'), { target: { files: [videoFile()] } })
  expect((panel.getByRole('button', { name: 'Sync local video' }) as HTMLButtonElement).disabled).toBe(true)

  act(() => harness.setSnapshot({ video: readyVideoSnapshot }))
  const sync = panel.getByRole('button', { name: 'Sync local video' })
  expect((sync as HTMLButtonElement).disabled).toBe(false)
  fireEvent.click(sync)

  expect(harness.coordinator.alignCurrent).toHaveBeenCalledOnce()
  expect(storage.value()).toContain('race.mp4')
  expect(storage.value()).toContain('"replayTimeMs":1500')
  expect(storage.value()).toContain('"videoTimeMs":500')
  expect(storage.value()).not.toContain('local video')
  expect(storage.value()).not.toContain('blob:')
  expect(panel.getByRole('button', { name: 'Unsync local video' })).toBeTruthy()
  expect(panel.getByRole('group', { name: 'Fine sync adjustment' })).toBeTruthy()
})

test('Unsync resets only synchronization state and does not issue replay transport commands', () => {
  const controller = renderPanel()
  const panel = localVideoPanel()
  fireEvent.change(panel.getByLabelText('Local video file'), { target: { files: [videoFile()] } })
  act(() => harness.setSnapshot({ video: readyVideoSnapshot }))
  fireEvent.click(panel.getByRole('button', { name: 'Sync local video' }))

  vi.mocked(controller.start).mockClear()
  vi.mocked(controller.pause).mockClear()
  vi.mocked(controller.seek).mockClear()
  vi.mocked(controller.setSpeed).mockClear()
  harness.coordinator.reset.mockClear()
  fireEvent.click(panel.getByRole('button', { name: 'Unsync local video' }))

  expect(harness.coordinator.reset).toHaveBeenCalledOnce()
  expect(vi.mocked(controller.start)).not.toHaveBeenCalled()
  expect(vi.mocked(controller.pause)).not.toHaveBeenCalled()
  expect(vi.mocked(controller.seek)).not.toHaveBeenCalled()
  expect(vi.mocked(controller.setSpeed)).not.toHaveBeenCalled()
  expect(panel.getByRole('button', { name: 'Sync local video' })).toBeTruthy()
  expect(panel.queryByRole('group', { name: 'Fine sync adjustment' })).toBeNull()
})

test('delegates native seeked events to the coordinator', () => {
  renderPanel()
  const panel = localVideoPanel()

  fireEvent.seeked(panel.getByLabelText('Selected local replay video'))

  expect(harness.coordinator.commitVideoSeek).toHaveBeenCalledOnce()
})

test('offers matching saved alignment only after the file is reselected and restores both anchors', () => {
  const file = videoFile()
  const storage = memoryStorage(JSON.stringify({
    version: 1,
    replayIdentity: 'test-replay',
    fileMetadata: { name: file.name, size: file.size, lastModified: file.lastModified, type: file.type },
    alignment: { replayTimeMs: 1_500, videoTimeMs: 500 },
  }))
  renderPanel(storage)
  const panel = localVideoPanel()

  expect(panel.getByText('Video needs to be reselected')).toBeTruthy()
  expect(panel.queryByRole('button', { name: 'Restore saved alignment' })).toBeNull()
  fireEvent.change(panel.getByLabelText('Local video file'), { target: { files: [file] } })
  act(() => harness.setSnapshot({ video: readyVideoSnapshot }))

  fireEvent.click(panel.getByRole('button', { name: 'Restore saved alignment' }))
  expect(harness.adapter.seek).toHaveBeenCalledWith(500)
  expect(harness.coordinator.seek).toHaveBeenCalledWith(1_500)
  expect(panel.queryByRole('button', { name: 'Restore saved alignment' })).toBeNull()
})

test.each([
  ['unsupported media', { type: 'unsupported-media', message: 'This format is not supported.' }, 'Unsupported media', 'alert'],
  ['autoplay rejection', { type: 'play-rejected', message: 'Playback was rejected.' }, 'Autoplay rejected', 'alert'],
  ['missing metadata', { type: 'missing-metadata', message: 'Video duration is unavailable.' }, 'Metadata pending', 'status'],
  ['generic media error', { type: 'operation-error', message: 'Video operation failed.' }, 'Video error', 'alert'],
] as const)('announces the %s media state', (_name, error, label, role) => {
  renderPanel()
  const panel = localVideoPanel()
  fireEvent.change(panel.getByLabelText('Local video file'), { target: { files: [videoFile()] } })
  act(() => harness.setSnapshot({ error: mediaError(error.type, error.message) }))

  const status = panel.getByRole(role)
  expect(status.textContent).toContain(label)
  expect(status.textContent).toContain(error.type === 'play-rejected' ? 'Press play again' : error.message)
})

test('announces pending, ended, out-of-range, unsynced, and synced states through one live region', () => {
  renderPanel()
  const panel = localVideoPanel()
  fireEvent.change(panel.getByLabelText('Local video file'), { target: { files: [videoFile()] } })

  expect(panel.getByRole('status').textContent).toContain('Metadata pending')
  act(() => harness.setSnapshot({ video: readyVideoSnapshot }))
  expect(panel.getByRole('status').textContent).toContain('Unsynced')
  act(() => harness.setSnapshot({ video: { ...readyVideoSnapshot, isEnded: true } }))
  expect(panel.getByRole('status').textContent).toContain('Video ended')
  act(() => harness.setSnapshot({ video: readyVideoSnapshot, status: 'out-of-range', syncStatus: 'out-of-range' }))
  expect(panel.getByRole('status').textContent).toContain('Out of range')
  act(() => harness.setSnapshot({ video: readyVideoSnapshot, status: 'synced', syncStatus: 'synced', isLinked: true, model: syncedModel, videoMapping: { status: 'mapped', timeMs: 1_500 } }))
  expect(panel.getByRole('status').textContent).toContain('Synced')
})

test('shows native video out-of-range status until a valid seek recovers the mapping', () => {
  renderPanel()
  const panel = localVideoPanel()
  fireEvent.change(panel.getByLabelText('Local video file'), { target: { files: [videoFile()] } })
  act(() => harness.setSnapshot({
    video: readyVideoSnapshot,
    status: 'synced',
    syncStatus: 'synced',
    isLinked: true,
    model: syncedModel,
    videoMapping: { status: 'out-of-range', requestedTimeMs: 3_500, mappedTimeMs: null, bounds: { startMs: 0, endMs: 3_000 }, reason: 'source-out-of-range' },
  }))

  fireEvent.seeked(panel.getByLabelText('Selected local replay video'))
  expect(harness.coordinator.commitVideoSeek).toHaveBeenCalledOnce()
  expect(panel.getByRole('status').textContent).toContain('Out of range')

  act(() => harness.setSnapshot({ videoMapping: { status: 'mapped', timeMs: 1_500 } }))
  expect(panel.getByRole('status').textContent).toContain('Synced')
})

test("does not announce another replay's saved alignment", () => {
  const file = videoFile()
  const storage = memoryStorage(JSON.stringify({
    version: 1,
    replayIdentity: 'another-replay',
    fileMetadata: { name: file.name, size: file.size, lastModified: file.lastModified, type: file.type },
    alignment: { replayTimeMs: 1_500, videoTimeMs: 500 },
  }))
  renderPanel(storage)
  const panel = localVideoPanel()

  expect(panel.queryByText('Video needs to be reselected')).toBeNull()
  fireEvent.change(panel.getByLabelText('Local video file'), { target: { files: [file] } })
  act(() => harness.setSnapshot({ video: readyVideoSnapshot }))
  expect(panel.queryByRole('button', { name: 'Restore saved alignment' })).toBeNull()
  fireEvent.click(panel.getByRole('button', { name: 'Clear and reselect' }))
  expect(panel.queryByText('Video needs to be reselected')).toBeNull()
})

test('provides scoped accessible names, pressed state, focus rules, and touch targets', () => {
  renderPanel()
  const panel = localVideoPanel()
  const video = panel.getByLabelText('Selected local replay video')
  fireEvent.change(panel.getByLabelText('Local video file'), { target: { files: [videoFile()] } })
  act(() => harness.setSnapshot({ video: readyVideoSnapshot }))
  const sync = panel.getByRole('button', { name: 'Sync local video' })

  expect(screen.getByRole('region', { name: 'Local video replay' })).toBeTruthy()
  expect(video.getAttribute('aria-describedby')).toBe('local-video-status')
  expect(panel.getByRole('status')).toBeTruthy()
  expect(panel.getByLabelText('Local video controls')).toBeTruthy()
  expect(sync.getAttribute('aria-pressed')).toBe('false')
  sync.focus()
  expect(document.activeElement).toBe(sync)
  expect(localVideoStyles).toContain('min-height: 44px')
  expect(localVideoStyles).toContain('min-width: 44px')
  expect(localVideoStyles).toContain(':focus-visible')
})

test.each([
  [375, '.live-leaderboard__table th, .live-leaderboard__table td { padding-inline: .5rem; }'],
  [768, '.local-video-panel__actions { grid-template-columns: repeat(3, minmax(0, 1fr)); }'],
  [1024, '.local-video-panel__actions { grid-template-columns: minmax(0, 1.25fr) repeat(2, minmax(0, 1fr)); }'],
  [1440, '.replay-panel { padding: 1.75rem; }'],
] as const)('keeps local-video responsive rules available at %dpx', (width, rule) => {
  expect(responsiveStyles).toContain(`@media (min-width: ${width}px)`)
  expect(responsiveStyles).toContain(rule)
  expect(localVideoStyles).toContain('.local-video-panel__actions { grid-template-columns: minmax(0, 1fr); }')
})

function renderPanel(storage?: { getItem: (key: string) => string | null; setItem: (key: string, value: string) => void }): ReplayController {
  const controller = createController()
  render(<LocalVideoPanel controller={controller} endMs={3_000} replayIdentity="test-replay" startMs={0} storage={storage} />)
  return controller
}

function localVideoPanel() {
  return within(screen.getByRole('region', { name: 'Local video replay' }))
}

function createController(): ReplayController {
  return {
    getSnapshot: () => replaySnapshot,
    subscribe: vi.fn(() => () => undefined),
    start: vi.fn(), pause: vi.fn(), seek: vi.fn(), setSpeed: vi.fn(), retry: vi.fn(async () => undefined), dispose: vi.fn(),
  }
}

function videoFile(): File {
  return new File(['local video'], 'race.mp4', { type: 'video/mp4', lastModified: 42 })
}

function memoryStorage(initial: string | null = null) {
  let stored = initial
  return {
    getItem: vi.fn(() => stored),
    setItem: vi.fn((_key: string, value: string) => { stored = value }),
    value: () => stored ?? '',
  }
}

function mediaError(type: string, message: string): LocalVideoMediaError {
  return { type: type as LocalVideoMediaError['type'], kind: type as LocalVideoMediaError['kind'], message, code: null }
}

const readyVideoSnapshot: LocalVideoAdapterSnapshot = {
  currentTimeMs: 500, durationMs: 3_000, metadataReady: true, isPlaying: false, isEnded: false, playbackRate: 1, sourceUrl: 'blob:local-video', error: null,
}
