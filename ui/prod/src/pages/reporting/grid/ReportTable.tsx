// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useCallback, useMemo, useEffect } from 'react';
import { ExplorerTable } from '@/pages/explorer/table/ExplorerTable';
import { useAssets, useExplorerFields } from '@/api/assets';
import type { AssetListParams } from '@/api/assets';
import type { FieldId } from '@/types/field-id';
import { buildFieldMap } from '@/pages/explorer/explorer-utils';
import {
  ASSET_TABLE_DEFAULT_COLUMNS,
  normalizeColumnOrder,
} from '@/constants/assetTableColumns';
import styles from './ReportTable.module.css';

interface ReportTableProps {
  columns?: string[];
  filters?: { field_id: FieldId; operator: string; value?: unknown }[];
  itemsPerPage?: number;
}

// Public export — ChartEditor seeds new Data Table charts with this set and
// the editor tests import it. Alias of the shared asset-table defaults so all
// three ExplorerTable surfaces stay in step (#1153).
export const DEFAULT_COLUMNS = ASSET_TABLE_DEFAULT_COLUMNS;

// The chart wizard's FilterRow emits underscored display strings
// ("is_not_empty", "greater_than"); the asset list endpoint
// (src/api/models/requests.py::AssetFilter) validates `op` against an
// abbreviated vocabulary (notempty/gt/...). Without translation only
// "contains" — the lone overlap — survives validation and every other
// filter on a Data Table chart returns 400.
const CHART_TO_ASSET_OP: Record<string, string> = {
  // Underscore form (FilterRow.normalizeOperator output)
  equals: 'eq',
  not_equals: 'neq',
  contains: 'contains',
  not_contains: 'ncontains',
  starts_with: 'starts',
  ends_with: 'ends',
  is_empty: 'empty',
  is_not_empty: 'notempty',
  greater_than: 'gt',
  greater_than_or_equal: 'gte',
  less_than: 'lt',
  less_than_or_equal: 'lte',
  any_of: 'any',
  // Spaced display form (defensive — saved configs that bypassed FilterRow)
  'not equals': 'neq',
  'not contains': 'ncontains',
  'starts with': 'starts',
  'ends with': 'ends',
  'is empty': 'empty',
  'is not empty': 'notempty',
  'greater than': 'gt',
  'greater than or equal': 'gte',
  'less than': 'lt',
  'less than or equal': 'lte',
  'any of': 'any',
};

export function toAssetFilterOp(chartOp: string): string {
  return CHART_TO_ASSET_OP[chartOp] ?? chartOp;
}

/**
 * Lightweight wrapper around ExplorerTable for use in reporting cards.
 * Self-contained: loads its own data, no URL sync, no localStorage.
 */
export function ReportTable({ columns, filters, itemsPerPage = 25 }: ReportTableProps) {
  // A chart config is an explicit column selection: normalization enforces
  // the pinned Asset Label's position when the config includes it, but an
  // absent pin is the author's choice, not stale storage (ensurePinned:
  // false) — unlike Explorer/DSD persistence, which always restores it.
  const visibleColumns = normalizeColumnOrder(
    columns && columns.length > 0 ? columns : DEFAULT_COLUMNS,
    { ensurePinned: false },
  );

  const [page, setPage] = useState(1);
  const [sortColumn, setSortColumn] = useState('static:Last%20Seen');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
  const [columnOrder, setColumnOrder] = useState(visibleColumns);

  // Resync columnOrder when the configured columns change. `useState`
  // captures the initial value once, so without this the table kept
  // showing the originally rendered set even after the user changed the
  // chart settings — i.e. the saved chart config was effectively ignored.
  // The `columns` prop reference changes whenever the parent rebuilds
  // queryConfig.options, so a stable string key is enough.
  const columnsKey = columns ? columns.join(',') : '';
  useEffect(() => {
    setColumnOrder(
      normalizeColumnOrder(columns && columns.length > 0 ? columns : DEFAULT_COLUMNS, {
        ensurePinned: false,
      }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columnsKey]);
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>({});
  const [columnFilters, setColumnFilters] = useState<Record<string, string[]>>({});
  const [dateFilters] = useState<Record<string, number>>({});

  const apiFilters = useMemo(() => {
    if (!filters || filters.length === 0) return undefined;
    return JSON.stringify(
      filters.map((f) => ({
        field_id: f.field_id,
        op: toAssetFilterOp(f.operator),
        value: f.value,
      })),
    );
  }, [filters]);

  // Fetch data
  const params: AssetListParams = useMemo(
    () => ({
      page,
      limit: itemsPerPage,
      fields: columnOrder.join(','),
      sort_by: sortColumn,
      sort_dir: sortDirection,
      filters: apiFilters,
    }),
    [page, itemsPerPage, columnOrder, sortColumn, sortDirection, apiFilters],
  );

  const { data, isLoading } = useAssets(params);
  const { data: fieldsData } = useExplorerFields();
  const fieldMap = useMemo(
    () => buildFieldMap(fieldsData?.entries),
    [fieldsData],
  );

  const columnDefs = useMemo(() => {
    return columnOrder.map((key) => {
      const f = fieldMap[key];
      return {
        key,
        name: f?.display_name ?? key,
        dataType: f?.data_type ?? 'string',
        sortable: true,
      };
    });
  }, [columnOrder, fieldMap]);

  const assets = data?.assets ?? [];
  const totalPages = data?.pagination?.pages ?? 0;
  const totalAssets = data?.pagination?.total ?? 0;

  const handleSort = useCallback((col: string) => {
    if (col === sortColumn) {
      setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortColumn(col);
      setSortDirection('desc');
    }
    setPage(1);
  }, [sortColumn]);

  const handleColumnResize = useCallback((col: string, width: number) => {
    setColumnWidths((prev) => ({ ...prev, [col]: width }));
  }, []);

  const handleColumnFilter = useCallback((field: string, values: string[]) => {
    setColumnFilters((prev) => ({ ...prev, [field]: values }));
  }, []);

  const handleClearColumnFilter = useCallback((field: string) => {
    setColumnFilters((prev) => {
      const next = { ...prev };
      delete next[field];
      return next;
    });
  }, []);

  const handleColumnReorder = useCallback((order: string[]) => {
    setColumnOrder(normalizeColumnOrder(order, { ensurePinned: false }));
  }, []);

  return (
    <div className={styles.wrapper}>
      <div className={styles.tableWrap}>
        <ExplorerTable
          assets={assets}
          columns={columnDefs}
          sortColumn={sortColumn}
          sortDirection={sortDirection}
          onSort={handleSort}
          onEditAsset={() => {}}
          isLoading={isLoading}
          columnOrder={columnOrder}
          onColumnReorder={handleColumnReorder}
          columnWidths={columnWidths}
          onColumnResize={handleColumnResize}
          columnFilters={columnFilters}
          dateFilters={dateFilters}
          onColumnFilter={handleColumnFilter}
          onClearColumnFilter={handleClearColumnFilter}
        />
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className={styles.pagination}>
          <span className={styles.pageInfo}>{totalAssets.toLocaleString()} assets</span>
          <div className={styles.pageButtons}>
            <button disabled={page <= 1} onClick={() => setPage(1)}>First</button>
            <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
            <span className={styles.pageNum}>{page} / {totalPages}</span>
            <button disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>Next</button>
            <button disabled={page >= totalPages} onClick={() => setPage(totalPages)}>Last</button>
          </div>
        </div>
      )}
    </div>
  );
}
