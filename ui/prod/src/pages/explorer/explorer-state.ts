// SPDX-License-Identifier: AGPL-3.0-or-later
// Explorer state management — useReducer-based

import {
  loadAssetTableColumnConfig,
  normalizeColumnOrder,
  normalizeVisibleColumns,
  saveAssetTableColumnConfig,
} from '@/constants/assetTableColumns';

export interface ParsedFilter {
  field: string;
  op: string;
  value: string | string[] | null;
}

export interface ExplorerState {
  // Data
  currentPage: number;
  itemsPerPage: 25 | 50 | 100;
  totalAssets: number;
  totalPages: number;

  // Search
  searchQuery: string;
  keywordSearch: string;
  structuredFilters: ParsedFilter[];

  // All field-keyed maps use encoded FieldId strings (e.g. "static:Hostname").
  columnFilters: Record<string, string[]>;
  dateFilters: Record<string, number>;

  // Sort
  sortColumn: string;
  sortDirection: 'asc' | 'desc';

  // Columns
  columnOrder: string[];
  visibleColumns: Record<string, boolean>;
  columnWidths: Record<string, number>;

  // Pivot
  pivotMode: boolean;
  pivotConfig: {
    rowDimension: string;
    columnDimension: string;
    columnsEnabled: boolean;
    displayMode: 'count' | 'percent_total' | 'percent_row' | 'percent_col';
  };

  // UI
  isLoading: boolean;
  editingAssetId: string | null;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

export type ExplorerAction =
  | { type: 'SET_PAGE'; page: number }
  | { type: 'SET_ITEMS_PER_PAGE'; itemsPerPage: 25 | 50 | 100 }
  | { type: 'SET_SEARCH'; searchQuery: string; keywordSearch: string; structuredFilters: ParsedFilter[] }
  | { type: 'SET_SORT'; column: string; direction: 'asc' | 'desc' }
  | { type: 'SET_COLUMN_FILTER'; field: string; values: string[] }
  | { type: 'CLEAR_COLUMN_FILTER'; field: string }
  | { type: 'CLEAR_ALL_COLUMN_FILTERS' }
  | { type: 'SET_DATE_FILTER'; field: string; days: number }
  | { type: 'CLEAR_DATE_FILTER'; field: string }
  | { type: 'SET_VISIBLE_COLUMNS'; visibleColumns: Record<string, boolean> }
  | { type: 'SET_COLUMN_ORDER'; columnOrder: string[] }
  | { type: 'SET_COLUMN_WIDTH'; field: string; width: number }
  | { type: 'TOGGLE_PIVOT' }
  | { type: 'SET_PIVOT_CONFIG'; config: Partial<ExplorerState['pivotConfig']> }
  | { type: 'SET_LOADING'; isLoading: boolean }
  | { type: 'SET_EDIT_ASSET'; assetId: string }
  | { type: 'CLEAR_EDIT_ASSET' }
  | { type: 'SET_DATA_RESPONSE'; totalAssets: number; totalPages: number }
  | {
      type: 'APPLY_SAVED_FILTER';
      searchQuery: string;
      keywordSearch: string;
      structuredFilters: ParsedFilter[];
      columnFilters: Record<string, string[]>;
      dateFilters: Record<string, number>;
      sortColumn: string;
      sortDirection: 'asc' | 'desc';
      layout: {
        columnOrder: string[];
        visibleColumns: Record<string, boolean>;
        columnWidths: Record<string, number>;
        pivotMode: boolean;
        pivotConfig: ExplorerState['pivotConfig'];
      } | null;
    };

// ---------------------------------------------------------------------------
// Default columns and persistence
// ---------------------------------------------------------------------------
// The default column list itself is shared across all ExplorerTable surfaces
// — see constants/assetTableColumns.ts (#1153).

// v4 keys — bumped when the default column set changed to the shared 16-column
// set with virtual:asset_label pinned first (#1153). v3 data is ignored, so
// existing users get the new defaults on next load. (v3 added Data Source +
// Model, dropped MAC + FQDN; v1/v2 predate that.)
const LS_KEYS = {
  order: 'explorerColumnOrder_v4',
  visible: 'explorerVisibleColumns_v4',
  widths: 'columnWidths_v4',
} as const;

export function loadColumnConfig(): {
  columnOrder: string[];
  visibleColumns: Record<string, boolean>;
  columnWidths: Record<string, number>;
} {
  return loadAssetTableColumnConfig(LS_KEYS);
}

export function saveColumnConfig(state: Pick<ExplorerState, 'columnOrder' | 'visibleColumns' | 'columnWidths'>): void {
  saveAssetTableColumnConfig(LS_KEYS, state);
}

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

function buildInitialState(): ExplorerState {
  const { columnOrder, visibleColumns, columnWidths } = loadColumnConfig();

  return {
    currentPage: 1,
    itemsPerPage: 50,
    totalAssets: 0,
    totalPages: 0,

    searchQuery: '',
    keywordSearch: '',
    structuredFilters: [],

    columnFilters: {},
    dateFilters: {},

    sortColumn: 'static:Confidence%20Score',
    sortDirection: 'desc',

    columnOrder,
    visibleColumns,
    columnWidths,

    pivotMode: false,
    pivotConfig: {
      rowDimension: 'static:Category',
      columnDimension: '',
      columnsEnabled: false,
      displayMode: 'count',
    },

    isLoading: false,
    editingAssetId: null,
  };
}

export const initialExplorerState: ExplorerState = buildInitialState();

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

export function explorerReducer(state: ExplorerState, action: ExplorerAction): ExplorerState {
  switch (action.type) {
    case 'SET_PAGE':
      return { ...state, currentPage: action.page };

    case 'SET_ITEMS_PER_PAGE':
      return { ...state, itemsPerPage: action.itemsPerPage, currentPage: 1 };

    case 'SET_SEARCH':
      return {
        ...state,
        searchQuery: action.searchQuery,
        keywordSearch: action.keywordSearch,
        structuredFilters: action.structuredFilters,
        currentPage: 1,
      };

    case 'SET_SORT':
      return {
        ...state,
        sortColumn: action.column,
        sortDirection: action.direction,
        currentPage: 1,
      };

    case 'SET_COLUMN_FILTER': {
      const columnFilters = { ...state.columnFilters, [action.field]: action.values };
      return { ...state, columnFilters, currentPage: 1 };
    }

    case 'CLEAR_COLUMN_FILTER': {
      const columnFilters = { ...state.columnFilters };
      delete columnFilters[action.field];
      return { ...state, columnFilters, currentPage: 1 };
    }

    case 'CLEAR_ALL_COLUMN_FILTERS':
      return { ...state, columnFilters: {}, dateFilters: {}, currentPage: 1 };

    case 'SET_DATE_FILTER': {
      const dateFilters = { ...state.dateFilters, [action.field]: action.days };
      return { ...state, dateFilters, currentPage: 1 };
    }

    case 'CLEAR_DATE_FILTER': {
      const dateFilters = { ...state.dateFilters };
      delete dateFilters[action.field];
      return { ...state, dateFilters, currentPage: 1 };
    }

    case 'SET_VISIBLE_COLUMNS':
      // The pinned Asset Label column can never be hidden (#1153).
      return { ...state, visibleColumns: normalizeVisibleColumns(action.visibleColumns) };

    case 'SET_COLUMN_ORDER':
      // The pinned Asset Label column always stays first (#1153).
      return { ...state, columnOrder: normalizeColumnOrder(action.columnOrder) };

    case 'SET_COLUMN_WIDTH': {
      const columnWidths = { ...state.columnWidths, [action.field]: action.width };
      return { ...state, columnWidths };
    }

    case 'TOGGLE_PIVOT':
      return { ...state, pivotMode: !state.pivotMode };

    case 'SET_PIVOT_CONFIG':
      return {
        ...state,
        pivotConfig: { ...state.pivotConfig, ...action.config },
      };

    case 'SET_LOADING':
      return { ...state, isLoading: action.isLoading };

    case 'SET_EDIT_ASSET':
      return { ...state, editingAssetId: action.assetId };

    case 'CLEAR_EDIT_ASSET':
      return { ...state, editingAssetId: null };

    case 'SET_DATA_RESPONSE':
      return {
        ...state,
        totalAssets: action.totalAssets,
        totalPages: action.totalPages,
      };

    case 'APPLY_SAVED_FILTER': {
      const next: ExplorerState = {
        ...state,
        searchQuery: action.searchQuery,
        keywordSearch: action.keywordSearch,
        structuredFilters: action.structuredFilters,
        columnFilters: action.columnFilters,
        dateFilters: action.dateFilters,
        sortColumn: action.sortColumn,
        sortDirection: action.sortDirection,
        currentPage: 1,
      };
      if (action.layout) {
        // Saved layouts predate the pinned Asset Label column — force it
        // first and visible here too (#1153).
        next.columnOrder = normalizeColumnOrder(action.layout.columnOrder);
        next.visibleColumns = normalizeVisibleColumns(action.layout.visibleColumns);
        next.columnWidths = action.layout.columnWidths;
        next.pivotMode = action.layout.pivotMode;
        next.pivotConfig = action.layout.pivotConfig;
      }
      return next;
    }

    default:
      return state;
  }
}
