export type TrackStatusTone = 'neutral' | 'yellow' | 'red'

export interface TrackStatusSemantics {
  readonly code: number | null
  readonly label: string
  readonly tone: TrackStatusTone
  readonly isSafetyCar: boolean
  readonly isVirtualSafetyCar: boolean
}

const TRACK_STATUS_LABELS: Readonly<Record<number, string>> = {
  1: 'All Clear',
  2: 'Yellow Flag',
  4: 'Safety Car',
  5: 'Red Flag',
  6: 'Virtual Safety Car',
  7: 'Virtual Safety Car Ending',
}

/** Maps the browser track-status codes to the shared presentation semantics. */
export function describeTrackStatus(trackStatusCode: number | null): TrackStatusSemantics {
  if (trackStatusCode === null || !Number.isFinite(trackStatusCode)) {
    return { code: null, label: 'Unavailable', tone: 'neutral', isSafetyCar: false, isVirtualSafetyCar: false }
  }

  const isSafetyCar = trackStatusCode === 4
  const isVirtualSafetyCar = trackStatusCode === 6 || trackStatusCode === 7
  const tone: TrackStatusTone = trackStatusCode === 5 ? 'red' : isSafetyCar || isVirtualSafetyCar || trackStatusCode === 2 ? 'yellow' : 'neutral'
  return {
    code: trackStatusCode,
    label: TRACK_STATUS_LABELS[trackStatusCode] ?? `Unknown (Code ${trackStatusCode})`,
    tone,
    isSafetyCar,
    isVirtualSafetyCar,
  }
}

export function formatTrackStatus(trackStatusCode: number | null): string {
  return describeTrackStatus(trackStatusCode).label
}
