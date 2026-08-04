// SPDX-License-Identifier: AGPL-3.0-or-later
import { useRef, useState, useCallback, useEffect, useLayoutEffect, type ReactNode } from 'react';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { CellRenderer } from './CellRenderer';
import { RowActions } from './RowActions';
import { ColumnFilter } from './ColumnFilter';
import { CellContextMenu } from './CellContextMenu';
import { getDisplayName, isDateField } from '../explorer-utils';
import { canAddToSearch } from '../explorer-query-builder';
import { ASSET_LABEL_COLUMN_KEY } from '@/constants/assetTableColumns';
import styles from './table.module.css';

interface ColumnDef {
  key: string;
  name: string;
  dataType: string;
  sortable: boolean;
}

interface ExplorerTableProps {
  assets: Record<string, unknown>[];
  columns: ColumnDef[];
  sortColumn: string;
  sortDirection: 'asc' | 'desc';
  onSort: (column: string) => void;
  onEditAsset: (assetId: string) => void;
  isLoading: boolean;
  columnOrder: string[];
  onColumnReorder: (newOrder: string[]) => void;
  columnWidths: Record<string, number>;
  onColumnResize: (column: string, width: number) => void;
  columnFilters: Record<string, string[]>;
  dateFilters: Record<string, number>;
  onColumnFilter: (field: string, values: string[]) => void;
  onClearColumnFilter: (field: string) => void;
  /** When provided, cells become right-clickable to add/exclude their value
   *  in the search. `op` is the operator symbol ('=' to add, '!=' to exclude).
   *  Omitted where the table has no associated search (report grid,
   *  data-source detail), which simply disables the menu. */
  onAddToSearch?: (displayName: string, value: string, op: string) => void;
  /** Rendered in the header cell above the row-actions column — the slot for
   *  actions that affect every matching row (bulk recategorize). */
  headerActions?: ReactNode;
}

// Add: the same magnifier as the Explorer search bar. Exclude: a "no-entry"
// circle for filtering a value out.
function SearchGlyph() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function ExcludeGlyph() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <line x1="5.6" y1="5.6" x2="18.4" y2="18.4" />
    </svg>
  );
}

function SortIcon({ direction, active }: { direction: 'asc' | 'desc'; active: boolean }) {
  const iconClass = `${styles.sortIcon} ${active ? styles.sortIconActive : ''}`;
  if (direction === 'asc') {
    return (
      <svg className={iconClass} viewBox="0 0 12 12" fill="currentColor">
        <path d="M6 2L10 8H2L6 2Z" />
      </svg>
    );
  }
  return (
    <svg className={iconClass} viewBox="0 0 12 12" fill="currentColor">
      <path d="M6 10L2 4H10L6 10Z" />
    </svg>
  );
}

export function ExplorerTable({
  assets,
  columns,
  sortColumn,
  sortDirection,
  onSort,
  onEditAsset,
  isLoading,
  columnOrder,
  onColumnReorder,
  columnWidths,
  onColumnResize,
  columnFilters,
  dateFilters,
  onColumnFilter,
  onClearColumnFilter,
  onAddToSearch,
  headerActions,
}: ExplorerTableProps) {
  // Which column's filter dropdown is open
  const [openFilterColumn, setOpenFilterColumn] = useState<string | null>(null);

  // Right-click "add to search" menu: cursor position + the clicked field/value.
  const [cellMenu, setCellMenu] = useState<{
    x: number;
    y: number;
    displayName: string;
    value: string;
  } | null>(null);

  const openCellMenu = useCallback(
    (e: React.MouseEvent, displayName: string, rawValue: unknown) => {
      if (rawValue === null || rawValue === undefined || rawValue === '') return;
      e.preventDefault();
      setCellMenu({ x: e.clientX, y: e.clientY, displayName, value: String(rawValue) });
    },
    [],
  );

  // Drag-drop state stored in a ref to avoid re-renders
  const draggedKeyRef = useRef<string | null>(null);
  const dragOverKeyRef = useRef<string | null>(null);
  const draggingThRef = useRef<HTMLTableCellElement | null>(null);
  const dragOverThRef = useRef<HTMLTableCellElement | null>(null);

  // Resize state stored in a ref to avoid re-renders
  const resizeRef = useRef<{
    key: string;
    startX: number;
    startWidth: number;
  } | null>(null);
  const thRefs = useRef<Record<string, HTMLTableCellElement>>({});

  // ---------------------------------------------------------------------------
  // Horizontal-scroll nudges: translucent chevrons at the table edges, shown
  // only when columns are hidden in that direction. Vertical placement is
  // JS-driven (viewport middle clamped to the table's bounds) because
  // position: sticky is defeated by .main's overflow-x: hidden ancestor.
  // ---------------------------------------------------------------------------
  const outerRef = useRef<HTMLDivElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const nudgeLeftRef = useRef<HTMLButtonElement | null>(null);
  const nudgeRightRef = useRef<HTMLButtonElement | null>(null);
  const [canScroll, setCanScroll] = useState({ left: false, right: false });

  const positionNudges = useCallback(() => {
    const outer = outerRef.current;
    if (!outer) return;
    const rect = outer.getBoundingClientRect();
    const mid = window.innerHeight / 2 - rect.top;
    const top = Math.max(70, Math.min(rect.height - 40, mid));
    for (const el of [nudgeLeftRef.current, nudgeRightRef.current]) {
      if (el) el.style.top = `${top}px`;
    }
  }, []);

  // Style writes go through refs so vertical scrolling never re-renders the
  // table; state only flips when scrollability actually changes.
  const updateNudges = useCallback(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const left = wrap.scrollLeft > 2;
    const right = wrap.scrollLeft + wrap.clientWidth < wrap.scrollWidth - 2;
    setCanScroll((prev) => (prev.left === left && prev.right === right ? prev : { left, right }));
    positionNudges();
  }, [positionNudges]);

  useEffect(() => {
    // Capture-phase scroll catches both the wrap's own horizontal scroll and
    // the page's vertical scroll (which moves the clamp target).
    window.addEventListener('scroll', updateNudges, true);
    window.addEventListener('resize', updateNudges);
    const ro = new ResizeObserver(updateNudges);
    if (wrapRef.current) ro.observe(wrapRef.current);
    return () => {
      window.removeEventListener('scroll', updateNudges, true);
      window.removeEventListener('resize', updateNudges);
      ro.disconnect();
    };
  }, [updateNudges]);

  // Re-measure when the column set, data, or column widths change the table's
  // width, and position freshly-mounted nudge buttons.
  useLayoutEffect(() => {
    updateNudges();
  }, [columns, assets, columnWidths, canScroll, updateNudges]);

  const nudgeScroll = useCallback((dir: 1 | -1) => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    wrap.scrollBy({ left: dir * Math.max(240, wrap.clientWidth * 0.7), behavior: 'smooth' });
  }, []);

  // ---------------------------------------------------------------------------
  // Drag-drop handlers
  // ---------------------------------------------------------------------------

  function handleDragStart(e: React.DragEvent<HTMLTableCellElement>, key: string) {
    // If a column resize is in progress, cancel the native drag so it
    // doesn't hijack the mousemove stream.
    if (resizeRef.current) {
      e.preventDefault();
      return;
    }
    // The pinned Asset Label column is locked first — never draggable (#1153).
    if (key === ASSET_LABEL_COLUMN_KEY) {
      e.preventDefault();
      return;
    }
    draggedKeyRef.current = key;
    e.dataTransfer.effectAllowed = 'move';
    // Apply visual feedback after the drag image is captured
    const th = e.currentTarget;
    draggingThRef.current = th;
    requestAnimationFrame(() => {
      th.classList.add(styles.thDragging);
    });
  }

  function handleDragOver(e: React.DragEvent<HTMLTableCellElement>, key: string) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';

    if (dragOverKeyRef.current !== key) {
      // Remove highlight from previous target
      if (dragOverThRef.current) {
        dragOverThRef.current.classList.remove(styles.thDragOver);
      }
      dragOverKeyRef.current = key;
      dragOverThRef.current = e.currentTarget;
      if (key !== draggedKeyRef.current) {
        e.currentTarget.classList.add(styles.thDragOver);
      }
    }
  }

  function handleDrop(e: React.DragEvent<HTMLTableCellElement>, targetKey: string) {
    e.preventDefault();

    const srcKey = draggedKeyRef.current;
    if (!srcKey || srcKey === targetKey) return;

    // Dropping onto the pinned Asset Label column would insert before it —
    // it is locked first, so the drop is rejected (#1153).
    if (targetKey === ASSET_LABEL_COLUMN_KEY) return;

    // Build new order: remove src, insert before target
    const newOrder = [...columnOrder];
    const srcIdx = newOrder.indexOf(srcKey);
    const tgtIdx = newOrder.indexOf(targetKey);
    if (srcIdx === -1 || tgtIdx === -1) return;

    newOrder.splice(srcIdx, 1);
    const insertAt = newOrder.indexOf(targetKey);
    newOrder.splice(insertAt, 0, srcKey);

    onColumnReorder(newOrder);
  }

  function handleDragEnd(_e: React.DragEvent<HTMLTableCellElement>) {
    // Clean up visual state
    if (draggingThRef.current) {
      draggingThRef.current.classList.remove(styles.thDragging);
      draggingThRef.current = null;
    }
    if (dragOverThRef.current) {
      dragOverThRef.current.classList.remove(styles.thDragOver);
      dragOverThRef.current = null;
    }
    draggedKeyRef.current = null;
    dragOverKeyRef.current = null;
  }

  // ---------------------------------------------------------------------------
  // Resize handlers
  // ---------------------------------------------------------------------------

  const getThEl = useCallback((key: string) => {
    return thRefs.current[key] ?? null;
  }, []);

  function handleResizePointerDown(e: React.PointerEvent, key: string) {
    // Only respond to primary mouse button / single touch.
    if (e.button !== undefined && e.button !== 0) return;

    e.preventDefault();
    e.stopPropagation();

    const handle = e.currentTarget as HTMLElement;
    const thEl = handle.closest('th');

    try {
      handle.setPointerCapture(e.pointerId);
    } catch {
      // Older browsers without setPointerCapture — fall back silently.
    }

    // Disable native HTML5 drag on the th during resize so the browser
    // doesn't take over the pointer stream for column reorder.
    thEl?.setAttribute('draggable', 'false');

    const measureEl = getThEl(key);
    const startWidth = measureEl ? measureEl.offsetWidth : (columnWidths[key] || 150);

    resizeRef.current = {
      key,
      startX: e.pageX,
      startWidth,
    };

    document.body.classList.add('is-resizing');
    document.body.style.cursor = 'col-resize';

    // Capture-phase listener that suppresses any HTML5 drag the browser
    // might still try to start.
    const suppressDragStart = (ev: DragEvent) => {
      ev.preventDefault();
      ev.stopPropagation();
    };
    window.addEventListener('dragstart', suppressDragStart, true);

    let rafId: number | null = null;
    let pendingWidth: number | null = null;
    const flush = () => {
      rafId = null;
      const r = resizeRef.current;
      if (r && pendingWidth !== null) {
        onColumnResize(r.key, pendingWidth);
      }
    };
    const onPointerMove = (pe: PointerEvent) => {
      const r = resizeRef.current;
      if (!r) return;
      const diff = pe.pageX - r.startX;
      const newWidth = Math.min(300, Math.max(80, r.startWidth + diff));
      const th = getThEl(r.key);
      if (th) {
        th.style.width = `${newWidth}px`;
        th.style.minWidth = `${newWidth}px`;
        th.style.maxWidth = `${newWidth}px`;
      }
      pendingWidth = newWidth;
      if (rafId === null) {
        rafId = requestAnimationFrame(flush);
      }
    };

    const cleanup = (pe: PointerEvent) => {
      const r = resizeRef.current;
      if (r) {
        const diff = pe.pageX - r.startX;
        const finalWidth = Math.min(300, Math.max(80, r.startWidth + diff));
        onColumnResize(r.key, finalWidth);
      }

      document.body.classList.remove('is-resizing');
      document.body.style.cursor = '';
      resizeRef.current = null;

      // Restore draggability — except on the pinned Asset Label column,
      // which stays undraggable (#1153).
      thEl?.setAttribute('draggable', key === ASSET_LABEL_COLUMN_KEY ? 'false' : 'true');

      if (rafId !== null) {
        cancelAnimationFrame(rafId);
        rafId = null;
      }
      window.removeEventListener('dragstart', suppressDragStart, true);
      handle.removeEventListener('pointermove', onPointerMove);
      handle.removeEventListener('pointerup', cleanup);
      handle.removeEventListener('pointercancel', cleanup);
      handle.removeEventListener('lostpointercapture', cleanup as EventListener);

      try {
        handle.releasePointerCapture(e.pointerId);
      } catch {
        // ignore
      }
    };

    handle.addEventListener('pointermove', onPointerMove);
    handle.addEventListener('pointerup', cleanup);
    handle.addEventListener('pointercancel', cleanup);
    handle.addEventListener('lostpointercapture', cleanup as EventListener);
  }

  return (
    <div className={styles.tableOuter} ref={outerRef}>
    <div className={styles.tableWrap} ref={wrapRef}>
      <table className={styles.table}>
        {/* No <colgroup> — Chromium ignores col-level CSS width changes once
            the table has been laid out, so we set widths directly on each
            <th> instead. The browser uses the first-row cell widths to size
            columns under table-layout: fixed. */}
        <thead>
          <tr>
            <th className={`${styles.th} ${styles.actionHeadCell}`} style={{ width: 40, minWidth: 40, maxWidth: 40 }}>
              {headerActions}
            </th>
            {columns.map((col) => {
              const isActive = sortColumn === col.key;
              // The pinned Asset Label column is locked first: not draggable,
              // and handleDrop refuses inserts ahead of it (#1153).
              const isPinned = col.key === ASSET_LABEL_COLUMN_KEY;

              const isDateType = isDateField(col.key, col.dataType);
              const selectedValues: string[] = isDateType
                ? (dateFilters[col.key] !== undefined ? [String(dateFilters[col.key])] : [])
                : (columnFilters[col.key] ?? []);

              const w = columnWidths[col.key] || 150;
              return (
                <th
                  key={col.key}
                  ref={(el) => {
                    if (el) thRefs.current[col.key] = el;
                  }}
                  className={styles.th}
                  style={{
                    position: 'relative',
                    width: w,
                    minWidth: w,
                    maxWidth: w,
                  }}
                  draggable={!isPinned && openFilterColumn !== col.key}
                  onDragStart={(e) => handleDragStart(e, col.key)}
                  onDragOver={(e) => handleDragOver(e, col.key)}
                  onDrop={(e) => handleDrop(e, col.key)}
                  onDragEnd={handleDragEnd}
                >
                  <span
                    className={`${styles.thLabel} ${col.sortable ? styles.thSortable : ''}`}
                    onClick={col.sortable ? () => onSort(col.key) : undefined}
                  >
                    <span className={styles.thContent}>
                      {getDisplayName(col)}
                    </span>
                    {col.sortable && (
                      <SortIcon
                        direction={isActive ? sortDirection : 'asc'}
                        active={isActive}
                      />
                    )}
                  </span>
                  <ColumnFilter
                    field={col.key}
                    dataType={col.dataType}
                    selectedValues={selectedValues}
                    onApply={(field, values) => {
                      onColumnFilter(field, values);
                      setOpenFilterColumn(null);
                    }}
                    onClear={(field) => {
                      onClearColumnFilter(field);
                      setOpenFilterColumn(null);
                    }}
                    isOpen={openFilterColumn === col.key}
                    onToggle={() =>
                      setOpenFilterColumn((prev) => (prev === col.key ? null : col.key))
                    }
                  />
                  <div
                    className={styles.resizeHandle}
                    onPointerDown={(e) => handleResizePointerDown(e, col.key)}
                    onPointerEnter={(e) => {
                      // Chrome locks in the parent th's draggable state at
                      // pointerdown time. Disable it preemptively the moment
                      // the cursor enters the handle, so the click doesn't
                      // get hijacked into a column-reorder drag.
                      (e.currentTarget as HTMLElement)
                        .closest('th')
                        ?.setAttribute('draggable', 'false');
                    }}
                    onPointerLeave={(e) => {
                      // Restore drag for column reorder once the cursor
                      // leaves, but only if no resize is in progress — and
                      // never on the pinned column (#1153).
                      if (resizeRef.current) return;
                      (e.currentTarget as HTMLElement)
                        .closest('th')
                        ?.setAttribute('draggable', isPinned ? 'false' : 'true');
                    }}
                    onClick={(e) => e.stopPropagation()}
                    onDragStart={(e) => e.preventDefault()}
                    draggable={false}
                  />
                </th>
              );
            })}
            <th className={`${styles.th} ${styles.spacerCell}`} aria-hidden="true" style={{ width: 'auto' }} />
          </tr>
        </thead>
        <tbody>
          {assets.length === 0 && !isLoading && (
            <tr>
              <td
                colSpan={columns.length + 2}
                className={styles.emptyState}
              >
                No assets found
              </td>
            </tr>
          )}
          {assets.map((asset, idx) => {
            const id = String(asset.id ?? idx);
            return (
              <tr key={id} className={styles.tr}>
                <RowActions assetId={id} onEdit={onEditAsset} />
                {columns.map((col) => {
                  const addable = !!onAddToSearch && canAddToSearch(col.dataType);
                  const multiValue =
                    col.dataType === 'string_array' || col.dataType === 'jsonb_array';
                  return (
                    <td
                      key={col.key}
                      className={styles.td}
                      onContextMenu={
                        addable && !multiValue
                          ? (e) => openCellMenu(e, col.name, asset[col.key])
                          : undefined
                      }
                    >
                      <CellRenderer
                        value={asset[col.key]}
                        dataType={col.dataType}
                        fieldKey={col.key}
                        onContextMenu={
                          addable && multiValue
                            ? (e, v) => openCellMenu(e, col.name, v)
                            : undefined
                        }
                      />
                    </td>
                  );
                })}
                <td className={`${styles.td} ${styles.spacerCell}`} aria-hidden="true" />
              </tr>
            );
          })}
        </tbody>
      </table>
      {isLoading && (
        <div className={styles.loadingOverlay}>
          <LoadingSpinner size="md" />
        </div>
      )}
      {cellMenu && onAddToSearch && (
        <CellContextMenu
          x={cellMenu.x}
          y={cellMenu.y}
          items={[
            {
              label: 'Add to search',
              detail: `${cellMenu.displayName} = ${cellMenu.value}`,
              icon: <SearchGlyph />,
              onSelect: () => onAddToSearch(cellMenu.displayName, cellMenu.value, '='),
            },
            {
              label: 'Exclude from search',
              detail: `${cellMenu.displayName} != ${cellMenu.value}`,
              icon: <ExcludeGlyph />,
              onSelect: () => onAddToSearch(cellMenu.displayName, cellMenu.value, '!='),
            },
          ]}
          onClose={() => setCellMenu(null)}
        />
      )}
    </div>
    {canScroll.left && (
      <button
        ref={nudgeLeftRef}
        className={`${styles.scrollNudge} ${styles.scrollNudgeLeft}`}
        onClick={() => nudgeScroll(-1)}
        aria-label="Scroll columns left"
        title="More columns to the left"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </button>
    )}
    {canScroll.right && (
      <button
        ref={nudgeRightRef}
        className={`${styles.scrollNudge} ${styles.scrollNudgeRight}`}
        onClick={() => nudgeScroll(1)}
        aria-label="Scroll columns right"
        title="More columns to the right"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>
    )}
    </div>
  );
}
