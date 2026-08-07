import { describe, expect, test } from 'vitest'
import type { LapSectorSidecar, QualifyingLapStatusSidecar } from '../../../../src/data/replay/types'
import { selectSectorColour, selectSectorColors, selectSectorColours } from '../../../../src/features/replay/selectors/sector-colour-selectors'

/**
 * A qualifying sidecar whose outlap carries faster sector durations (20ms)
 * than either flying lap. Only the explicit flying laps may set session and
 * personal bests or appear as coloured sectors.
 */
function qualifyingFlyingSidecar(): LapSectorSidecar {
  return {
    contractVersion: 'v2', fixtureId: 'sector-colour-fixture', phaseBoundaries: [{ phase: 'Q1', startMs: 0 }], drivers: {
      HAM: {
        lapNumber: [1, 2, 3], lapStartMs: [0, 100, 200], lapEndMs: [90, 190, 290], lapDurationMs: [90, 86, 60],
        sector1DurationMs: [30, 28, 20], sector2DurationMs: [30, 30, 20], sector3DurationMs: [30, 30, 20],
        sector1SessionTimeMs: [30, 130, 230], sector2SessionTimeMs: [60, 160, 260], sector3SessionTimeMs: [90, 190, 290],
        qualifyingPhase: ['Q1', 'Q1', 'Q1'],
        lapKind: ['flying', 'flying', 'outlap'],
      },
      VER: {
        lapNumber: [1], lapStartMs: [0], lapEndMs: [100], lapDurationMs: [95],
        sector1DurationMs: [32], sector2DurationMs: [31], sector3DurationMs: [29],
        sector1SessionTimeMs: [40], sector2SessionTimeMs: [70], sector3SessionTimeMs: [100],
        qualifyingPhase: ['Q1'],
        lapKind: ['flying'],
      },
    },
  }
}

describe('sector-colour selectors — flying-only bests', () => {
  test('colours only explicit flying lap sectors and ignores faster outlap rows', () => {
    // Arrange: the outlap lap 3 posts 20ms sectors that are faster than every
    // flying sector, so any leak into bests would be visible immediately.
    const sidecar = qualifyingFlyingSidecar()

    // Act
    const selection = selectSectorColours(sidecar, 300, 'HAM')

    // Assert
    expect(selection.driverId).toBe('HAM')
    expect(selection.sessionTimeMs).toBe(300)
    expect(selection.sectors.map(({ lapNumber }) => lapNumber)).toEqual([1, 1, 1, 2, 2, 2])
    // Session bests come only from flying laps: S1=28 (lap 2), S2=30, S3=29 (VER).
    expect(selection.sectors[0]).toMatchObject({
      lapNumber: 1, sectorNumber: 1, durationMs: 30, sessionTimeMs: 30,
      sessionBestMs: 28, personalBestMs: 28, colour: 'slower',
      isSessionBest: false, isPersonalBest: false,
    })
    expect(selection.sectors[1]).toMatchObject({ lapNumber: 1, sectorNumber: 2, durationMs: 30, sessionBestMs: 30, colour: 'session-best', isSessionBest: true })
    // 30ms is the HAM personal best for S3 while the session best is VER's 29ms.
    expect(selection.sectors[2]).toMatchObject({ lapNumber: 1, sectorNumber: 3, sessionBestMs: 29, personalBestMs: 30, colour: 'personal-best', isPersonalBest: true })
    expect(selection.sectors[3]).toMatchObject({ lapNumber: 2, sectorNumber: 1, durationMs: 28, sessionBestMs: 28, colour: 'session-best', isSessionBest: true })
    expect(selection.sectors[4]).toMatchObject({ lapNumber: 2, sectorNumber: 2, sessionBestMs: 30, colour: 'session-best' })
    expect(selection.sectors[5]).toMatchObject({ lapNumber: 2, sectorNumber: 3, sessionBestMs: 29, personalBestMs: 30, colour: 'personal-best' })
  })

  test('excludes sector completions after the replay cursor from bests and visible sectors', () => {
    // Arrange: lap 2 completes sectors at 130/160/190 while the cursor sits at
    // 150 — only sector 1 is causally known. The faster 28ms S2/S3 values must
    // not shape bests before their completions.
    const sidecar: LapSectorSidecar = {
      contractVersion: 'v2', fixtureId: 'sector-colour-fixture', phaseBoundaries: [{ phase: 'Q1', startMs: 0 }], drivers: {
        HAM: {
          lapNumber: [1, 2], lapStartMs: [0, 100], lapEndMs: [90, 190], lapDurationMs: [90, 90],
          sector1DurationMs: [30, 28], sector2DurationMs: [30, 28], sector3DurationMs: [30, 28],
          sector1SessionTimeMs: [30, 130], sector2SessionTimeMs: [60, 160], sector3SessionTimeMs: [90, 190],
          qualifyingPhase: ['Q1', 'Q1'],
          lapKind: ['flying', 'flying'],
        },
      },
    }

    // Act
    const early = selectSectorColours(sidecar, 150, 'HAM')
    const complete = selectSectorColours(sidecar, 200, 'HAM')

    // Assert
    expect(early.sectors.map(({ lapNumber, sectorNumber }) => [lapNumber, sectorNumber])).toEqual([[1, 1], [1, 2], [1, 3], [2, 1]])
    expect(early.sectors.find((s) => s.lapNumber === 1 && s.sectorNumber === 2)?.sessionBestMs).toBe(30)
    expect(early.sectors.find((s) => s.lapNumber === 2 && s.sectorNumber === 1)?.colour).toBe('session-best')
    // After the full flying lap is causal the faster S2 value becomes the best.
    expect(complete.sectors.find((s) => s.lapNumber === 1 && s.sectorNumber === 2)?.sessionBestMs).toBe(28)
  })

  test('excludes sectors deleted by the qualifying status sidecar from visibility and bests', () => {
    // Arrange: lap 2 is deleted at 150, after its sector 1 completed at 130.
    const sidecar: LapSectorSidecar = {
      contractVersion: 'v2', fixtureId: 'sector-colour-fixture', phaseBoundaries: [{ phase: 'Q1', startMs: 0 }], drivers: {
        HAM: {
          lapNumber: [1, 2], lapStartMs: [0, 100], lapEndMs: [90, 190], lapDurationMs: [90, 90],
          sector1DurationMs: [30, 28], sector2DurationMs: [30, 30], sector3DurationMs: [30, 30],
          sector1SessionTimeMs: [30, 130], sector2SessionTimeMs: [60, 160], sector3SessionTimeMs: [90, 190],
          qualifyingPhase: ['Q1', 'Q1'],
          lapKind: ['flying', 'flying'],
        },
      },
    }
    const statusSidecar: QualifyingLapStatusSidecar = {
      contractVersion: 'v2', fixtureId: 'sector-colour-fixture',
      drivers: { HAM: { lapNumber: [1, 2], lapStartMs: [0, 100], lapEndMs: [90, 190], status: ['valid', 'deleted'], deletedReason: [null, 'TRACK LIMITS'] } },
      events: [{ driverId: 'HAM', lapNumber: 2, eventTimeMs: 150, status: 'deleted', reason: 'TRACK LIMITS', rawMessage: 'LAP 2 DELETED' }],
    }

    // Act
    const beforeDeletion = selectSectorColours(sidecar, 140, 'HAM', statusSidecar)
    const deleted = selectSectorColours(sidecar, 200, 'HAM', statusSidecar)

    // Assert
    expect(beforeDeletion.sectors.map(({ lapNumber }) => lapNumber)).toEqual([1, 1, 1, 2])
    expect(deleted.sectors.map(({ lapNumber }) => lapNumber)).toEqual([1, 1, 1])
    // With lap 2 deleted, the 28ms S1 no longer shapes the session best.
    expect(deleted.sectors[0]).toMatchObject({ lapNumber: 1, sectorNumber: 1, sessionBestMs: 30, colour: 'session-best' })
  })

  test('fails closed when a qualifying-like sidecar has no lapKind column', () => {
    // Arrange: an old v2 sidecar with phase structure but no lapKind column
    // exposes no flying evidence — absence never means every sector is flying.
    const legacyQualifying: LapSectorSidecar = {
      contractVersion: 'v2', fixtureId: 'sector-colour-fixture', phaseBoundaries: [{ phase: 'Q1', startMs: 0 }], drivers: {
        HAM: {
          lapNumber: [1], lapStartMs: [0], lapEndMs: [90], lapDurationMs: [90],
          sector1DurationMs: [30], sector2DurationMs: [30], sector3DurationMs: [30],
          sector1SessionTimeMs: [30], sector2SessionTimeMs: [60], sector3SessionTimeMs: [90],
          qualifyingPhase: ['Q1'],
        },
      },
    }

    // Act
    const selection = selectSectorColours(legacyQualifying, 200, 'HAM')

    // Assert
    expect(selection.sectors).toEqual([])
    expect(selection.sessionTimeMs).toBe(200)
  })

  test('preserves race and sprint sidecar behaviour when no flying capability exists', () => {
    // Arrange: a race-like sidecar (no phase boundaries, no lapKind) keeps the
    // legacy behaviour of colouring every causally completed sector.
    const raceLike: LapSectorSidecar = {
      contractVersion: 'v2', fixtureId: 'sector-colour-fixture', phaseBoundaries: [], drivers: {
        VER: {
          lapNumber: [1, 2], lapStartMs: [0, 100], lapEndMs: [90, 190], lapDurationMs: [90, 89],
          sector1DurationMs: [30, 29], sector2DurationMs: [32, 31], sector3DurationMs: [31, 30],
          sector1SessionTimeMs: [30, 130], sector2SessionTimeMs: [60, 160], sector3SessionTimeMs: [90, 190],
          qualifyingPhase: [null, null],
        },
      },
    }

    // Act
    const selection = selectSectorColours(raceLike, 200, 'VER')

    // Assert
    expect(selection.sectors.map(({ lapNumber }) => lapNumber)).toEqual([1, 1, 1, 2, 2, 2])
    expect(selection.sectors[0]).toMatchObject({ lapNumber: 1, sectorNumber: 1, colour: 'slower' })
    expect(selection.sectors[3]).toMatchObject({ lapNumber: 2, sectorNumber: 1, durationMs: 29, sessionBestMs: 29, colour: 'session-best' })
    expect(selection.sectors[4]).toMatchObject({ lapNumber: 2, sectorNumber: 2, durationMs: 31, sessionBestMs: 31, colour: 'session-best' })
    expect(selection.sectors[5]).toMatchObject({ lapNumber: 2, sectorNumber: 3, durationMs: 30, sessionBestMs: 30, colour: 'session-best' })
  })

  test('marks a sector unavailable when its duration is not published', () => {
    // Arrange: sector 1 completes causally but has no published duration.
    const sidecar: LapSectorSidecar = {
      contractVersion: 'v2', fixtureId: 'sector-colour-fixture', phaseBoundaries: [{ phase: 'Q1', startMs: 0 }], drivers: {
        HAM: {
          lapNumber: [1], lapStartMs: [0], lapEndMs: [90], lapDurationMs: [90],
          sector1DurationMs: [null], sector2DurationMs: [30], sector3DurationMs: [30],
          sector1SessionTimeMs: [30], sector2SessionTimeMs: [60], sector3SessionTimeMs: [90],
          qualifyingPhase: ['Q1'],
          lapKind: ['flying'],
        },
      },
    }

    // Act
    const selection = selectSectorColours(sidecar, 100, 'HAM')

    // Assert
    expect(selection.sectors[0]).toMatchObject({ sectorNumber: 1, durationMs: null, sessionBestMs: null, colour: 'unavailable', isSessionBest: false })
    expect(selection.sectors[1]).toMatchObject({ sectorNumber: 2, durationMs: 30, colour: 'session-best' })
    expect(selection.sectors[2]).toMatchObject({ sectorNumber: 3, durationMs: 30, colour: 'session-best' })
  })

  test('returns an empty selection for an absent sidecar or unknown driver', () => {
    // Arrange
    const sidecar = qualifyingFlyingSidecar()

    // Act
    const absent = selectSectorColours(undefined, 300, 'HAM')
    const unknownDriver = selectSectorColours(sidecar, 300, 'MISSING')

    // Assert
    expect(absent.sectors).toEqual([])
    expect(absent.sessionTimeMs).toBe(300)
    expect(unknownDriver.sectors).toEqual([])
  })

  test('freezes the selection and its sector records', () => {
    // Arrange
    const sidecar = qualifyingFlyingSidecar()

    // Act
    const selection = selectSectorColours(sidecar, 300, 'HAM')

    // Assert
    expect(Object.isFrozen(selection)).toBe(true)
    expect(Object.isFrozen(selection.sectors)).toBe(true)
    expect(Object.isFrozen(selection.sectors[0])).toBe(true)
  })

  test('exposes the selector under concise aliases', () => {
    expect(selectSectorColour).toBe(selectSectorColours)
    expect(selectSectorColors).toBe(selectSectorColours)
  })
})
