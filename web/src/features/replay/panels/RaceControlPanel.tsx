import { memo } from 'react'
import fiaLogo from '../../../assets/fia/fia.png'
import type { ReplayEvent } from '../../../data/replay/types'
import type { ReplayControllerSnapshot } from '../../../engine/replay'
import { formatTrackStatus } from './track-status'

export { formatTrackStatus } from './track-status'

export const RACE_CONTROL_MESSAGE_DURATION_MS = 5_000
export const RACE_CONTROL_MESSAGE_EXIT_DURATION_MS = 240

export interface RaceControlPanelProps {
  readonly snapshot: ReplayControllerSnapshot
  readonly activeMessage: ReplayEvent | null
  readonly isMessageExiting: boolean
}

/** Presents sampled race state and the current transient race-control message. */
export const RaceControlPanel = memo(function RaceControlPanel({ snapshot, activeMessage, isMessageExiting }: RaceControlPanelProps) {
  const replay = snapshot.replay
  const message = activeMessage === null ? null : formatRaceControlMessage(activeMessage)
  return (
    <article className="race-control-panel" aria-labelledby="race-control-title">
      <header className="race-control-panel__header">
        <p className="race-control-panel__eyebrow">Live race state</p>
        <h2 id="race-control-title">Race control</h2>
      </header>
      <dl className="race-control-panel__status" aria-label="Active race state">
        <div className="race-control-panel__status-item">
          <dt>Track status</dt>
          <dd>{formatTrackStatus(replay?.trackStatusCode ?? null)}</dd>
        </div>
        <div className="race-control-panel__status-item">
          <dt>Weather</dt>
          <dd>{formatWeatherState(replay?.weatherState ?? null)}</dd>
        </div>
      </dl>
      {message !== null && (
        <section className="race-control-panel__message-region" aria-label="Race control message">
          <article className={`race-control-panel__message${message.isPenalty ? ' race-control-panel__message--penalty' : ''}${isMessageExiting ? ' race-control-panel__message--exiting' : ''}`} aria-live="polite" data-state={isMessageExiting ? 'exiting' : 'active'}>
            <img className="race-control-panel__fia-logo" src={fiaLogo} alt="" aria-hidden="true" />
            <div className="race-control-panel__message-content">
              <p className="race-control-panel__message-headline">{message.headline}</p>
              <p className="race-control-panel__message-copy">{message.detail}</p>
            </div>
          </article>
        </section>
      )}
    </article>
  )
})

export function formatWeatherState(weatherState: string | null): string {
  const normalized = weatherState?.trim() ?? ''
  return normalized ? normalized.charAt(0).toUpperCase() + normalized.slice(1).toLowerCase() : 'Unavailable'
}

export interface FormattedRaceControlMessage {
  readonly headline: string
  readonly detail: string
  readonly isPenalty: boolean
}

export function formatRaceControlMessage(event: ReplayEvent): FormattedRaceControlMessage {
  const description = uppercaseRaceControlText(event.description)
  const segments = description.split(' - ').filter(Boolean)
  const hasIncidentHeadline = segments.length > 1 && /\b(INCIDENT|PENALTY|INVESTIGATION|FLAG)\b/.test(segments[0])
  const scope = uppercaseRaceControlText(stringPayload(event, 'scope'))
  const eventType = uppercaseRaceControlText(event.eventType).replace(/[_-]+/g, ' ')
  const subject = uppercaseRaceControlText(event.driverId ?? '')
  const headline = hasIncidentHeadline
    ? `RACE CONTROL: ${segments[0]}`
    : `RACE CONTROL: ${[subject, eventType || 'MESSAGE'].filter(Boolean).join(' ')}`
  const detail = hasIncidentHeadline
    ? segments.slice(1).join(' - ')
    : [scope, description].filter(Boolean).join(' - ') || 'UNAVAILABLE'
  return { headline, detail, isPenalty: /\b(PENALTY|PENALISED|PENALIZED|DISQUALIFIED)\b/.test(`${headline} ${detail}`) }
}

function stringPayload(event: ReplayEvent, key: string): string {
  const value = event.payload?.[key]
  return typeof value === 'string' ? value : ''
}

function uppercaseRaceControlText(value: string): string {
  return value.trim().replace(/\s+/g, ' ').replace(/\s*-\s*/g, ' - ').toUpperCase()
}
