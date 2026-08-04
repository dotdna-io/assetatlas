// SPDX-License-Identifier: AGPL-3.0-or-later
import { useDocumentTitle } from '@/hooks';
import { useAuth } from '@/auth';
import { useReducer, useEffect, useRef, useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAssets, useExplorerFields, useSearchSynonyms } from '@/api/assets';
import type { AssetListParams, AssetFilter } from '@/api/assets';
import type { FieldCatalogEntry } from '@/types/field-id';
import { coerceFieldId } from '@/types/field-id';
import { buildFieldMap, findFieldEntry, isDateField, confidenceBandToFilter } from './explorer/explorer-utils';
import {
  explorerReducer,
  initialExplorerState,
  loadColumnConfig,
  saveColumnConfig,
} from './explorer/explorer-state';
import type { ParseResult } from './explorer/explorer-query-parser';
import { parseSearchQuery } from './explorer/explorer-query-parser';
import { buildFilterToken, appendFilterToken } from './explorer/explorer-query-builder';
import { ExplorerTable } from './explorer/table/ExplorerTable';
import { ColumnSelector } from './explorer/table/ColumnSelector';
import { SearchBar } from './explorer/search/SearchBar';
import { PivotControls } from './explorer/pivot/PivotControls';
import { PivotTable } from './explorer/pivot/PivotTable';
import { ExportMenu } from './explorer/ExportMenu';
import { BulkActionsMenu } from './explorer/bulk/BulkActionsMenu';
import { EditAssetModal } from './explorer/edit/EditAssetModal';
import { SavedFiltersMenu } from './explorer/saved-filters/SavedFiltersMenu';
import { SaveFilterModal } from './explorer/saved-filters/SaveFilterModal';
import { serializeView, buildApplyAction, layoutDiffers, buildShareUrl, needsShareFlip } from './explorer/saved-filters/serialize';
import {
  savedFiltersApi,
  useSavedFilters,
  useCreateSavedFilter,
  useUpdateSavedFilter,
  useDeleteSavedFilter,
} from '@/api/saved-filters';
import type { SavedFilter, SavedFilterVisibility } from '@/api/saved-filters';
import { getApiErrorMessage } from '@/api/client';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import styles from './ExplorerPage.module.css';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build combined filters from structured + column filters for the API.
 *
 * Date filters (the "last N days" column dropdowns) are intentionally not
 * emitted — the backend has no `within_days` operator. Values still live in
 * UI state so the control stays responsive.
 */
function buildFilters(
  structured: { field: string; op: string; value: string | string[] | null }[],
  columnFilters: Record<string, string[]>,
  fieldMap: Record<string, FieldCatalogEntry>,
): AssetFilter[] | null {
  const out: AssetFilter[] = [];

  for (const f of structured) {
    if (!f.field || !f.op) continue;
    if (f.value === null || f.value === undefined) {
      if (f.op !== 'empty' && f.op !== 'notempty') continue;
    }
    if (typeof f.value === 'string' && f.value.trim() === '' && f.op !== 'empty' && f.op !== 'notempty') continue;

    const entry = findFieldEntry(f.field, fieldMap);
    if (!entry) continue;
    out.push({ field_id: entry.field_id, op: f.op, value: f.value });
  }

  for (const [encodedKey, values] of Object.entries(columnFilters)) {
    const fieldId = fieldMap[encodedKey]?.field_id ?? coerceFieldId(encodedKey);
    if (!fieldId) continue;
    if (values.length === 1) {
      out.push({ field_id: fieldId, op: 'eq', value: values[0] });
    } else if (values.length > 1) {
      out.push({ field_id: fieldId, op: 'any', value: values });
    }
  }

  return out.length > 0 ? out : null;
}

/** Build pagination window: first, last, and window around current. */
function buildPageNumbers(current: number, total: number): (number | null)[] {
  if (total <= 1) return total === 1 ? [1] : [];

  const pages: (number | null)[] = [];
  const windowSize = 1; // show current +/- 1

  const rangeStart = Math.max(2, current - windowSize);
  const rangeEnd = Math.min(total - 1, current + windowSize);

  pages.push(1);

  if (rangeStart > 2) pages.push(null); // ellipsis

  for (let i = rangeStart; i <= rangeEnd; i++) {
    pages.push(i);
  }

  if (rangeEnd < total - 1) pages.push(null); // ellipsis

  if (total > 1) pages.push(total);

  return pages;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function ExplorerPage() {
  useDocumentTitle('Explorer');
  const [searchParams, setSearchParams] = useSearchParams();

  // Merge URL params into initial state
  const urlInitialState = useMemo(() => {
    const urlPage = Number(searchParams.get('page')) || 1;
    const urlLimit = Number(searchParams.get('limit')) || initialExplorerState.itemsPerPage;
    const urlSort = searchParams.get('sort') || initialExplorerState.sortColumn;
    const urlDir = (searchParams.get('dir') || initialExplorerState.sortDirection) as 'asc' | 'desc';
    const urlSearch = searchParams.get('search') || '';
    const urlFiltersRaw = searchParams.get('filters');
    let urlColumnFilters: Record<string, string[]> = {};
    if (urlFiltersRaw) {
      try {
        const parsed = JSON.parse(urlFiltersRaw);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          urlColumnFilters = parsed;
        }
      } catch {
        // ignore malformed filters param
      }
    }
    const validLimit = ([25, 50, 100] as const).includes(urlLimit as 25 | 50 | 100)
      ? (urlLimit as 25 | 50 | 100)
      : initialExplorerState.itemsPerPage;

    const parsedSearch = urlSearch ? parseSearchQuery(urlSearch) : { filters: [], keyword: '' };

    return {
      ...initialExplorerState,
      currentPage: urlPage,
      itemsPerPage: validLimit,
      sortColumn: urlSort,
      sortDirection: urlDir,
      searchQuery: urlSearch,
      keywordSearch: parsedSearch.keyword,
      structuredFilters: parsedSearch.filters,
      columnFilters: urlColumnFilters,
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // run once on mount only

  const [state, dispatch] = useReducer(explorerReducer, urlInitialState);
  const [isColumnSelectorOpen, setIsColumnSelectorOpen] = useState(false);
  const columnsBtnRef = useRef<HTMLButtonElement>(null);

  const [isSavedMenuOpen, setIsSavedMenuOpen] = useState(false);
  const savedBtnRef = useRef<HTMLButtonElement>(null);
  const [savedStatus, setSavedStatus] = useInlineStatus();
  // Save dialog: { mode, target? } where target is the filter being edited.
  const [saveDialog, setSaveDialog] = useState<{ mode: 'create' | 'edit'; target?: SavedFilter } | null>(null);
  // Pending layout-apply confirmation when a loaded filter changes the layout.
  const [pendingLayout, setPendingLayout] = useState<SavedFilter | null>(null);

  const { isAdmin } = useAuth();
  const savedFiltersQuery = useSavedFilters();
  const myFilters = savedFiltersQuery.data?.mine ?? [];

  // Copy a saved filter's deep link to the clipboard; fall back to a prompt so
  // the URL stays selectable when the clipboard API is blocked (HTTP context).
  const copyShareLink = useCallback(async (id: string) => {
    const url = buildShareUrl(id, window.location.origin, window.location.pathname);
    try {
      await navigator.clipboard.writeText(url);
      setSavedStatus({ kind: 'success', message: 'Link copied' });
    } catch {
      window.prompt('Copy this share link:', url);
    }
  }, [setSavedStatus]);

  const createSaved = useCreateSavedFilter({
    onSuccess: () => { setSavedStatus({ kind: 'success', message: 'Filter saved' }); setSaveDialog(null); },
    onError: (e) => setSavedStatus({ kind: 'error', message: getApiErrorMessage(e, 'Could not save filter') }),
  });
  const updateSaved = useUpdateSavedFilter({
    onSuccess: () => { setSavedStatus({ kind: 'success', message: 'Filter updated' }); setSaveDialog(null); },
    onError: (e) => setSavedStatus({ kind: 'error', message: getApiErrorMessage(e, 'Could not update filter') }),
  });
  // Granting link access is a background write (a dedicated mutation keeps it off
  // the edit dialog's update path — no dialog close, no "Filter updated" toast).
  // The link is NOT copied here: the clipboard write must run inside the click
  // gesture (see handleShareSaved), or browsers reject it for lost activation.
  const shareSaved = useUpdateSavedFilter({
    onError: (e) => setSavedStatus({ kind: 'error', message: getApiErrorMessage(e, 'Could not share filter') }),
  });
  const deleteSaved = useDeleteSavedFilter({
    onSuccess: () => setSavedStatus(null), // the filter leaves the saved list
    onError: (e) => setSavedStatus({ kind: 'error', message: getApiErrorMessage(e, 'Could not delete filter') }),
  });

  const applySavedFilter = useCallback((f: SavedFilter, includeLayout: boolean) => {
    // The reducer writes the applied layout into state; the existing
    // column-config effect persists it to localStorage, so no manual save here.
    dispatch(buildApplyAction(f.payload, includeLayout));
    setIsSavedMenuOpen(false);
  }, []);

  const handleLoadSaved = useCallback((f: SavedFilter) => {
    setIsSavedMenuOpen(false);
    if (layoutDiffers(state, f.payload)) {
      setPendingLayout(f);          // ask before overriding columns/pivot
    } else {
      applySavedFilter(f, false);
    }
  }, [state, applySavedFilter]);

  const handleDeleteSaved = useCallback((f: SavedFilter) => {
    if (window.confirm(`Delete saved filter "${f.name}"?`)) deleteSaved.mutate(f.id);
  }, [deleteSaved]);

  const handleShareSaved = useCallback((f: SavedFilter) => {
    // Copy now, inside the click gesture, so the clipboard write keeps its user
    // activation (an async flip-then-copy is rejected by Safari/Firefox). The id
    // already resolves the link; for a private filter, grant link access in the
    // background — the PUT lands well before anyone opens the link.
    void copyShareLink(f.id);
    if (needsShareFlip(f.visibility)) {
      shareSaved.mutate({ id: f.id, body: { visibility: 'link' } });
    }
  }, [shareSaved, copyShareLink]);

  const handleSubmitSaveDialog = useCallback((v: { name: string; description: string; visibility: SavedFilterVisibility }) => {
    if (saveDialog?.mode === 'edit' && saveDialog.target) {
      updateSaved.mutate({ id: saveDialog.target.id, body: { name: v.name, description: v.description, visibility: v.visibility } });
    } else {
      createSaved.mutate({ name: v.name, description: v.description, visibility: v.visibility, payload: serializeView(state) });
    }
  }, [saveDialog, state, createSaved, updateSaved]);

  // Sync state -> URL (replace history to avoid polluting back stack)
  useEffect(() => {
    const params: Record<string, string> = {};
    if (state.currentPage !== 1) params.page = String(state.currentPage);
    if (state.itemsPerPage !== initialExplorerState.itemsPerPage) params.limit = String(state.itemsPerPage);
    if (state.sortColumn !== initialExplorerState.sortColumn) params.sort = state.sortColumn;
    if (state.sortDirection !== initialExplorerState.sortDirection) params.dir = state.sortDirection;
    if (state.searchQuery) params.search = state.searchQuery;
    const activeFilters = Object.entries(state.columnFilters).filter(([, v]) => v.length > 0);
    if (activeFilters.length > 0) params.filters = JSON.stringify(Object.fromEntries(activeFilters));
    // This effect rebuilds the query string from scratch, so anything it does
    // not re-add is dropped on mount — which is exactly how ?view= disappears
    // above.
    setSearchParams(params, { replace: true });
  }, [state.currentPage, state.itemsPerPage, state.sortColumn, state.sortDirection, state.searchQuery, state.columnFilters, setSearchParams]);

  // Handle sessionStorage drill-down from dashboard
  useEffect(() => {
    const initialSearch = sessionStorage.getItem('explorer_initial_search');
    if (initialSearch) {
      sessionStorage.removeItem('explorer_initial_search');
      const parsed = parseSearchQuery(initialSearch);
      dispatch({ type: 'SET_SEARCH', searchQuery: initialSearch, keywordSearch: parsed.keyword, structuredFilters: parsed.filters });
    }
  }, []);

  // Deep-link recall: ?view=<id> opens a shared saved filter. Runs once — the
  // ref guards against re-entry when the state→URL effect later drops `view`.
  const deepLinkHandledRef = useRef(false);
  useEffect(() => {
    if (deepLinkHandledRef.current) return;
    const viewId = searchParams.get('view');
    if (!viewId) return;
    deepLinkHandledRef.current = true;
    (async () => {
      try {
        const f = await savedFiltersApi.get(viewId);
        // A shared link implies the recipient wants the author's exact view,
        // so apply layout too (no confirm — they opted in by opening the link).
        dispatch(buildApplyAction(f.payload, true));
        setSavedStatus({ kind: 'success', message: `Loaded “${f.name}”` });
      } catch (e) {
        setSavedStatus({ kind: 'error', message: getApiErrorMessage(e, 'That shared filter is unavailable') });
      }
    })();
  }, [searchParams, setSavedStatus]);

  // Handle ?filter_category= (homepage category strip) and ?posture= (posture
  // chevron drill-down) params. The structured filter's `field` is the
  // display-name token that buildFilters() resolves against the live catalog
  // at request time. The posture tier filters mirror the chevron's funnel
  // exactly (secured wins, so monitored/discovered exclude higher tiers) so
  // the explorer count matches the number that was clicked.
  useEffect(() => {
    const filterCategory = searchParams.get('filter_category');
    const posture = searchParams.get('posture');
    const confidence = searchParams.get('confidence');
    // Links shipped before the Alert field existed are still in bookmarks and
    // open tabs. Translating rather than keeping the old code path costs one
    // token, and only because the field accepts the id spelling too. The URL
    // sync effect rebuilds the query string from scratch and no longer re-adds
    // alert_id/alert_label, so both drop off on the first render.
    const legacyAlertId = searchParams.get('alert_id');
    if (!filterCategory && !posture && !confidence && !legacyAlertId) return;

    const filters: { field: string; op: string; value: string }[] = [];
    const parts: string[] = [];
    if (filterCategory) {
      filters.push({ field: 'Category', op: 'eq', value: filterCategory });
      parts.push(`Category = ${filterCategory}`);
    }
    if (posture === 'secured') {
      filters.push({ field: 'Secured', op: 'eq', value: 'true' });
      parts.push('Secured = true');
    } else if (posture === 'monitored') {
      filters.push({ field: 'Monitored', op: 'eq', value: 'true' });
      filters.push({ field: 'Secured', op: 'neq', value: 'true' });
      parts.push('Monitored = true', 'Secured != true');
    } else if (posture === 'discovered') {
      filters.push({ field: 'Secured', op: 'neq', value: 'true' });
      filters.push({ field: 'Monitored', op: 'neq', value: 'true' });
      parts.push('Secured != true', 'Monitored != true');
    }
    // Confidence-band drill-down from the confidence chevrons. Filters by the
    // confidence_level virtual field, which honors the configurable threshold
    // setting end-to-end — so the Explorer set always matches the clicked
    // chevron without any hardcoded score boundaries here.
    if (confidence) {
      const bandFilter = confidenceBandToFilter(confidence);
      if (bandFilter) {
        filters.push(bandFilter.filter);
        parts.push(bandFilter.label);
      }
    }
    if (legacyAlertId) {
      filters.push({ field: 'Alert', op: 'eq', value: legacyAlertId });
      parts.push(`Alert = ${legacyAlertId}`);
    }

    dispatch({
      type: 'SET_SEARCH',
      // Conditions are space-separated — AND is the explorer's implicit default
      // and isn't part of the search syntax/suggestions.
      searchQuery: parts.join(' '),
      keywordSearch: '',
      structuredFilters: filters,
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // run once on mount only

  // Load column config from localStorage on mount
  const initializedRef = useRef(false);
  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;
    const config = loadColumnConfig();
    dispatch({ type: 'SET_COLUMN_ORDER', columnOrder: config.columnOrder });
    dispatch({ type: 'SET_VISIBLE_COLUMNS', visibleColumns: config.visibleColumns });
  }, []);

  // Save column config when it changes
  useEffect(() => {
    if (!initializedRef.current) return;
    saveColumnConfig({
      columnOrder: state.columnOrder,
      visibleColumns: state.visibleColumns,
      columnWidths: state.columnWidths,
    });
  }, [state.columnOrder, state.visibleColumns, state.columnWidths]);

  // Fetch field metadata
  // Opt in to filter-only fields: the search bar needs the Alert field. They
  // must NOT reach the column or pivot pickers — projecting or grouping by one
  // runs a correlated subquery per asset row.
  const { data: fieldsData } = useExplorerFields(true);

  // Pickers get the projectable subset. fieldMap below is built from the FULL
  // list on purpose: buildFilters() resolves the `Alert` token against it.
  const pickableFieldsData = useMemo(
    () => (fieldsData ? { ...fieldsData, entries: fieldsData.entries.filter((e) => !e.filter_only) } : undefined),
    [fieldsData],
  );
  const pickableFields = useMemo(
    () => pickableFieldsData?.entries ?? [],
    [pickableFieldsData],
  );

  const fieldMap = useMemo(
    () => buildFieldMap(fieldsData?.entries),
    [fieldsData],
  );

  // FieldCatalogEntry has no `sortable` flag; default to true.
  const visibleColumnDefs = useMemo(() => {
    return state.columnOrder
      .filter((key) => state.visibleColumns[key])
      .map((key) => {
        const meta = fieldMap[key];
        return {
          key,
          name: meta?.display_name ?? key,
          dataType: meta?.data_type ?? 'string',
          sortable: true,
        };
      });
  }, [state.columnOrder, state.visibleColumns, fieldMap]);

  // Resolve the structured + column filters once; both the table query (`params`,
  // JSON-stringified) and the bulk menu (`bulkFilters`, raw array) derive from this.
  const rawFilters = useMemo(
    () => buildFilters(state.structuredFilters, state.columnFilters, fieldMap),
    [state.structuredFilters, state.columnFilters, fieldMap],
  );

  // Build API params
  const params: AssetListParams = useMemo(() => {
    const p: AssetListParams = {
      page: state.currentPage,
      limit: state.itemsPerPage,
      fields: state.columnOrder.filter((c) => state.visibleColumns[c]).join(','),
      sort_by: state.sortColumn,
      sort_dir: state.sortDirection,
    };
    if (state.keywordSearch) p.search = state.keywordSearch;
    if (rawFilters) p.filters = JSON.stringify(rawFilters);
    return p;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    state.currentPage,
    state.itemsPerPage,
    state.columnOrder,
    state.visibleColumns,
    state.sortColumn,
    state.sortDirection,
    state.keywordSearch,
    rawFilters,
  ]);

  // Same resolved filters as a typed array for bulk operations.
  const bulkFilters = useMemo(() => rawFilters ?? [], [rawFilters]);

  // Fetch assets
  const { data: assetsData, isLoading, isFetching, error: assetsError } = useAssets(params);

  // Sync API response into state
  useEffect(() => {
    if (assetsData?.pagination) {
      dispatch({
        type: 'SET_DATA_RESPONSE',
        totalAssets: assetsData.pagination.total,
        totalPages: assetsData.pagination.pages,
      });
    }
  }, [assetsData]);

  const allFields: FieldCatalogEntry[] = fieldsData?.entries ?? [];

  // Synonym lookup for search suggestions
  const { data: synonymData } = useSearchSynonyms();
  const synonymLookup = synonymData ?? null;

  // Search change handler (called debounced from SearchBar)
  const handleSearchChange = useCallback(
    (raw: string, parsed: ParseResult) => {
      dispatch({
        type: 'SET_SEARCH',
        searchQuery: raw,
        keywordSearch: parsed.keyword,
        structuredFilters: parsed.filters,
      });
    },
    [],
  );

  // Add or exclude a clicked cell value in the search as a `Field <op> value`
  // filter (op '=' to add, '!=' to exclude), reusing the same text pipeline as
  // manual search. Dedupe makes re-adding a no-op.
  const handleAddToSearch = useCallback(
    (displayName: string, value: string, op: string) => {
      const token = buildFilterToken(displayName, value, op);
      const newQuery = appendFilterToken(state.searchQuery, token);
      if (newQuery === state.searchQuery.trim()) return;
      const parsed = parseSearchQuery(newQuery, synonymLookup ?? undefined);
      dispatch({
        type: 'SET_SEARCH',
        searchQuery: newQuery,
        keywordSearch: parsed.keyword,
        structuredFilters: parsed.filters,
      });
    },
    [state.searchQuery, synonymLookup],
  );

  // Sort handler
  const handleSort = useCallback(
    (column: string) => {
      const direction =
        state.sortColumn === column && state.sortDirection === 'asc' ? 'desc' : 'asc';
      dispatch({ type: 'SET_SORT', column, direction });
    },
    [state.sortColumn, state.sortDirection],
  );

  // Edit handler
  const handleEditAsset = useCallback((assetId: string) => {
    dispatch({ type: 'SET_EDIT_ASSET', assetId });
  }, []);

  // Page size handler
  const handlePageSize = useCallback((size: 25 | 50 | 100) => {
    dispatch({ type: 'SET_ITEMS_PER_PAGE', itemsPerPage: size });
  }, []);

  // Column selector apply handler
  const handleColumnSelectorApply = useCallback(
    (newVisibleColumns: Record<string, boolean>) => {
      // Keep existing order for currently visible columns; append newly enabled ones at end
      const newOrder = [
        ...state.columnOrder.filter((k) => newVisibleColumns[k]),
        ...Object.keys(newVisibleColumns).filter(
          (k) => newVisibleColumns[k] && !state.columnOrder.includes(k),
        ),
      ];
      dispatch({ type: 'SET_VISIBLE_COLUMNS', visibleColumns: newVisibleColumns });
      dispatch({ type: 'SET_COLUMN_ORDER', columnOrder: newOrder });
    },
    [state.columnOrder],
  );

  // Column reorder handler
  const handleColumnReorder = useCallback((newOrder: string[]) => {
    dispatch({ type: 'SET_COLUMN_ORDER', columnOrder: newOrder });
  }, []);

  // Column resize handler
  const handleColumnResize = useCallback((column: string, width: number) => {
    dispatch({ type: 'SET_COLUMN_WIDTH', field: column, width });
  }, []);

  const handleColumnFilter = useCallback((field: string, values: string[]) => {
    if (isDateField(field, fieldMap[field]?.data_type)) {
      if (values.length > 0) {
        const days = parseInt(values[0], 10);
        if (!isNaN(days) && days > 0) {
          dispatch({ type: 'SET_DATE_FILTER', field, days });
        }
      } else {
        dispatch({ type: 'CLEAR_DATE_FILTER', field });
      }
    } else {
      dispatch({ type: 'SET_COLUMN_FILTER', field, values });
    }
  }, [fieldMap]);

  const handleClearColumnFilter = useCallback((field: string) => {
    if (isDateField(field, fieldMap[field]?.data_type)) {
      dispatch({ type: 'CLEAR_DATE_FILTER', field });
    } else {
      dispatch({ type: 'CLEAR_COLUMN_FILTER', field });
    }
  }, [fieldMap]);

  const [isExportMenuOpen, setIsExportMenuOpen] = useState(false);

  const assets = assetsData?.assets ?? [];
  const pageNumbers = buildPageNumbers(state.currentPage, state.totalPages);

  return (
    <div className={styles.page}>
      {/* Search bar — full width */}
      <SearchBar
        value={state.searchQuery}
        onChange={handleSearchChange}
        fields={allFields}
        synonymLookup={synonymLookup}
      />

      {/* Toolbar */}
      <div className={styles.toolbar}>
        <div className={styles.toolbarLeft}>
          <button
            className={`${styles.iconBtn} ${state.pivotMode ? styles.iconBtnActive : ''}`}
            onClick={() => dispatch({ type: 'TOGGLE_PIVOT' })}
            title="Pivot mode"
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <line x1="3" y1="9" x2="21" y2="9" />
              <line x1="9" y1="3" x2="9" y2="21" />
            </svg>
          </button>
          <span className={styles.resultCount}>
            {state.totalAssets.toLocaleString()} assets
          </span>
          {state.pivotMode && (
            <PivotControls
              config={state.pivotConfig}
              onConfigChange={(cfg) => dispatch({ type: 'SET_PIVOT_CONFIG', config: cfg })}
              fields={pickableFields}
            />
          )}
        </div>
        <div className={styles.toolbarRight}>
          <InlineStatus status={savedStatus} align="inline" />
          <div className={styles.pageSizeGroup}>
            {([25, 50, 100] as const).map((size) => (
              <button
                key={size}
                className={`${styles.pageSizeBtn} ${state.itemsPerPage === size ? styles.pageSizeBtnActive : ''}`}
                onClick={() => handlePageSize(size)}
              >
                {size}
              </button>
            ))}
          </div>
          <div className={styles.iconBtnWrap}>
            <button
              ref={savedBtnRef}
              className={styles.iconBtn}
              title="Saved filters"
              onClick={() => setIsSavedMenuOpen((o) => !o)}
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
              </svg>
            </button>
            <SavedFiltersMenu
              isOpen={isSavedMenuOpen}
              onClose={() => setIsSavedMenuOpen(false)}
              filters={myFilters}
              library={savedFiltersQuery.data?.library ?? []}
              loading={savedFiltersQuery.isLoading}
              error={savedFiltersQuery.isError}
              onSaveCurrent={() => { setSaveDialog({ mode: 'create' }); setIsSavedMenuOpen(false); }}
              onLoad={handleLoadSaved}
              onShare={(f) => { setIsSavedMenuOpen(false); handleShareSaved(f); }}
              onRename={(f) => { setSaveDialog({ mode: 'edit', target: f }); setIsSavedMenuOpen(false); }}
              onDelete={handleDeleteSaved}
              triggerRef={savedBtnRef}
            />
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
            {pickableFieldsData && (
              <ColumnSelector
                isOpen={isColumnSelectorOpen}
                onClose={() => setIsColumnSelectorOpen(false)}
                fields={pickableFieldsData}
                visibleColumns={state.visibleColumns}
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
            {/* All three exports inherit the active filters[] — an Alert scope
                included — same as the table: the row exports via GET /assets,
                Export Pivot via GET /assets/aggregate (#1013). See ExportMenu. */}
            <ExportMenu
              isOpen={isExportMenuOpen}
              onClose={() => setIsExportMenuOpen(false)}
              totalFiltered={state.totalAssets}
              pivotMode={state.pivotMode}
              visibleColumns={visibleColumnDefs}
              filters={params.filters}
              search={params.search}
              sortBy={params.sort_by}
              sortDir={params.sort_dir}
              pivotConfig={state.pivotConfig}
            />
          </div>
        </div>
      </div>

      {/* Table */}
      {state.pivotMode ? (
        <PivotTable
          rowField={state.pivotConfig.rowDimension}
          colField={state.pivotConfig.columnsEnabled && state.pivotConfig.columnDimension
            ? state.pivotConfig.columnDimension
            : undefined}
          filters={params.filters}
          displayMode={state.pivotConfig.displayMode}
        />
      ) : (
        <div className={styles.tableWrap}>
          <ExplorerTable
            assets={assets}
            columns={visibleColumnDefs}
            sortColumn={state.sortColumn}
            sortDirection={state.sortDirection}
            onSort={handleSort}
            onEditAsset={handleEditAsset}
            isLoading={isLoading || isFetching}
            columnOrder={state.columnOrder}
            onColumnReorder={handleColumnReorder}
            columnWidths={state.columnWidths}
            onColumnResize={handleColumnResize}
            columnFilters={state.columnFilters}
            dateFilters={state.dateFilters}
            onColumnFilter={handleColumnFilter}
            onClearColumnFilter={handleClearColumnFilter}
            onAddToSearch={handleAddToSearch}
            headerActions={
              /* Bulk actions live in the header cell above the row kebabs —
                 they act on every matching row. Bulk and export resolve their
                 target set server-side from the same filters[] the table uses
                 (#1011), an Alert scope included, so nothing here needs to
                 disable them for one. */
              <BulkActionsMenu
                filters={bulkFilters}
                search={state.keywordSearch || undefined}
                matchCount={assetsData?.pagination?.total ?? 0}
                disabled={isFetching}
              />
            }
          />
        </div>
      )}

      {/* Pagination — hidden in pivot mode */}
      {!state.pivotMode && state.totalPages > 1 && (
        <div className={styles.pagination}>
          <button
            className={styles.pageBtn}
            disabled={state.currentPage <= 1}
            onClick={() => dispatch({ type: 'SET_PAGE', page: state.currentPage - 1 })}
            title="Previous page"
          >
            &lt;
          </button>
          {pageNumbers.map((num, idx) =>
            num === null ? (
              <span key={`ellipsis-${idx}`} className={styles.pageEllipsis}>...</span>
            ) : (
              <button
                key={num}
                className={`${styles.pageBtn} ${num === state.currentPage ? styles.pageBtnActive : ''}`}
                onClick={() => dispatch({ type: 'SET_PAGE', page: num })}
              >
                {num}
              </button>
            ),
          )}
          <button
            className={styles.pageBtn}
            disabled={state.currentPage >= state.totalPages}
            onClick={() => dispatch({ type: 'SET_PAGE', page: state.currentPage + 1 })}
            title="Next page"
          >
            &gt;
          </button>
        </div>
      )}

      {/* Edit Asset Modal */}
      <EditAssetModal
        assetId={state.editingAssetId}
        initialTab="reclassify"
        onClose={() => dispatch({ type: 'CLEAR_EDIT_ASSET' })}
      />

      {/* Save / edit saved-filter dialog */}
      {saveDialog && (
        <SaveFilterModal
          key={saveDialog.mode === 'edit' ? saveDialog.target?.id : 'create'}
          isOpen
          mode={saveDialog.mode}
          canPublish={isAdmin}
          initialName={saveDialog.target?.name ?? ''}
          initialDescription={saveDialog.target?.description ?? ''}
          initialVisibility={saveDialog.target?.visibility ?? 'private'}
          busy={createSaved.isPending || updateSaved.isPending}
          onSubmit={handleSubmitSaveDialog}
          onCancel={() => setSaveDialog(null)}
        />
      )}

      {/* Confirm before a loaded filter overrides the viewer's column/pivot layout */}
      <Modal
        isOpen={pendingLayout !== null}
        onClose={() => setPendingLayout(null)}
        title="Apply saved layout?"
        size="sm"
      >
        <p>This saved filter includes a different column layout and pivot configuration. Apply it, or keep your current columns?</p>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
          <Button variant="secondary" onClick={() => { if (pendingLayout) applySavedFilter(pendingLayout, false); setPendingLayout(null); }}>
            Filters only
          </Button>
          <Button variant="primary" onClick={() => { if (pendingLayout) applySavedFilter(pendingLayout, true); setPendingLayout(null); }}>
            Apply layout
          </Button>
        </div>
      </Modal>
    </div>
  );
}
