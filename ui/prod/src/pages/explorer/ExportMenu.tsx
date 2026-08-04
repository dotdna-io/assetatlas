// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { assetsApi } from '@/api/assets';
import type { AggregateParams } from '@/api/assets';
import { generateCSV } from './explorer-utils';
import styles from '../ExplorerPage.module.css';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ExportMenuProps {
  isOpen: boolean;
  onClose: () => void;
  totalFiltered: number;
  pivotMode: boolean;
  visibleColumns: { key: string; name: string }[];
  filters?: string;
  search?: string;
  sortBy?: string;
  sortDir?: string;
  pivotConfig?: { rowDimension: string; columnDimension: string; columnsEnabled: boolean };
}

interface ProgressState {
  current: number;
  total: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function todayISO(): string {
  const d = new Date();
  const yr = d.getFullYear();
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  const dy = String(d.getDate()).padStart(2, '0');
  return `${yr}-${mo}-${dy}`;
}

function downloadCSV(csv: string, filename: string): void {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Progress badge (rendered via portal)
// ---------------------------------------------------------------------------

function ExportProgress({ progress }: { progress: ProgressState }) {
  return createPortal(
    <div
      style={{
        position: 'fixed',
        bottom: 16,
        right: 16,
        padding: '8px 16px',
        background: 'var(--accent)',
        color: 'white',
        borderRadius: 'var(--radius-sm)',
        fontSize: 'var(--font-size-xs)',
        fontWeight: 600,
        zIndex: 9999,
        boxShadow: 'var(--shadow-md)',
      }}
    >
      Exporting... {progress.current}/{progress.total}
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ExportMenu({
  isOpen,
  onClose,
  totalFiltered,
  pivotMode,
  visibleColumns,
  filters,
  search,
  sortBy,
  sortDir,
  pivotConfig,
}: ExportMenuProps) {
  const [progress, setProgress] = useState<ProgressState | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef(false);

  // Close when clicking outside
  useEffect(() => {
    if (!isOpen) return;
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [isOpen, onClose]);

  // Reset abort flag when menu opens
  useEffect(() => {
    if (isOpen) {
      abortRef.current = false;
    }
  }, [isOpen]);

  const batchFetch = useCallback(
    async (includeFilters: boolean): Promise<Record<string, unknown>[]> => {
      const BATCH = 5000;
      const fields = visibleColumns.map((c) => c.key).join(',');

      // The alert scope is an ordinary entry in filters[] now, so it rides along
      // with every other filter instead of a param of its own — which is also
      // why Full drops it exactly as it drops the rest (D7, design §4.6).
      // First request to get total
      const first = await assetsApi.list({
        page: 1,
        limit: BATCH,
        fields,
        ...(includeFilters && filters ? { filters } : {}),
        ...(includeFilters && search ? { search } : {}),
        ...(sortBy ? { sort_by: sortBy } : {}),
        ...(sortDir ? { sort_dir: sortDir as 'asc' | 'desc' } : {}),
      });

      const totalPages = first.pagination.pages;
      setProgress({ current: 1, total: totalPages });

      const all: Record<string, unknown>[] = [...first.assets];

      for (let page = 2; page <= totalPages; page++) {
        if (abortRef.current) break;
        const resp = await assetsApi.list({
          page,
          limit: BATCH,
          fields,
          ...(includeFilters && filters ? { filters } : {}),
          ...(includeFilters && search ? { search } : {}),
          ...(sortBy ? { sort_by: sortBy } : {}),
          ...(sortDir ? { sort_dir: sortDir as 'asc' | 'desc' } : {}),
        });
        all.push(...resp.assets);
        setProgress({ current: page, total: totalPages });
      }

      return all;
    },
    [visibleColumns, filters, search, sortBy, sortDir],
  );

  const handleExportFull = useCallback(async () => {
    onClose();
    try {
      const data = await batchFetch(false);
      const csv = generateCSV(data, visibleColumns);
      downloadCSV(csv, `assetatlas-full-inventory-${todayISO()}.csv`);
    } catch (err) {
      console.error('Export failed:', err);
    } finally {
      setProgress(null);
    }
  }, [batchFetch, visibleColumns, onClose]);

  const handleExportFiltered = useCallback(async () => {
    onClose();
    try {
      const data = await batchFetch(true);
      const csv = generateCSV(data, visibleColumns);
      downloadCSV(csv, `assetatlas-filtered-${todayISO()}.csv`);
    } catch (err) {
      console.error('Export filtered failed:', err);
    } finally {
      setProgress(null);
    }
  }, [batchFetch, visibleColumns, onClose]);

  const handleExportPivot = useCallback(async () => {
    if (!pivotConfig) return;
    onClose();
    setProgress({ current: 1, total: 1 });
    try {
      const params: AggregateParams = {
        row_field: pivotConfig.rowDimension,
        ...(pivotConfig.columnsEnabled && pivotConfig.columnDimension
          ? { col_field: pivotConfig.columnDimension }
          : {}),
        // The pivot always passes filters, so it inherits the alert scope the
        // same way the row exports do — no alert param of its own (#1013).
        ...(filters ? { filters } : {}),
      };

      const resp = await assetsApi.aggregate(params);
      const { rows, columns, data } = resp;

      // Build header: row dimension label + column names + Total
      const rowDimLabel = pivotConfig.rowDimension;
      const header = [rowDimLabel, ...columns, 'Total'].map(escapeCSVVal).join(',');

      // Build data rows — data is { rowKey: { colKey: count } }
      const csvRows: string[] = [header];
      for (let ri = 0; ri < rows.length; ri++) {
        const row = rows[ri];
        const rowData = data[row] ?? {};
        const rowTotal = columns.reduce((s: number, col: string) => s + (rowData[col] ?? 0), 0);
        const cells = [
          escapeCSVVal(row),
          ...columns.map((col: string) => String(rowData[col] ?? 0)),
          String(rowTotal),
        ];
        csvRows.push(cells.join(','));
      }

      // Total row
      const colTotals = columns.map((col: string) =>
        rows.reduce((s: number, row: string) => s + (data[row]?.[col] ?? 0), 0),
      );
      const grandTotal = colTotals.reduce((s, v) => s + v, 0);
      const totalRow = [
        'Total',
        ...colTotals.map(String),
        String(grandTotal),
      ];
      csvRows.push(totalRow.join(','));

      const csv = csvRows.join('\r\n');
      downloadCSV(csv, `assetatlas-pivot-${todayISO()}.csv`);
    } catch (err) {
      console.error('Pivot export failed:', err);
    } finally {
      setProgress(null);
    }
  }, [pivotConfig, filters, onClose]);

  if (!isOpen && !progress) return null;

  return (
    <>
      {progress && <ExportProgress progress={progress} />}
      {isOpen && (
        <div ref={menuRef} className={styles.exportMenu}>
          <div
            className={styles.exportMenuItem}
            role="button"
            tabIndex={0}
            onClick={handleExportFull}
            onKeyDown={(e) => e.key === 'Enter' && handleExportFull()}
          >
            Export Full Inventory
          </div>
          <div
            className={styles.exportMenuItem}
            role="button"
            tabIndex={0}
            onClick={handleExportFiltered}
            onKeyDown={(e) => e.key === 'Enter' && handleExportFiltered()}
          >
            Export Filtered Data ({totalFiltered.toLocaleString()} items)
          </div>
          {pivotMode && (
            // Pivot narrows to the alert scope through GET /assets/aggregate now
            // (#1013), so it is a live item like the row exports — no per-item
            // disable.
            <div
              className={styles.exportMenuItem}
              role="button"
              tabIndex={0}
              onClick={handleExportPivot}
              onKeyDown={(e) => e.key === 'Enter' && handleExportPivot()}
            >
              Export Pivot Table
            </div>
          )}
        </div>
      )}
    </>
  );
}

// Simple CSV escaper for pivot cells (not using generateCSV since we build rows manually)
function escapeCSVVal(val: unknown): string {
  if (val === null || val === undefined) return '';
  const str = String(val);
  if (str.includes(',') || str.includes('"') || str.includes('\n') || str.includes('\r')) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}
