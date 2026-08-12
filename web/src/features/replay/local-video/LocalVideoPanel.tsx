import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import type { ReplayController } from '../../../engine/replay'
import type { LocalVideoAdapter } from './local-video-adapter'
import { createLocalVideoAdapter } from './local-video-adapter'
import {
  createLocalVideoFileMetadata,
  hasPersistedLocalVideoAlignment,
  loadLocalVideoAlignment,
  saveLocalVideoAlignment,
  type LocalVideoAlignment,
  type LocalVideoPersistenceStorage,
  type LocalVideoReplayIdentity,
} from './local-video-persistence'
import {
  createLocalVideoSyncCoordinator,
  type LocalVideoSyncCoordinator,
  type LocalVideoSyncCoordinatorSnapshot,
} from './local-video-sync-coordinator'
import type { LocalVideoSyncModel } from './local-video-sync-model'

const FINE_ADJUSTMENTS_MS = [-500, -100, 100, 500] as const

export interface LocalVideoPanelProps {
  readonly controller: ReplayController
  readonly startMs: number
  readonly endMs: number
  readonly replayIdentity: LocalVideoReplayIdentity
  readonly storage?: LocalVideoPersistenceStorage | null
}

/** Browser-only video surface; the coordinator remains the source of sync truth. */
export function LocalVideoPanel({ controller, startMs, endMs, replayIdentity, storage }: LocalVideoPanelProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const adapterRef = useRef<LocalVideoAdapter | null>(null)
  const [coordinator, setCoordinator] = useState<LocalVideoSyncCoordinator | null>(null)
  const [snapshot, setSnapshot] = useState<LocalVideoSyncCoordinatorSnapshot | null>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [savedAlignment, setSavedAlignment] = useState<LocalVideoAlignment | null>(null)
  const [hasStoredAlignment, setHasStoredAlignment] = useState(() => hasPersistedLocalVideoAlignment(replayIdentity, storage))
  const fileMetadata = selectedFile === null ? null : createLocalVideoFileMetadata(selectedFile)
  const status = getPanelStatus(snapshot, selectedFile, hasStoredAlignment)
  const isReady = snapshot?.video.metadataReady === true
  const isSynced = snapshot?.model.status === 'synced'
  const syncButtonDisabled = !isReady && !isSynced

  useEffect(() => {
    const video = videoRef.current
    if (video === null) return
    const adapter = createLocalVideoAdapter(video)
    const nextCoordinator = createLocalVideoSyncCoordinator({ controller, adapter, replayBounds: { startMs, endMs } })
    const publish = () => setSnapshot(nextCoordinator.getSnapshot())
    adapterRef.current = adapter
    setCoordinator(nextCoordinator)
    const unsubscribe = nextCoordinator.subscribe(publish)
    publish()
    return () => {
      unsubscribe()
      nextCoordinator.dispose()
      adapter.dispose()
      adapterRef.current = null
      setCoordinator((current) => current === nextCoordinator ? null : current)
      setSnapshot(null)
    }
  }, [controller, endMs, startMs])

  useEffect(() => {
    const video = videoRef.current
    if (video === null || coordinator === null) return
    const handleNativeSeeked = (): void => { coordinator.commitVideoSeek() }
    video.addEventListener('seeked', handleNativeSeeked)
    return () => video.removeEventListener('seeked', handleNativeSeeked)
  }, [coordinator])

  useEffect(() => {
    const adapter = adapterRef.current
    if (adapter === null) return
    adapter.setFile(selectedFile)
    coordinator?.reset()
  }, [coordinator, selectedFile])

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0] ?? null
    setSavedAlignment(file === null ? null : loadLocalVideoAlignment(replayIdentity, createLocalVideoFileMetadata(file), storage))
    setSelectedFile(file)
    setHasStoredAlignment(file === null ? hasPersistedLocalVideoAlignment(replayIdentity, storage) : false)
  }

  const persistModel = (model: LocalVideoSyncModel): void => {
    if (fileMetadata === null || model.status === 'unsynced') return
    saveLocalVideoAlignment(replayIdentity, fileMetadata, {
      replayTimeMs: model.anchor.replayTimeMs,
      videoTimeMs: model.anchor.videoTimeMs,
    }, storage)
  }

  const toggleSync = () => {
    if (coordinator === null) return
    if (isSynced) {
      coordinator.reset()
      return
    }
    persistModel(coordinator.alignCurrent())
  }

  const adjustAlignment = (deltaMs: number) => {
    const model = coordinator?.adjustAlignment(deltaMs)
    if (model !== undefined) persistModel(model)
  }

  const restoreSavedAlignment = () => {
    if (coordinator === null || adapterRef.current === null || savedAlignment === null) return
    adapterRef.current.seek(savedAlignment.videoTimeMs)
    coordinator.seek(savedAlignment.replayTimeMs)
    const model = coordinator.alignCurrent()
    persistModel(model)
    setSavedAlignment(null)
  }

  const clearVideo = () => {
    setSavedAlignment(null)
    setSelectedFile(null)
    setHasStoredAlignment(hasPersistedLocalVideoAlignment(replayIdentity, storage))
    if (fileInputRef.current !== null) fileInputRef.current.value = ''
  }

  return (
    <section className="local-video-panel" role="region" aria-labelledby="local-video-panel-title">
      <header className="local-video-panel__header">
        <h2 id="local-video-panel-title">Local video replay</h2>
      </header>

      <div className="local-video-panel__body">
        <div className="local-video-panel__import">
          {selectedFile === null && <>
            <button type="button" className="local-video-panel__file-button" onClick={() => fileInputRef.current?.click()}>Select local video</button>
            <input ref={fileInputRef} id="local-video-file" className="local-video-panel__file-input" type="file" accept="video/*" aria-label="Local video file" tabIndex={-1} onChange={handleFileChange} />
          </>}
          {selectedFile !== null && <p className="local-video-panel__file-name">Selected: <strong>{selectedFile.name}</strong></p>}
        </div>

        <div className="local-video-panel__stage">
          <video ref={videoRef} className="local-video-panel__video" controls playsInline preload="metadata" aria-label="Selected local replay video" aria-describedby="local-video-status" />
          {selectedFile === null && <p className="local-video-panel__placeholder">Select a video to begin.</p>}
          {selectedFile !== null && !isReady && snapshot?.error === null && <p className="local-video-panel__placeholder">Loading video metadata…</p>}
        </div>

        <div id="local-video-status" className={`local-video-panel__status local-video-panel__status--${status.tone}`} role={status.tone === 'error' ? 'alert' : 'status'} aria-live="polite">
          <strong>{status.label}</strong>
          <span>{status.detail}</span>
        </div>

        <div className="local-video-panel__actions" aria-label="Local video controls">
          <button type="button" className="local-video-panel__primary-action" aria-pressed={isSynced} aria-label={isSynced ? 'Unsync local video' : 'Sync local video'} disabled={syncButtonDisabled} onClick={toggleSync}>{isSynced ? 'Unsync' : 'Sync'}</button>
          {savedAlignment !== null && <button type="button" disabled={!isReady} onClick={restoreSavedAlignment}>Restore saved alignment</button>}
          <button type="button" disabled={selectedFile === null} onClick={clearVideo}>Clear and reselect</button>
        </div>

        {isSynced && <div className="local-video-panel__fine-adjust" role="group" aria-label="Fine sync adjustment">
          <span>Fine adjustment</span>
          <div>
            {FINE_ADJUSTMENTS_MS.map((deltaMs) => <button key={deltaMs} type="button" onClick={() => adjustAlignment(deltaMs)}>{deltaMs > 0 ? '+' : ''}{deltaMs} ms</button>)}
          </div>
        </div>}
      </div>
    </section>
  )
}

interface PanelStatus {
  readonly tone: 'neutral' | 'success' | 'warning' | 'error'
  readonly label: string
  readonly detail: string
}

function getPanelStatus(snapshot: LocalVideoSyncCoordinatorSnapshot | null, file: File | null, hasStoredAlignment: boolean): PanelStatus {
  if (file === null) return hasStoredAlignment
    ? { tone: 'warning', label: 'Video needs to be reselected', detail: 'A saved alignment is available only after you choose the matching file again.' }
    : { tone: 'neutral', label: 'Unsynced', detail: 'Select a local video to create a browser-only replay link.' }
  if (snapshot?.error !== null && snapshot?.error !== undefined) return mediaErrorStatus(snapshot.error.type, snapshot.error.message)
  if (snapshot?.video.isEnded === true) return { tone: 'warning', label: 'Video ended', detail: 'Replay is paused. Seek the video or select it again to continue.' }
  if (snapshot?.video.metadataReady !== true) return { tone: 'neutral', label: 'Metadata pending', detail: 'The browser is reading duration metadata; video bytes remain local.' }
  if (snapshot?.videoMapping.status === 'out-of-range' || snapshot?.status === 'out-of-range') return { tone: 'warning', label: 'Out of range', detail: 'The current replay position has no matching position in this video.' }
  if (snapshot?.status === 'synced' && snapshot.videoMapping.status === 'mapped') return { tone: 'success', label: 'Synced', detail: 'Replay and video play, pause, seeks, and speed stay linked.' }
  return { tone: 'neutral', label: 'Unsynced', detail: 'Sync the current replay and video positions to link playback.' }
}

function mediaErrorStatus(type: string, message: string): PanelStatus {
  if (type === 'unsupported-media') return { tone: 'error', label: 'Unsupported media', detail: message }
  if (type === 'play-rejected') return { tone: 'error', label: 'Autoplay rejected', detail: 'Press play again after interacting with the page.' }
  if (type === 'missing-metadata') return { tone: 'neutral', label: 'Metadata pending', detail: message }
  return { tone: 'error', label: 'Video error', detail: message }
}
