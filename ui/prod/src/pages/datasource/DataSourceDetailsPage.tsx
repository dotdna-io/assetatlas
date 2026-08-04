// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useCallback, useMemo, useRef, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useDocumentTitle } from '@/hooks';
import { useDataSources } from '@/api/dashboard';
import { useIntegrationTimeline } from '@/api/integrations';
import { useAssets, useExplorerFields, useAssetAggregate } from '@/api/assets';
import type { AssetListParams } from '@/api/assets';
import { buildFieldMap } from '@/pages/explorer/explorer-utils';
import {
  loadAssetTableColumnConfig,
  normalizeColumnOrder,
  saveAssetTableColumnConfig,
  type ColumnConfigStorageKeys,
} from '@/constants/assetTableColumns';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { resolveSourceLogo } from '@/lib/logoUrl';
import { ConfidenceDonutGauge } from '@/components/charts/ConfidenceDonutGauge';
import { ButterflyChart } from '@/components/charts/ButterflyChart';
import { CategoryBars } from '@/components/charts/CategoryBars';
import { ExplorerTable } from '@/pages/explorer/table/ExplorerTable';
import { ColumnSelector } from '@/pages/explorer/table/ColumnSelector';
import { ExportMenu } from '@/pages/explorer/ExportMenu';
import { PivotTable } from '@/pages/explorer/pivot/PivotTable';
import { PivotControls } from '@/pages/explorer/pivot/PivotControls';
import { formatRelativeTime } from '@/pages/integrations/integration-utils';
import styles from './DataSourceDetailsPage.module.css';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type SyncStatus = 'ok' | 'aging' | 'stale' | 'never';

function getSyncStatus(syncTime: string | null | undefined): SyncStatus {
  if (!syncTime) return 'never';
  const days = (Date.now() - new Date(syncTime).getTime()) / 86_400_000;
  if (days > 7) return 'stale';
  if (days > 1) return 'aging';
  return 'ok';
}

function statusClass(status: string): string {
  switch (status) {
    case 'healthy': return styles.statusOk;
    case 'warning': return styles.statusWarning;
    case 'error':   return styles.statusError;
    default:        return styles.statusNeutral;
  }
}

// Column order/visibility/widths persist under their own versioned namespace,
// mirroring Explorer's scheme; the default list itself is shared across all
// ExplorerTable surfaces — see constants/assetTableColumns.ts (#1153).
export const DSD_COLUMN_STORAGE_KEYS: ColumnConfigStorageKeys = {
  order: 'dsdColumnOrder_v1',
  visible: 'dsdVisibleColumns_v1',
  widths: 'dsdColumnWidths_v1',
};

const RANGE_DAYS: Record<'7d' | '30d' | '90d', number> = { '7d': 7, '30d': 30, '90d': 90 };

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function DataSourceDetailsPage() {
  const [searchParams] = useSearchParams();
  const idParam = searchParams.get('id');
  const nameParam = searchParams.get('source');

  useDocumentTitle(nameParam ?? 'Data Source');

  // ─── Source ──────────────────────────────────────────────────────────────
  const { data: dsRaw, isLoading: dsLoading } = useDataSources();
  const dsData = (dsRaw as any) ?? { sources: [] };

  const source = useMemo(() => {
    if (!dsData?.sources) return null;
    if (nameParam) {
      const byName = dsData.sources.find(
        (s: any) => s.name?.toLowerCase() === nameParam.toLowerCase(),
      );
      if (byName) return byName;
    }
    if (idParam) {
      return dsData.sources.find((s: any) => s.id === idParam) ?? null;
    }
    return null;
  }, [dsData, idParam, nameParam]);

  const integrationId = source?.id;
  const sourceName = source?.name ?? '';
  const assetCount = source?.assetCount ?? 0;
  const syncTime = source?.lastSync ?? null;
  const syncStatus = getSyncStatus(syncTime);
  const syncText = formatRelativeTime(syncTime);
  const status = source?.status ?? 'unknown';
  const conf = source?.confidenceDistribution ?? null;
  const avgConfidence: number | null =
    typeof source?.confidence === 'number' ? source.confidence : null;

  const logoSrc = resolveSourceLogo(source?.logo, sourceName);

  // ─── Butterfly timeline ──────────────────────────────────────────────────
  const [timeRange, setTimeRange] = useState<'7d' | '30d' | '90d'>('30d');
  const { data: timelineRaw } = useIntegrationTimeline(
    integrationId,
    RANGE_DAYS[timeRange],
  );

  const timelineData = useMemo(() => {
    const tl = timelineRaw?.data;
    return Array.isArray(tl) ? tl : [];
  }, [timelineRaw]);

  const { totalAdded, totalReturned, totalRemoved } = useMemo(() => {
    let added = 0, returned = 0, removed = 0;
    for (const d of timelineData) {
      added += Number(d.added) || 0;
      returned += Number(d.returned) || 0;
      removed += Number(d.removed) || 0;
    }
    return { totalAdded: added, totalReturned: returned, totalRemoved: removed };
  }, [timelineData]);

  // ─── Asset table state ───────────────────────────────────────────────────
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<25 | 50 | 100>(25);
  const [sortColumn, setSortColumn] = useState('static:Last%20Seen');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
  // Column config loads once per mount from localStorage (already pin-
  // normalized by the loader) and persists on every change.
  const [initialColumnConfig] = useState(() => loadAssetTableColumnConfig(DSD_COLUMN_STORAGE_KEYS));
  const [columnOrder, setColumnOrder] = useState(initialColumnConfig.columnOrder);
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>(initialColumnConfig.columnWidths);
  const [columnFilters, setColumnFilters] = useState<Record<string, string[]>>({});
  const [dateFilters] = useState<Record<string, number>>({});
  const [visibleColumns, setVisibleColumns] = useState<Record<string, boolean>>(initialColumnConfig.visibleColumns);

  useEffect(() => {
    saveAssetTableColumnConfig(DSD_COLUMN_STORAGE_KEYS, {
      columnOrder,
      visibleColumns,
      columnWidths,
    });
  }, [columnOrder, visibleColumns, columnWidths]);

  const [pivotMode, setPivotMode] = useState(false);
  const [pivotConfig, setPivotConfig] = useState({
    rowDimension: 'static:Category',
    columnDimension: '',
    columnsEnabled: false,
    displayMode: 'count' as 'count' | 'percent_total' | 'percent_row' | 'percent_col',
  });
  const [isColumnSelectorOpen, setIsColumnSelectorOpen] = useState(false);
  const [isExportMenuOpen, setIsExportMenuOpen] = useState(false);
  const columnsBtnRef = useRef<HTMLButtonElement>(null);

  const tableFilters = useMemo(() => {
    if (!sourceName) return undefined;
    return JSON.stringify([
      {
        field_id: { kind: 'static', name: 'Data Source' },
        op: 'contains',
        value: sourceName,
      },
    ]);
  }, [sourceName]);

  // ─── Distribution by asset category (for this source) ────────────────────
  const { data: categoryAgg } = useAssetAggregate({
    row_field: sourceName ? 'static:Category' : '',
    filters: tableFilters,
  });
  const categoryDist = useMemo(() => {
    const rows = categoryAgg?.rows ?? [];
    const data = categoryAgg?.data ?? {};
    return rows
      .map((cat) => ({ category: cat, count: data[cat]?.count ?? 0 }))
      .filter((d) => d.count > 0);
  }, [categoryAgg]);

  const tableParams: AssetListParams = useMemo(
    () => ({
      page,
      limit: pageSize,
      fields: columnOrder.filter((c) => visibleColumns[c] !== false).join(','),
      sort_by: sortColumn,
      sort_dir: sortDirection,
      filters: tableFilters,
    }),
    [page, pageSize, columnOrder, visibleColumns, sortColumn, sortDirection, tableFilters],
  );

  const { data: assetsData, isLoading: assetsLoading } = useAssets(
    sourceName ? tableParams : undefined,
  );
  const { data: fieldsData } = useExplorerFields();
  const fieldMap = useMemo(() => buildFieldMap(fieldsData?.entries), [fieldsData]);

  const visibleColumnDefs = useMemo(() => {
    return columnOrder
      .filter((c) => visibleColumns[c] !== false)
      .map((key) => {
        const f = fieldMap[key];
        return {
          key,
          name: f?.display_name ?? key,
          dataType: f?.data_type ?? 'string',
          sortable: true,
        };
      });
  }, [columnOrder, visibleColumns, fieldMap]);

  const assets = assetsData?.assets ?? [];
  const totalAssets = assetsData?.pagination?.total ?? 0;
  const totalPages = assetsData?.pagination?.pages ?? 0;
  const allFields = fieldsData?.entries ?? [];

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
    setColumnFilters((prev) => { const n = { ...prev }; delete n[field]; return n; });
  }, []);

  const handleColumnSelectorApply = useCallback((cols: Record<string, boolean>) => {
    // Keep the current ordering for columns the user kept visible, then
    // append any newly enabled columns at the end. Without this, columns
    // added via the selector never show up because the table only renders
    // keys present in `columnOrder`.
    const newOrder = [
      ...columnOrder.filter((k) => cols[k]),
      ...Object.keys(cols).filter((k) => cols[k] && !columnOrder.includes(k)),
    ];
    setVisibleColumns(cols);
    setColumnOrder(normalizeColumnOrder(newOrder));
    setIsColumnSelectorOpen(false);
  }, [columnOrder]);

  const handleColumnReorder = useCallback((order: string[]) => {
    setColumnOrder(normalizeColumnOrder(order));
  }, []);

  // ─── Loading / not found ────────────────────────────────────────────────
  if (dsLoading) {
    return <div className={styles.centered}><LoadingSpinner /></div>;
  }
  if (!source) {
    return (
      <div className={styles.centered}>
        <div className={styles.notFound}>
          <span className={styles.notFoundText}>Data source not found</span>
          <a href="/" className={styles.notFoundLink}>Back to Dashboard</a>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.topGrid}>
        <div className={styles.sourceCard}>
          <div className={styles.logoNameGroup}>
            {logoSrc ? (
              <img
                src={logoSrc}
                alt={sourceName}
                className={styles.sourceLogo}
              />
            ) : (
              <div className={styles.sourceLogoFallback}>
                {sourceName.charAt(0).toUpperCase()}
              </div>
            )}
            <span className={styles.sourceName}>{sourceName}</span>
          </div>
          <div className={styles.countGroup}>
            <span className={styles.countValue}>{assetCount.toLocaleString()}</span>
            <span className={styles.countLabel}>assets</span>
          </div>

          <div className={styles.infoRow}>
            <InfoField
              label="Health status"
              value={status.toUpperCase()}
              valueClass={statusClass(status)}
            />
            <InfoField
              label="Last sync"
              value={syncText}
              valueClass={styles[syncStatus]}
            />
          </div>

          <div className={styles.categoryDist}>
            <CategoryBars data={categoryDist} />
          </div>
          <div className={styles.sourceGauge}>
            <ConfidenceDonutGauge
              high={conf?.high ?? 0}
              medium={conf?.medium ?? 0}
              low={conf?.low ?? 0}
              avg={avgConfidence}
              size={128}
            />
          </div>
        </div>

        <div className={styles.butterflyCard}>
          <div className={styles.butterflyHeader}>
            <div className={styles.butterflyTitleGroup}>
              <span className={styles.cardTitle}>Added &amp; removed assets</span>
              <span className={styles.statAdded}>
                <svg className={styles.statArrow} viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <line x1="12" y1="19" x2="12" y2="5" />
                  <polyline points="5 12 12 5 19 12" />
                </svg>
                {totalAdded.toLocaleString()} added
              </span>
              <span className={styles.statReturned}>
                <svg className={styles.statArrow} viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <polyline points="1 4 1 10 7 10" />
                  <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                </svg>
                {totalReturned.toLocaleString()} returned
              </span>
              <span className={styles.statRemoved}>
                <svg className={styles.statArrow} viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <polyline points="19 12 12 19 5 12" />
                </svg>
                {totalRemoved.toLocaleString()} removed
              </span>
            </div>
            <div className={styles.inlineTimePicker}>
              {(['7d', '30d', '90d'] as const).map((opt, idx, arr) => (
                <span key={opt} className={styles.inlineTimePickerSlot}>
                  <button
                    type="button"
                    className={`${styles.inlineTimePickerOpt} ${timeRange === opt ? styles.inlineTimePickerOptActive : ''}`}
                    onClick={() => setTimeRange(opt)}
                  >
                    {opt}
                  </button>
                  {idx < arr.length - 1 && (
                    <span className={styles.inlineTimePickerSep} aria-hidden>|</span>
                  )}
                </span>
              ))}
            </div>
          </div>
          <div className={styles.butterflyBody}>
            {timelineData && timelineData.length > 0 ? (
              <ButterflyChart data={timelineData} returnedLabel="Returned" />
            ) : (
              <div className={styles.chartEmpty}>No timeline data available</div>
            )}
          </div>
        </div>

      </div>

      {/* ── Asset table (toolbar + table, expands with row count) ── */}
      <div className={styles.tableSection}>
        <div className={styles.toolbar}>
          <div className={styles.toolbarLeft}>
            <button
              className={`${styles.iconBtn} ${pivotMode ? styles.iconBtnActive : ''}`}
              onClick={() => setPivotMode((m) => !m)}
              title="Pivot mode"
            >
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <line x1="3" y1="9" x2="21" y2="9" />
                <line x1="9" y1="3" x2="9" y2="21" />
              </svg>
            </button>
            <span className={styles.resultCount}>
              {totalAssets.toLocaleString()} assets
            </span>
            {pivotMode && (
              <PivotControls
                config={pivotConfig}
                onConfigChange={(cfg) => setPivotConfig((prev) => ({ ...prev, ...cfg }))}
                fields={allFields}
              />
            )}
          </div>
          <div className={styles.toolbarRight}>
            <div className={styles.pageSizeGroup}>
              {([25, 50, 100] as const).map((size) => (
                <button
                  key={size}
                  className={`${styles.pageSizeBtn} ${pageSize === size ? styles.pageSizeBtnActive : ''}`}
                  onClick={() => { setPageSize(size); setPage(1); }}
                >
                  {size}
                </button>
              ))}
            </div>
            <div className={styles.iconBtnWrap}>
              <button
                ref={columnsBtnRef}
                className={styles.iconBtn}
                title="Columns"
                onClick={() => setIsColumnSelectorOpen((o) => !o)}
              >
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="12" y1="3" x2="12" y2="21" />
                  <line x1="5" y1="3" x2="5" y2="21" />
                  <line x1="19" y1="3" x2="19" y2="21" />
                </svg>
              </button>
              {fieldsData && (
                <ColumnSelector
                  isOpen={isColumnSelectorOpen}
                  onClose={() => setIsColumnSelectorOpen(false)}
                  fields={fieldsData}
                  visibleColumns={visibleColumns}
                  onApply={handleColumnSelectorApply}
                  triggerRef={columnsBtnRef}
                />
              )}
            </div>
            <div className={styles.iconBtnWrap}>
              <button
                className={styles.iconBtn}
                title="Export"
                onClick={() => setIsExportMenuOpen((o) => !o)}
              >
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
              </button>
              <ExportMenu
                isOpen={isExportMenuOpen}
                onClose={() => setIsExportMenuOpen(false)}
                totalFiltered={totalAssets}
                pivotMode={pivotMode}
                visibleColumns={visibleColumnDefs}
                filters={tableFilters}
                sortBy={sortColumn}
                sortDir={sortDirection}
                pivotConfig={pivotConfig}
              />
            </div>
          </div>
        </div>

        {/* Table — no internal scroll; the page extends with row count */}
        {pivotMode ? (
          <PivotTable
            rowField={pivotConfig.rowDimension}
            colField={pivotConfig.columnsEnabled && pivotConfig.columnDimension
              ? pivotConfig.columnDimension
              : undefined}
            filters={tableFilters}
            displayMode={pivotConfig.displayMode}
          />
        ) : (
          <div className={styles.tableWrap}>
            <ExplorerTable
              assets={assets}
              columns={visibleColumnDefs}
              sortColumn={sortColumn}
              sortDirection={sortDirection}
              onSort={handleSort}
              onEditAsset={() => {}}
              isLoading={assetsLoading}
              columnOrder={columnOrder.filter((c) => visibleColumns[c] !== false)}
              onColumnReorder={handleColumnReorder}
              columnWidths={columnWidths}
              onColumnResize={handleColumnResize}
              columnFilters={columnFilters}
              dateFilters={dateFilters}
              onColumnFilter={handleColumnFilter}
              onClearColumnFilter={handleClearColumnFilter}
            />
          </div>
        )}

        {!pivotMode && totalPages > 1 && (
          <div className={styles.pagination}>
            <button className={styles.pageBtn} disabled={page <= 1} onClick={() => setPage(1)}>First</button>
            <button className={styles.pageBtn} disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
            <span className={styles.pageNum}>{page} / {totalPages}</span>
            <button className={styles.pageBtn} disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>Next</button>
            <button className={styles.pageBtn} disabled={page >= totalPages} onClick={() => setPage(totalPages)}>Last</button>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Info field — uppercase label prefix + colored value (+ optional grey detail
// on a second line). Used for the right-side stack of the source card.
// ---------------------------------------------------------------------------

interface InfoFieldProps {
  label: string;
  value: string | number;
  valueClass?: string;
  detail?: string;
}

function InfoField({ label, value, valueClass, detail }: InfoFieldProps) {
  return (
    <div className={styles.infoField}>
      <div className={styles.infoFieldHead}>
        <span className={styles.infoFieldLabel}>{label}</span>
        <span className={`${styles.infoFieldValue} ${valueClass ?? ''}`}>{value}</span>
      </div>
      {detail && <span className={styles.infoFieldDetail}>{detail}</span>}
    </div>
  );
}
