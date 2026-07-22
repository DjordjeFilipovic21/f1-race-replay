export const TABLET_WORKSPACE_COLUMNS = 2
export const DESKTOP_WORKSPACE_COLUMNS = 4
export const WORKSPACE_GAP_PX = 12
export const COLUMN_HYSTERESIS_PX = 8

export interface MasonryPlacementItem {
  readonly id: string
  readonly columnStart: number
  readonly columns: 1 | 2
  readonly rowSpan: number
}

export interface MasonryPreview extends MasonryPlacementItem {
  readonly index: number
}

export interface PanelVerticalGeometry {
  readonly top: number
  readonly bottom: number
}

export function columnStartFromDropCenter(dropCenterX: number, workspaceLeft: number, workspaceWidth: number, columnCount: number, panelColumns: 1 | 2): number | null {
  if (!Number.isFinite(dropCenterX) || !Number.isFinite(workspaceLeft) || !Number.isFinite(workspaceWidth) || workspaceWidth <= 0 || !Number.isInteger(columnCount) || columnCount < 1) return null
  const columns = Math.max(1, columnCount)
  const span: 1 | 2 = panelColumns === 2 && columns > 1 ? 2 : 1
  const columnWidth = (workspaceWidth - (columns - 1) * WORKSPACE_GAP_PX) / columns
  if (columnWidth <= 0) return null
  const panelWidth = columnWidth * span + WORKSPACE_GAP_PX * (span - 1)
  const rawStart = Math.round((dropCenterX - workspaceLeft - panelWidth / 2) / (columnWidth + WORKSPACE_GAP_PX)) + 1
  return clampColumnStart(rawStart, columns, span)
}

/** Holds a column until the shape center deliberately clears its boundary dead-zone. */
export function columnStartWithHysteresis(previousColumnStart: number | null, nextColumnStart: number, centerX: number, workspaceLeft: number, workspaceWidth: number, columnCount: number, panelColumns: 1 | 2): number {
  const next = clampColumnStart(nextColumnStart, columnCount, panelColumns)
  if (previousColumnStart === null || !Number.isFinite(centerX) || !Number.isFinite(workspaceLeft) || !Number.isFinite(workspaceWidth) || workspaceWidth <= 0) return next
  const previous = clampColumnStart(previousColumnStart, columnCount, panelColumns)
  if (previous === next) return next
  const columns = Math.max(1, columnCount)
  const span: 1 | 2 = panelColumns === 2 && columns > 1 ? 2 : 1
  const columnWidth = (workspaceWidth - (columns - 1) * WORKSPACE_GAP_PX) / columns
  if (columnWidth <= 0) return next
  const step = columnWidth + WORKSPACE_GAP_PX
  const panelWidth = columnWidth * span + WORKSPACE_GAP_PX * (span - 1)
  const previousCenter = workspaceLeft + (previous - 1) * step + panelWidth / 2
  const nextCenter = workspaceLeft + (next - 1) * step + panelWidth / 2
  const boundary = (previousCenter + nextCenter) / 2
  return next > previous
    ? centerX > boundary + COLUMN_HYSTERESIS_PX ? next : previous
    : centerX < boundary - COLUMN_HYSTERESIS_PX ? next : previous
}

export function clampColumnStart(columnStart: number, columnCount: number, panelColumns: 1 | 2): number {
  const columns = Number.isInteger(columnCount) && columnCount > 0 ? columnCount : 1
  const span: 1 | 2 = panelColumns === 2 && columns > 1 ? 2 : 1
  const validStart = Number.isInteger(columnStart) ? columnStart : 1
  return Math.min(Math.max(validStart, 1), columns - span + 1)
}

export function responsiveColumnStart(desktopColumnStart: number, panelColumns: 1 | 2, columnCount: number): number {
  if (columnCount <= 1) return 1
  if (panelColumns === 2 && columnCount === TABLET_WORKSPACE_COLUMNS) return 1
  return clampColumnStart(columnCount === TABLET_WORKSPACE_COLUMNS ? Math.min(desktopColumnStart, 2) : desktopColumnStart, columnCount, panelColumns)
}

export function workspaceColumnCount(viewportWidth: number): number {
  if (!Number.isFinite(viewportWidth) || viewportWidth < 768) return 1
  return viewportWidth >= 1024 ? DESKTOP_WORKSPACE_COLUMNS : TABLET_WORKSPACE_COLUMNS
}

/** Resolves canonical order from the target lane's measured vertical geometry. */
export function resolveVerticalInsertionIndex(items: readonly MasonryPlacementItem[], activeId: string, targetColumnStart: number, targetColumns: 1 | 2, geometryById: ReadonlyMap<string, PanelVerticalGeometry>, centerY: number, fallbackIndex: number): number {
  if (!Number.isFinite(centerY)) return fallbackIndex
  const targetEnd = targetColumnStart + targetColumns - 1
  const candidates = items
    .filter((item) => item.id !== activeId)
    .map((item) => ({ item, geometry: geometryById.get(item.id) }))
    .filter(({ item }) => rangesOverlap(item.columnStart, item.columns, targetColumnStart, targetEnd))
  if (candidates.length === 0 || candidates.some(({ geometry }) => geometry === undefined || !isFiniteGeometry(geometry))) return fallbackIndex

  for (let index = 0; index < candidates.length; index += 1) {
    const candidate = candidates[index]
    if (centerY < (candidate.geometry!.top + candidate.geometry!.bottom) / 2) {
      return items.filter((item) => item.id !== activeId).findIndex((item) => item.id === candidate.item.id)
    }
  }
  const last = candidates.at(-1)!.item.id
  return items.filter((item) => item.id !== activeId).findIndex((item) => item.id === last) + 1
}

function rangesOverlap(columnStart: number, columns: 1 | 2, targetStart: number, targetEnd: number): boolean {
  return columnStart <= targetEnd && columnStart + columns - 1 >= targetStart
}

function isFiniteGeometry(geometry: PanelVerticalGeometry | undefined): geometry is PanelVerticalGeometry {
  return geometry !== undefined && Number.isFinite(geometry.top) && Number.isFinite(geometry.bottom) && geometry.bottom >= geometry.top
}

/** Predicts dense grid placement without introducing a preview item into grid geometry. */
export function previewMasonryRow(items: readonly MasonryPlacementItem[], preview: MasonryPreview, columnCount: number): number {
  const withoutPreview = items.filter((item) => item.id !== preview.id)
  const index = Math.min(Math.max(preview.index, 0), withoutPreview.length)
  const prospective = [...withoutPreview.slice(0, index), preview, ...withoutPreview.slice(index)]
  const occupied: boolean[][] = []
  let previewRow = 1

  prospective.forEach((item) => {
    const span: 1 | 2 = item.columns === 2 && columnCount > 1 ? 2 : 1
    const columnStart = clampColumnStart(item.columnStart, columnCount, span)
    const rowSpan = Number.isInteger(item.rowSpan) && item.rowSpan > 0 ? item.rowSpan : 1
    const row = firstAvailableRow(occupied, columnStart, span, rowSpan)
    occupy(occupied, row, columnStart, span, rowSpan)
    if (item.id === preview.id) previewRow = row
  })
  return previewRow
}

function firstAvailableRow(occupied: readonly boolean[][], columnStart: number, columns: 1 | 2, rowSpan: number): number {
  let row = 1
  while (!isAvailable(occupied, row, columnStart, columns, rowSpan)) row += 1
  return row
}

function isAvailable(occupied: readonly boolean[][], rowStart: number, columnStart: number, columns: 1 | 2, rowSpan: number): boolean {
  for (let row = rowStart; row < rowStart + rowSpan; row += 1) {
    for (let column = columnStart; column < columnStart + columns; column += 1) {
      if (occupied[row - 1]?.[column - 1] === true) return false
    }
  }
  return true
}

function occupy(occupied: boolean[][], rowStart: number, columnStart: number, columns: 1 | 2, rowSpan: number): void {
  for (let row = rowStart; row < rowStart + rowSpan; row += 1) {
    const cells = occupied[row - 1] ?? (occupied[row - 1] = [])
    for (let column = columnStart; column < columnStart + columns; column += 1) cells[column - 1] = true
  }
}
