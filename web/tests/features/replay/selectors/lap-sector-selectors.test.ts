import { describe, expect, test } from 'vitest'
import type { LapSectorSidecar, QualifyingLapStatusSidecar } from '../../../../src/data/replay/types'
import { selectLapSectorData } from '../../../../src/features/replay/selectors/lap-sector-selectors'

function qualifyingSidecar(lapKind: readonly ('flying' | 'outlap' | 'inlap' | 'unknown')[]): LapSectorSidecar {
  return {
    contractVersion: 'v2', fixtureId: 'lap-sector-fixture', phaseBoundaries: [{ phase: 'Q1', startMs: 0 }], drivers: {
      HAM: {
        lapNumber: lapKind.map((_, index) => index + 1),
        lapStartMs: lapKind.map((_, index) => index * 100),
        lapEndMs: lapKind.map((_, index) => index * 100 + 90),
        lapDurationMs: lapKind.map((_, index) => 90 - index * 5),
        sector1DurationMs: lapKind.map(() => 30), sector2DurationMs: lapKind.map(() => 30), sector3DurationMs: lapKind.map(() => 30),
        sector1SessionTimeMs: lapKind.map((_, index) => index * 100 + 30),
        sector2SessionTimeMs: lapKind.map((_, index) => index * 100 + 60),
        sector3SessionTimeMs: lapKind.map((_, index) => index * 100 + 90),
        qualifyingPhase: lapKind.map(() => 'Q1' as const),
        lapKind,
      },
    },
  }
}

const raceLikeSidecar: LapSectorSidecar = {
  contractVersion: 'v2', fixtureId: 'lap-sector-fixture', phaseBoundaries: [], drivers: {
    HAM: {
      lapNumber: [1, 2], lapStartMs: [0, 100], lapEndMs: [90, 190], lapDurationMs: [90, 90],
      sector1DurationMs: [30, 30], sector2DurationMs: [30, 30], sector3DurationMs: [30, 30],
      sector1SessionTimeMs: [30, 130], sector2SessionTimeMs: [60, 160], sector3SessionTimeMs: [90, 190],
      qualifyingPhase: [null, null],
    },
  },
}

describe('lap-sector selectors — flying-only visibility', () => {
  test('shows only explicit flying laps and their sectors for qualifying sidecars', () => {
    // Arrange: outlap, inlap, and unknown rows all have causal timing evidence
    // but must contribute no visible lap or sector to qualifying lap analysis.
    const sidecar = qualifyingSidecar(['flying', 'outlap', 'inlap', 'unknown'])

    // Act
    const selection = selectLapSectorData(sidecar, 400, 'HAM')

    // Assert
    expect(selection.laps.map((lap) => lap.lapNumber)).toEqual([1])
    expect(selection.sectors.map((sector) => sector.lapNumber)).toEqual([1, 1, 1])
    expect(selection.laps[0]?.sectors).toHaveLength(3)
  })

  test('keeps a lap whose end is exactly at the cursor visible when flying', () => {
    // Arrange
    const sidecar = qualifyingSidecar(['flying'])

    // Act
    const atEnd = selectLapSectorData(sidecar, 90, 'HAM')
    const beforeEnd = selectLapSectorData(sidecar, 89, 'HAM')

    // Assert
    expect(atEnd.laps.map((lap) => lap.lapNumber)).toEqual([1])
    expect(beforeEnd.laps).toEqual([])
  })

  test('hides a causally incomplete flying lap', () => {
    // Arrange: lap 2 ends at 190, so at 150 it has started but not completed.
    const sidecar = qualifyingSidecar(['flying', 'flying'])

    // Act
    const selection = selectLapSectorData(sidecar, 150, 'HAM')

    // Assert
    expect(selection.laps.map((lap) => lap.lapNumber)).toEqual([1])
    // The causal sector 1 of the in-progress flying lap is still flying evidence.
    expect(selection.sectors.map((sector) => sector.lapNumber)).toEqual([1, 1, 1, 2])
  })

  test('hides a flying lap deleted by the qualifying status sidecar', () => {
    // Arrange: lap 1 completes at 90 and the deletion is effective from 100,
    // so the lap is visible between completion and the causal deletion.
    const sidecar = qualifyingSidecar(['flying'])
    const statusSidecar: QualifyingLapStatusSidecar = {
      contractVersion: 'v2', fixtureId: 'lap-sector-fixture',
      drivers: { HAM: { lapNumber: [1], lapStartMs: [0], lapEndMs: [90], status: ['deleted'], deletedReason: ['TRACK LIMITS'] } },
      events: [{ driverId: 'HAM', lapNumber: 1, eventTimeMs: 100, status: 'deleted', reason: 'TRACK LIMITS', rawMessage: 'LAP 1 DELETED' }],
    }

    // Act
    const beforeDeletion = selectLapSectorData(sidecar, 95, 'HAM', statusSidecar)
    const deleted = selectLapSectorData(sidecar, 200, 'HAM', statusSidecar)

    // Assert
    expect(beforeDeletion.laps.map((lap) => lap.lapNumber)).toEqual([1])
    expect(deleted.laps).toEqual([])
    expect(deleted.sectors).toEqual([])
  })

  test('fails closed when a qualifying-like sidecar has no lapKind column', () => {
    // Arrange: an old v2 sidecar with phase structure but no lapKind column
    // exposes no flying evidence — absence never means every lap is flying.
    const legacyQualifying: LapSectorSidecar = {
      contractVersion: 'v2', fixtureId: 'lap-sector-fixture', phaseBoundaries: [{ phase: 'Q1', startMs: 0 }], drivers: {
        HAM: {
          lapNumber: [1], lapStartMs: [0], lapEndMs: [90], lapDurationMs: [90],
          sector1DurationMs: [30], sector2DurationMs: [30], sector3DurationMs: [30],
          sector1SessionTimeMs: [30], sector2SessionTimeMs: [60], sector3SessionTimeMs: [90],
          qualifyingPhase: ['Q1'],
        },
      },
    }

    // Act
    const selection = selectLapSectorData(legacyQualifying, 200, 'HAM')

    // Assert
    expect(selection.laps).toEqual([])
    expect(selection.sectors).toEqual([])
  })

  test('preserves race/sprint sidecar behavior when no flying capability exists', () => {
    // Arrange: a race-like sidecar (no phase boundaries, no lapKind) keeps the
    // legacy behavior of showing every causally completed lap.
    // Act
    const selection = selectLapSectorData(raceLikeSidecar, 200, 'HAM')

    // Assert
    expect(selection.laps.map((lap) => lap.lapNumber)).toEqual([1, 2])
    expect(selection.sectors).toHaveLength(6)
  })
})
