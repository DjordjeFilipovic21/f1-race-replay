import { describe, expect, test } from 'vitest'
import { parseQualifyingLapStatus } from '../../../../src/data/replay/guards'
import {
  filterQualifyingBestLapCandidates,
  filterQualifyingLapCandidates,
  filterQualifyingSectorCandidates,
  selectQualifyingLapStatus,
  selectQualifyingLapStatuses,
} from '../../../../src/features/replay/selectors/qualifying-lap-status-selectors'

const sidecar = parseQualifyingLapStatus({
  contractVersion: 'v2', fixtureId: 'selector-fixture',
  drivers: {
    HAM: { lapNumber: [1, 2, 3], lapStartMs: [0, 100, 200], lapEndMs: [90, 190, 290], status: ['valid', 'valid', 'deleted'], deletedReason: [null, null, null] },
  },
  events: [
    { driverId: 'HAM', lapNumber: 2, eventTimeMs: 100, status: 'deleted', reason: 'TRACK LIMITS', rawMessage: 'deleted' },
    { driverId: 'HAM', lapNumber: 2, eventTimeMs: 200, status: 'reinstated', reason: null, rawMessage: 'reinstated' },
    { driverId: 'HAM', lapNumber: 3, eventTimeMs: 300, status: 'deleted', reason: null, rawMessage: 'deleted' },
  ],
})

describe('qualifying lap-status causal selectors', () => {
  test('starts valid, deletes, reinstates, and seeks in either direction', () => {
    expect(selectQualifyingLapStatus(sidecar, 99, 'HAM', 2)).toBe('valid')
    expect(selectQualifyingLapStatus(sidecar, 100, 'HAM', 2)).toBe('deleted')
    expect(selectQualifyingLapStatus(sidecar, 200, 'HAM', 2)).toBe('valid')
    expect(selectQualifyingLapStatus(sidecar, 301, 'HAM', 3)).toBe('deleted')
    expect(selectQualifyingLapStatus(sidecar, 0, 'HAM', 3)).toBe('valid')
  })

  test('uses deterministic ordering for duplicate timestamps and preserves immutable output', () => {
    const selected = selectQualifyingLapStatuses(sidecar, 150, 'HAM')
    expect(selected.laps.map(({ lapNumber, status }) => [lapNumber, status])).toEqual([[1, 'valid'], [2, 'deleted'], [3, 'valid']])
    expect(Object.isFrozen(selected)).toBe(true)
    expect(Object.isFrozen(selected.laps)).toBe(true)
  })

  test('returns no evidence for absent sidecars, drivers, and laps', () => {
    expect(selectQualifyingLapStatuses(undefined, 100, 'VER').laps).toEqual([])
    expect(selectQualifyingLapStatus(sidecar, 100, 'VER', 1)).toBeNull()
    expect(selectQualifyingLapStatus(sidecar, 100, 'HAM', 99)).toBeNull()
  })

  test('leaves candidates unchanged when the optional sidecar is absent', () => {
    const candidates = [{ lapNumber: 1 }, { lapNumber: null }]
    const filtered = filterQualifyingLapCandidates(candidates, null, 150, 'HAM')

    expect(filtered).toEqual(candidates)
    expect(filtered).not.toBe(candidates)
    expect(Object.isFrozen(filtered)).toBe(true)
  })

  test('filters lap, sector, and best-lap candidates without treating unknown evidence as valid', () => {
    const candidates = [{ lapNumber: 1 }, { lapNumber: 2 }, { lapNumber: 3 }, { lapNumber: null }]
    expect(filterQualifyingLapCandidates(candidates, sidecar, 150, 'HAM')).toEqual([{ lapNumber: 1 }, { lapNumber: 3 }])
    expect(filterQualifyingSectorCandidates(candidates, sidecar, 150, 'HAM')).toEqual([{ lapNumber: 1 }, { lapNumber: 3 }])
    expect(filterQualifyingBestLapCandidates(candidates, undefined, 150, 'HAM')).toEqual(candidates)
    expect(filterQualifyingLapCandidates([{ lapNumber: 99 }], sidecar, 150, 'HAM')).toEqual([])
    expect(selectQualifyingLapStatus(sidecar, 150, 'HAM', 2)).toBe('deleted')
    expect(selectQualifyingLapStatus(sidecar, 250, 'HAM', 2)).toBe('valid')
  })
})
