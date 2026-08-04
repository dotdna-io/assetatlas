/**
 * DataTable - Interactive asset table for reporting dashboards
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 *
 * Self-contained chart component: constructor(containerId, options),
 * setData/resize/destroy interface. Multiple instances can coexist.
 */

class DotDNADataTable {

    static OPERATORS = {
        eq:        { symbol: '=',      text: 'equals',         needsValue: true },
        neq:       { symbol: '!=',     text: 'not equals',     needsValue: true },
        gt:        { symbol: '>',      text: 'greater than',   needsValue: true },
        gte:       { symbol: '>=',     text: 'greater or equal', needsValue: true },
        lt:        { symbol: '<',      text: 'less than',      needsValue: true },
        lte:       { symbol: '<=',     text: 'less or equal',  needsValue: true },
        contains:  { symbol: '~',      text: 'contains',       needsValue: true },
        ncontains: { symbol: '!~',     text: 'not contains',   needsValue: true },
        starts:    { symbol: '^',      text: 'starts with',    needsValue: true },
        ends:      { symbol: '$',      text: 'ends with',      needsValue: true },
        any:       { symbol: 'IN',     text: 'any of',         needsValue: true },
        empty:     { symbol: 'EMPTY',  text: 'is empty',       needsValue: false },
        notempty:  { symbol: '!EMPTY', text: 'is not empty',   needsValue: false }
    };

    static DEFAULT_COLUMNS = [
        'hostname', 'ip', 'category', 'subcategory',
        'os_normalized', 'mac', 'data_source', 'last_seen', 'status'
    ];

    constructor(containerId, options = {}) {
        // The vanilla DataTable historically reached for a global `apiClient`
        // exposed by the React app. That global has been removed (see
        // ui/prod/src/api/client.ts) — any caller that needs DataTable must
        // pass an `apiClient` adapter on `options` exposing at least `get`.
        const injected = options.apiClient || (typeof window !== 'undefined' && window.apiClient);
        if (!injected || typeof injected.get !== 'function') {
            throw new Error(
                'DotDNADataTable requires an apiClient adapter (with .get) on options. ' +
                'The previous window.apiClient global has been removed.'
            );
        }
        this.apiClient = injected;
        this.container = document.getElementById(containerId);
        if (!this.container) throw new Error(`Container "${containerId}" not found`);
        this.containerId = containerId;
        this._prefix = `datatable_${containerId}_`;

        // If columns are explicitly configured, skip localStorage overrides
        this._hasExplicitColumns = Array.isArray(options.columns) && options.columns.length > 0;

        // When the caller drives the table via setData() (e.g. monitoring tiles),
        // skip the built-in /api/assets and /api/assets/fields fetches that would
        // otherwise fire on construction and 400 against unrelated endpoints.
        this._noAutoload = !!options.noAutoload;

        // Instance state
        this.state = {
            assets: [],
            totalAssets: 0,
            totalPages: 0,
            currentPage: 1,
            itemsPerPage: options.itemsPerPage || 10,
            currentSort: { column: 'last_seen', direction: 'desc' },
            columnOrder: this._hasExplicitColumns ? [...options.columns] : [...DotDNADataTable.DEFAULT_COLUMNS],
            visibleColumns: {},
            columnNames: {},
            fieldMetadata: {},
            availableFields: { standard: [], advanced: [], extended: {} },
            allFieldsFlat: [],
            isLoading: false,
            searchVisible: false,
            searchChips: [],
            chipIdCounter: 0,
            pivotMode: false,
            pivotConfig: {
                rowDimension: 'category',
                columnDimension: '',
                columnsEnabled: false,
                displayMode: 'count'
            },
            columnWidths: {}
        };

        // Default visible columns
        this.state.columnOrder.forEach(c => { this.state.visibleColumns[c] = true; });

        // DOM refs
        this.dom = {};

        // Chip build state machine: null | { step, field, operator }
        this._chipBuild = null;
        this._highlightIdx = -1;
        this._didResize = false;

        this._destroyed = false;

        this._init().catch(err => console.error('[DataTable] Init failed:', err));
    }

    // ── Helpers ────────────────────────────────────────────────────

    _esc(str) {
        const d = document.createElement('div');
        d.textContent = str;
        return d.innerHTML;
    }

    _lsGet(key) {
        try { return JSON.parse(localStorage.getItem(this._prefix + key)); }
        catch { return null; }
    }

    _lsSet(key, val) {
        try { localStorage.setItem(this._prefix + key, JSON.stringify(val)); }
        catch { /* quota */ }
    }

    _formatRelativeTime(dateString) {
        if (!dateString) return '-';
        const d = new Date(dateString);
        if (isNaN(d.getTime())) return String(dateString);
        const yr = d.getFullYear();
        const mo = String(d.getMonth() + 1).padStart(2, '0');
        const dy = String(d.getDate()).padStart(2, '0');
        return `${yr}-${mo}-${dy}`;
    }

    // ── Init ──────────────────────────────────────────────────────

    async _init() {
        this._loadPreferences();
        this._renderShell();
        this._bindEvents();
        if (this._noAutoload) return;
        await this._loadFields();
        await this._loadAssets(1);
    }

    _loadPreferences() {
        // Skip column overrides if columns were explicitly configured
        if (!this._hasExplicitColumns) {
            const savedVisible = this._lsGet('visibleColumns');
            if (savedVisible && typeof savedVisible === 'object') {
                this.state.visibleColumns = savedVisible;
            }
            const savedOrder = this._lsGet('columnOrder');
            if (Array.isArray(savedOrder) && savedOrder.length) {
                this.state.columnOrder = savedOrder;
            }
        }
        const savedWidths = this._lsGet('columnWidths');
        if (savedWidths && typeof savedWidths === 'object') {
            this.state.columnWidths = savedWidths;
        }
    }

    _savePreferences() {
        this._lsSet('visibleColumns', this.state.visibleColumns);
        this._lsSet('columnOrder', this.state.columnOrder);
        this._lsSet('columnWidths', this.state.columnWidths);
    }

    // ── Shell HTML ────────────────────────────────────────────────

    _renderShell() {
        this.container.innerHTML = '';
        this.container.className = 'dotdna-data-table';

        this.container.innerHTML = `
            <div class="ddt-toolbar">
                <button class="ddt-btn ddt-search-toggle" title="Search">
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                    </svg>
                </button>
                <span class="ddt-results-count"></span>
                <div class="ddt-pivot-controls" style="display:none">
                    <select class="ddt-pivot-row"></select>
                    <button class="ddt-btn ddt-col-toggle" title="Toggle column dimension">Col</button>
                    <select class="ddt-pivot-col" style="display:none"></select>
                    <select class="ddt-display-mode">
                        <option value="count">Count</option>
                        <option value="percent_total">% of Total</option>
                    </select>
                </div>
                <span style="flex:1"></span>
                <button class="ddt-btn ddt-pivot-toggle" title="Pivot view">
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="3" x2="9" y2="21"/>
                    </svg>
                </button>
                <div class="ddt-page-size-selector">
                    <button class="ddt-page-size-btn" data-size="5">5</button>
                    <button class="ddt-page-size-btn active" data-size="10">10</button>
                    <button class="ddt-page-size-btn" data-size="20">20</button>
                </div>
                <button class="ddt-btn ddt-columns-btn" title="Select columns">Columns</button>
            </div>
            <div class="ddt-search-bar" style="display:none">
                <div class="ddt-chips"></div>
                <input class="ddt-search-input" type="text" placeholder="Search assets..." autocomplete="off" />
                <div class="ddt-suggestions" style="display:none"></div>
            </div>
            <div class="ddt-table-container">
                <table class="ddt-table"><thead><tr></tr></thead><tbody></tbody></table>
            </div>
            <div class="ddt-empty" style="display:none">No assets found</div>
            <div class="ddt-pagination">
                <button class="ddt-btn ddt-prev" disabled>Prev</button>
                <div class="ddt-page-numbers"></div>
                <button class="ddt-btn ddt-next" disabled>Next</button>
            </div>
            <div class="ddt-column-selector" style="display:none"></div>
        `;

        // Cache DOM refs
        const q = (sel) => this.container.querySelector(sel);
        this.dom.searchToggle = q('.ddt-search-toggle');
        this.dom.pivotToggle = q('.ddt-pivot-toggle');
        this.dom.resultsCount = q('.ddt-results-count');
        this.dom.pivotControls = q('.ddt-pivot-controls');
        this.dom.pivotRow = q('.ddt-pivot-row');
        this.dom.colToggle = q('.ddt-col-toggle');
        this.dom.pivotCol = q('.ddt-pivot-col');
        this.dom.displayMode = q('.ddt-display-mode');
        this.dom.columnsBtn = q('.ddt-columns-btn');
        this.dom.searchBar = q('.ddt-search-bar');
        this.dom.chips = q('.ddt-chips');
        this.dom.searchInput = q('.ddt-search-input');
        this.dom.suggestions = q('.ddt-suggestions');
        this.dom.thead = q('.ddt-table thead tr');
        this.dom.tbody = q('.ddt-table tbody');
        this.dom.empty = q('.ddt-empty');
        this.dom.pagination = q('.ddt-pagination');
        this.dom.prevBtn = q('.ddt-prev');
        this.dom.nextBtn = q('.ddt-next');
        this.dom.pageNumbers = q('.ddt-page-numbers');
        this.dom.columnSelector = q('.ddt-column-selector');
        this.dom.tableContainer = q('.ddt-table-container');
    }

    // ── Event Binding ─────────────────────────────────────────────

    _bindEvents() {
        this.dom.searchToggle.addEventListener('click', () => this._toggleSearch());
        this.dom.pivotToggle.addEventListener('click', () => this._togglePivot());
        this.dom.columnsBtn.addEventListener('click', (e) => { e.stopPropagation(); this._openColumnSelector(); });
        this.dom.prevBtn.addEventListener('click', () => {
            if (this.state.currentPage > 1) this._loadAssets(this.state.currentPage - 1);
        });
        this.dom.nextBtn.addEventListener('click', () => {
            if (this.state.currentPage < this.state.totalPages) this._loadAssets(this.state.currentPage + 1);
        });
        this.dom.searchInput.addEventListener('input', () => this._onSearchInput());
        this.dom.searchInput.addEventListener('keydown', (e) => this._onSearchKeydown(e));

        // Pivot controls
        this.dom.colToggle.addEventListener('click', () => this._togglePivotColumns());
        this.dom.pivotRow.addEventListener('change', () => {
            this.state.pivotConfig.rowDimension = this.dom.pivotRow.value;
            if (this.state.pivotMode) this._renderPivot();
        });
        this.dom.pivotCol.addEventListener('change', () => {
            this.state.pivotConfig.columnDimension = this.dom.pivotCol.value;
            if (this.state.pivotMode) this._renderPivot();
        });
        this.dom.displayMode.addEventListener('change', () => {
            this.state.pivotConfig.displayMode = this.dom.displayMode.value;
            if (this.state.pivotMode) this._renderPivot();
        });

        // Page size selector
        this.container.querySelectorAll('.ddt-page-size-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this.container.querySelectorAll('.ddt-page-size-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.state.itemsPerPage = parseInt(btn.dataset.size, 10);
                this._loadAssets(1);
            });
        });

        // Array expand toggle
        this.dom.tbody.addEventListener('click', (e) => {
            const expandBtn = e.target.closest('.ddt-array-expand-btn');
            if (expandBtn) {
                e.stopPropagation();
                this._toggleArrayExpand(expandBtn);
            }
        });

        // Close suggestions on outside click
        this._outsideClick = (e) => {
            if (!this.dom.suggestions.contains(e.target) && e.target !== this.dom.searchInput) {
                this.dom.suggestions.style.display = 'none';
            }
            if (this.dom.columnSelector.style.display !== 'none' &&
                !this.dom.columnSelector.contains(e.target) &&
                e.target !== this.dom.columnsBtn) {
                this.dom.columnSelector.style.display = 'none';
            }
        };
        document.addEventListener('click', this._outsideClick);
    }

    // ── Data Loading ──────────────────────────────────────────────

    async _loadFields() {
        try {
            const response = await this.apiClient.get('/assets/fields');
            const data = response.data || response;
            this.state.availableFields = {
                standard: data.standard || [],
                advanced: data.advanced || [],
                extended: data.extended || {}
            };

            this.state.allFieldsFlat = [];
            this.state.columnNames = {};
            this.state.fieldMetadata = {};

            (data.standard || []).forEach(f => {
                this.state.allFieldsFlat.push(f);
                this.state.columnNames[f.key] = f.name;
                this.state.fieldMetadata[f.key] = f;
            });
            (data.advanced || []).forEach(f => {
                this.state.allFieldsFlat.push(f);
                this.state.columnNames[f.key] = f.name;
                this.state.fieldMetadata[f.key] = f;
            });
            Object.entries(data.extended || {}).forEach(([source, fields]) => {
                fields.forEach(f => {
                    this.state.allFieldsFlat.push({ ...f, source });
                    this.state.columnNames[f.key] = f.name;
                    this.state.fieldMetadata[f.key] = { ...f, source };
                });
            });

            this._populatePivotSelectors();

            // Prune stale localStorage columns that no longer exist
            const validKeys = new Set(this.state.allFieldsFlat.map(f => f.key));
            this.state.columnOrder = this.state.columnOrder.filter(k => validKeys.has(k));
            Object.keys(this.state.visibleColumns).forEach(k => {
                if (!validKeys.has(k)) delete this.state.visibleColumns[k];
            });
        } catch (err) {
            console.error('[DataTable] Failed to load fields:', err);
        }
    }

    async _loadAssets(page) {
        if (this._destroyed) return;
        if (this.state.isLoading) return;
        this.state.isLoading = true;
        this.state.currentPage = page || 1;

        try {
            const visibleKeys = this.state.columnOrder.filter(c => this.state.visibleColumns[c]);
            const fields = visibleKeys.join(',');
            const filters = this._buildServerFilters();

            let url = `/assets?page=${this.state.currentPage}&limit=${this.state.itemsPerPage}`;
            url += `&fields=${encodeURIComponent(fields)}`;
            url += `&sort_by=${encodeURIComponent(this.state.currentSort.column)}`;
            url += `&sort_dir=${this.state.currentSort.direction}`;
            if (filters.length > 0) {
                url += `&filters=${encodeURIComponent(JSON.stringify(filters))}`;
            }

            const data = await this.apiClient.get(url);
            this.state.assets = data.assets || [];
            this.state.totalAssets = data.pagination?.total || 0;
            this.state.totalPages = data.pagination?.pages || 0;

            if (!this._destroyed) {
                this._renderTable();
                this._updatePagination();
            }
        } catch (err) {
            console.error('[DataTable] Failed to load assets:', err);
            this.state.assets = [];
            this.state.totalAssets = 0;
            this._renderTable();
            this._updatePagination();
        } finally {
            this.state.isLoading = false;
        }
    }

    _buildServerFilters() {
        return this.state.searchChips.map(chip => {
            const f = { field: chip.field, op: chip.operator };
            if (chip.value !== undefined && chip.value !== null) f.value = chip.value;
            return f;
        });
    }

    // ── Table Rendering ───────────────────────────────────────────

    _renderTable() {
        const visibleCols = this.state.columnOrder.filter(c => this.state.visibleColumns[c]);

        // Headers
        this.dom.thead.innerHTML = visibleCols.map(col => {
            const name = this.state.columnNames[col] || col;
            const isSorted = this.state.currentSort.column === col;
            const icon = isSorted
                ? (this.state.currentSort.direction === 'asc' ? '\u2191' : '\u2193')
                : '\u2195';
            const opacity = isSorted ? '1' : '0.4';
            const w = this.state.columnWidths[col];
            const style = w ? ` style="width:${w}px"` : '';
            return `<th class="ddt-sortable" data-col="${this._esc(col)}" draggable="true"${style}>
                ${this._esc(name)} <span class="ddt-sort-icon" style="opacity:${opacity}">${icon}</span>
                <span class="ddt-resize-handle"></span>
            </th>`;
        }).join('');

        // Apply fixed layout if any widths are set
        const table = this.dom.thead.closest('.ddt-table');
        if (Object.keys(this.state.columnWidths).length > 0) {
            table.style.tableLayout = 'fixed';
        } else {
            table.style.tableLayout = '';
        }

        // Sort click handlers (ignore if resize drag occurred)
        this.dom.thead.querySelectorAll('.ddt-sortable').forEach(th => {
            th.addEventListener('click', (e) => {
                if (this._didResize) { this._didResize = false; return; }
                if (e.target.closest('.ddt-resize-handle')) return;
                const col = th.dataset.col;
                if (this.state.currentSort.column === col) {
                    this.state.currentSort.direction = this.state.currentSort.direction === 'asc' ? 'desc' : 'asc';
                } else {
                    this.state.currentSort.column = col;
                    this.state.currentSort.direction = 'asc';
                }
                this._loadAssets(1);
            });
        });

        // Resize handles
        this._bindResizeHandles();

        // Column drag & drop reorder
        this._setupDragDrop();

        // Body
        if (this.state.assets.length === 0) {
            this.dom.tbody.innerHTML = '';
            this.dom.empty.style.display = 'flex';
            return;
        }

        this.dom.empty.style.display = 'none';
        this.dom.tbody.innerHTML = this.state.assets.map(asset => {
            const cells = visibleCols.map(col => this._renderCell(asset, col)).join('');
            return `<tr data-id="${this._esc(String(asset.id))}">${cells}</tr>`;
        }).join('');

        this.dom.resultsCount.textContent = `${this.state.totalAssets.toLocaleString()} asset${this.state.totalAssets !== 1 ? 's' : ''}`;
    }

    _renderCell(asset, colKey) {
        const value = asset[colKey];
        const field = this.state.fieldMetadata[colKey] || {};
        const dataType = field.dataType || 'string';

        if (value === null || value === undefined) {
            return `<td>-</td>`;
        }

        // Data source badges
        if (colKey === 'data_source' && Array.isArray(value) && typeof getDataSourceBadgesHTML === 'function') {
            return `<td>${getDataSourceBadgesHTML(value, { maxVisible: 3, maxLength: 14 })}</td>`;
        }

        // String arrays — show first 3, expandable overflow
        if (dataType === 'string_array' && Array.isArray(value)) {
            if (value.length === 0) return `<td>-</td>`;
            const maxVisible = 3;
            const visible = value.slice(0, maxVisible);
            const hidden = value.slice(maxVisible);
            let html = '<td><div class="ddt-array-values">';
            html += visible.map(v => `<div class="ddt-array-value">${this._esc(v)}</div>`).join('');
            if (hidden.length > 0) {
                html += `<div class="ddt-array-overflow" style="display:none;">`;
                html += hidden.map(v => `<div class="ddt-array-value">${this._esc(v)}</div>`).join('');
                html += `</div>`;
                html += `<button class="ddt-array-expand-btn">+${hidden.length} more</button>`;
            }
            html += '</div></td>';
            return html;
        }

        // Timestamps
        if (dataType === 'timestamp') {
            const formatted = this._formatRelativeTime(value);

            if (colKey === 'last_seen') {
                const diffDays = Math.floor((Date.now() - new Date(value).getTime()) / 86400000);
                let ageClass = 'recent';
                if (diffDays > 30) ageClass = 'stale';
                else if (diffDays > 7) ageClass = 'aging';
                return `<td><span class="last-seen-badge last-seen-${ageClass}">${this._esc(formatted)}</span></td>`;
            }
            return `<td>${this._esc(formatted)}</td>`;
        }

        // Booleans
        if (dataType === 'boolean') {
            return `<td>${value ? 'Yes' : 'No'}</td>`;
        }

        // Floats
        if (dataType === 'float' || dataType === 'numeric') {
            const num = parseFloat(value);
            if (!isNaN(num)) {
                return `<td>${num % 1 === 0 ? num.toString() : num.toFixed(2)}</td>`;
            }
        }

        // Category badge
        if (colKey === 'category' && value) {
            const cls = String(value).toLowerCase().replace(/[^a-z0-9-]/g, '-');
            return `<td><span class="category-badge category-${cls}">${this._esc(String(value))}</span></td>`;
        }

        return `<td>${this._esc(String(value))}</td>`;
    }

    // ── Array Expand ──────────────────────────────────────────────

    _toggleArrayExpand(btn) {
        const container = btn.closest('.ddt-array-values');
        const overflow = container.querySelector('.ddt-array-overflow');
        if (overflow.style.display === 'none') {
            overflow.style.display = 'block';
            btn.textContent = 'Show less';
            btn.classList.add('expanded');
        } else {
            overflow.style.display = 'none';
            const count = overflow.querySelectorAll('.ddt-array-value').length;
            btn.textContent = `+${count} more`;
            btn.classList.remove('expanded');
        }
    }

    // ── Column Drag & Drop Reorder ───────────────────────────────

    _setupDragDrop() {
        this.dom.thead.querySelectorAll('th[draggable="true"]').forEach(th => {
            th.addEventListener('dragstart', (e) => this._onDragStart(e, th));
            th.addEventListener('dragover', (e) => this._onDragOver(e));
            th.addEventListener('drop', (e) => this._onDrop(e, th));
            th.addEventListener('dragend', () => this._onDragEnd(th));
            th.addEventListener('dragenter', () => th.classList.add('ddt-drag-over'));
            th.addEventListener('dragleave', () => th.classList.remove('ddt-drag-over'));
        });
    }

    _onDragStart(e, th) {
        // Don't drag when clicking interactive children (sort icon, resize handle)
        if (e.target.closest('.ddt-sort-icon') || e.target.closest('.ddt-resize-handle')) {
            e.preventDefault();
            return false;
        }
        this._draggedCol = th;
        th.classList.add('ddt-dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/html', th.innerHTML);
        th.style.opacity = '0.4';
    }

    _onDragOver(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
    }

    _onDrop(e, th) {
        e.stopPropagation();
        if (!this._draggedCol || this._draggedCol === th) {
            th.classList.remove('ddt-drag-over');
            return;
        }

        const fromCol = this._draggedCol.dataset.col;
        const toCol = th.dataset.col;
        if (!fromCol || !toCol) return;

        const order = this.state.columnOrder;
        const fromIdx = order.indexOf(fromCol);
        const toIdx = order.indexOf(toCol);
        if (fromIdx === -1 || toIdx === -1) return;

        order.splice(fromIdx, 1);
        const insertIdx = fromIdx < toIdx ? toIdx - 1 : toIdx;
        order.splice(insertIdx, 0, fromCol);

        this._lsSet('columnOrder', order);
        th.classList.remove('ddt-drag-over');
        this._renderTable();
    }

    _onDragEnd(th) {
        th.style.opacity = '1';
        th.classList.remove('ddt-dragging');
        this.dom.thead.querySelectorAll('th').forEach(h => h.classList.remove('ddt-drag-over'));
        this._draggedCol = null;
    }

    // ── Pagination ────────────────────────────────────────────────

    _updatePagination() {
        const total = this.state.totalPages;

        if (total <= 1) {
            this.dom.pagination.style.display = 'none';
            return;
        }

        this.dom.pagination.style.display = 'flex';
        this.dom.prevBtn.disabled = this.state.currentPage === 1;
        this.dom.nextBtn.disabled = this.state.currentPage === total;

        // Build page numbers with ellipsis
        const pages = [1];
        for (let i = Math.max(2, this.state.currentPage - 1); i <= Math.min(total - 1, this.state.currentPage + 1); i++) {
            pages.push(i);
        }
        if (total > 1) pages.push(total);
        const unique = [...new Set(pages)].sort((a, b) => a - b);

        this.dom.pageNumbers.innerHTML = unique.map((p, idx) => {
            const prev = unique[idx - 1];
            const gap = prev && p - prev > 1 ? '<span class="ddt-ellipsis">...</span>' : '';
            return `${gap}<button class="ddt-page-num ${p === this.state.currentPage ? 'active' : ''}" data-page="${p}">${p}</button>`;
        }).join('');

        this.dom.pageNumbers.querySelectorAll('.ddt-page-num').forEach(btn => {
            btn.addEventListener('click', () => {
                const pg = parseInt(btn.dataset.page);
                if (pg !== this.state.currentPage) this._loadAssets(pg);
            });
        });
    }

    // ── Smart Search (Chip-based) ─────────────────────────────────

    _toggleSearch() {
        this.state.searchVisible = !this.state.searchVisible;
        this.dom.searchBar.style.display = this.state.searchVisible ? 'flex' : 'none';
        this.dom.searchToggle.classList.toggle('active', this.state.searchVisible);
        if (this.state.searchVisible) this.dom.searchInput.focus();
    }

    _onSearchInput() {
        // Protect inline prefix from being deleted
        const prefix = this._getInlinePrefix();
        if (prefix && !this.dom.searchInput.value.startsWith(prefix)) {
            this.dom.searchInput.value = prefix;
            this._setCursorToEnd();
        }

        const raw = this.dom.searchInput.value;
        const val = prefix ? raw.slice(prefix.length).trim() : raw.trim();
        this._highlightIdx = -1;

        if (!this._chipBuild) {
            // Stage: select field
            this._showFieldSuggestions(val);
        } else if (this._chipBuild.step === 'operator') {
            this._showOperatorSuggestions(val);
        } else if (this._chipBuild.step === 'value') {
            this._showValueSuggestions(this._chipBuild.field, val);
        }
    }

    _setCursorToEnd() {
        const len = this.dom.searchInput.value.length;
        this.dom.searchInput.setSelectionRange(len, len);
    }

    _onSearchKeydown(e) {
        const items = this.dom.suggestions.querySelectorAll('.ddt-suggestion-item');
        const suggestionsVisible = this.dom.suggestions.style.display !== 'none' && items.length > 0;

        if (e.key === 'Escape') {
            if (this._chipBuild) {
                this._chipBuild = null;
                this._highlightIdx = -1;
                this.dom.searchInput.value = '';
                this.dom.searchInput.placeholder = 'Search assets...';
            } else {
                this._toggleSearch();
            }
            this.dom.suggestions.style.display = 'none';
            return;
        }

        // Tab / Arrow navigation through suggestions
        if ((e.key === 'Tab' || e.key === 'ArrowDown' || e.key === 'ArrowUp') && suggestionsVisible) {
            e.preventDefault();
            const dir = e.key === 'ArrowUp' ? -1 : 1;
            this._highlightIdx = ((this._highlightIdx + dir) + items.length) % items.length;
            this._updateHighlight(items);
            return;
        }

        // Tab with no suggestions open — no-op (don't leave input)
        if (e.key === 'Tab') {
            e.preventDefault();
            return;
        }

        if (e.key === 'Backspace') {
            const raw = this.dom.searchInput.value;
            const prefix = this._getInlinePrefix();

            if (this._chipBuild && prefix && raw.length <= prefix.length) {
                // Backspacing into the prefix — step back
                e.preventDefault();
                if (this._chipBuild.step === 'value') {
                    // Go back to operator selection
                    this._chipBuild.step = 'operator';
                    delete this._chipBuild.operator;
                    this._highlightIdx = -1;
                    const newPrefix = this._getInlinePrefix();
                    this.dom.searchInput.value = newPrefix;
                    this._setCursorToEnd();
                    this.dom.suggestions.style.display = 'none';
                    this._showOperatorSuggestions('');
                } else {
                    // Go back to field selection — cancel chip build
                    this._chipBuild = null;
                    this._highlightIdx = -1;
                    this.dom.searchInput.value = '';
                    this.dom.searchInput.placeholder = 'Search assets...';
                    this.dom.suggestions.style.display = 'none';
                    this._showFieldSuggestions('');
                }
                return;
            }

            if (!this._chipBuild && raw === '') {
                if (this.state.searchChips.length > 0) {
                    this.state.searchChips.pop();
                    this._renderChips();
                    this._loadAssets(1);
                }
                this.dom.suggestions.style.display = 'none';
                return;
            }
        }

        // ")" finalizes any-of chip
        if (e.key === ')' && this._chipBuild && this._chipBuild.operator === 'any' && this._chipBuild.step === 'value') {
            e.preventDefault();
            this._finalizeAnyOf();
            return;
        }

        if (e.key === 'Enter') {
            e.preventDefault();

            // If a suggestion is highlighted, select it
            if (suggestionsVisible && this._highlightIdx >= 0 && this._highlightIdx < items.length) {
                items[this._highlightIdx].click();
                return;
            }

            if (this._chipBuild && this._chipBuild.step === 'value') {
                const rawVal = this._extractValueFromInput();

                if (this._chipBuild.operator === 'any') {
                    // Enter on any-of: if there's typed text, append it as a value and stay open
                    // If no new text, finalize
                    if (rawVal) {
                        const parts = rawVal.split(',').map(v => v.trim()).filter(Boolean);
                        if (parts.length > 0) {
                            // Check if the last part looks like a partial entry (no comma after it)
                            // Finalize the whole thing
                            this._finalizeAnyOf();
                        }
                    }
                } else {
                    if (rawVal) {
                        this._addChip(this._chipBuild.field, this._chipBuild.operator, rawVal);
                    }
                }
                return;
            }

            // Try to select first suggestion if visible
            if (suggestionsVisible) {
                items[0].click();
            }
            return;
        }
    }

    _updateHighlight(items) {
        items.forEach((item, i) => {
            item.classList.toggle('highlighted', i === this._highlightIdx);
        });
        if (this._highlightIdx >= 0 && items[this._highlightIdx]) {
            items[this._highlightIdx].scrollIntoView({ block: 'nearest' });
        }
    }

    _getInlinePrefix() {
        if (!this._chipBuild) return '';
        const fieldName = this.state.columnNames[this._chipBuild.field] || this._chipBuild.field;
        if (this._chipBuild.step === 'operator') {
            return fieldName + ' ';
        }
        if (this._chipBuild.step === 'value') {
            const op = DotDNADataTable.OPERATORS[this._chipBuild.operator] || {};
            const base = fieldName + ' ' + (op.text || this._chipBuild.operator) + ' ';
            return this._chipBuild.operator === 'any' ? base + '(' : base;
        }
        return '';
    }

    _extractValueFromInput() {
        const prefix = this._getInlinePrefix();
        const raw = this.dom.searchInput.value;
        if (raw.startsWith(prefix)) return raw.slice(prefix.length).trim();
        return raw.trim();
    }

    _finalizeAnyOf() {
        const rawVal = this._extractValueFromInput();
        const values = rawVal ? rawVal.split(',').map(v => v.trim()).filter(Boolean) : [];
        if (values.length > 0) {
            this._addChip(this._chipBuild.field, this._chipBuild.operator, values);
        }
    }

    _showFieldSuggestions(partial) {
        const fields = this.state.allFieldsFlat.filter(f => {
            if (!partial) return true;
            const p = partial.toLowerCase();
            return f.key.toLowerCase().includes(p) || f.name.toLowerCase().includes(p);
        }).slice(0, 15);

        if (fields.length === 0) {
            this.dom.suggestions.style.display = 'none';
            return;
        }

        this._fieldSuggestions = fields;

        this.dom.suggestions.innerHTML = fields.map((f, i) =>
            `<div class="ddt-suggestion-item" data-idx="${i}">${this._esc(f.name)} <span class="ddt-suggestion-hint">${this._esc(f.key)}</span></div>`
        ).join('');
        this.dom.suggestions.style.display = 'block';

        this.dom.suggestions.querySelectorAll('.ddt-suggestion-item').forEach(item => {
            item.addEventListener('click', () => {
                const field = this._fieldSuggestions[parseInt(item.dataset.idx)];
                if (!field) return;
                this._selectField(field);
            });
        });
    }

    _selectField(field) {
        this._chipBuild = { step: 'operator', field: field.key };
        this._highlightIdx = -1;
        const fieldName = this.state.columnNames[field.key] || field.key;
        this.dom.searchInput.value = fieldName + ' ';
        this.dom.searchInput.placeholder = '';
        this._setCursorToEnd();
        this.dom.suggestions.style.display = 'none';
        this._showOperatorSuggestions('');
        this.dom.searchInput.focus();
    }

    _showOperatorSuggestions(partial) {
        let ops = Object.entries(DotDNADataTable.OPERATORS);
        if (partial) {
            const p = partial.toLowerCase();
            ops = ops.filter(([key, op]) =>
                key.includes(p) || op.text.toLowerCase().includes(p) || op.symbol.toLowerCase().includes(p)
            );
        }

        if (ops.length === 0) {
            this.dom.suggestions.style.display = 'none';
            return;
        }

        this.dom.suggestions.innerHTML = ops.map(([key, op]) =>
            `<div class="ddt-suggestion-item" data-op="${key}"><strong>${this._esc(op.symbol)}</strong> ${this._esc(op.text)}</div>`
        ).join('');
        this.dom.suggestions.style.display = 'block';

        this.dom.suggestions.querySelectorAll('.ddt-suggestion-item').forEach(item => {
            item.addEventListener('click', () => {
                this._selectOperator(item.dataset.op);
            });
        });
    }

    _selectOperator(opKey) {
        const op = DotDNADataTable.OPERATORS[opKey];

        if (!op.needsValue) {
            this._addChip(this._chipBuild.field, opKey, null);
            return;
        }

        this._chipBuild.step = 'value';
        this._chipBuild.operator = opKey;
        this._highlightIdx = -1;
        const prefix = this._getInlinePrefix();
        this.dom.searchInput.value = prefix;
        this.dom.searchInput.placeholder = '';
        this._setCursorToEnd();
        this.dom.suggestions.style.display = 'none';
        this._showValueSuggestions(this._chipBuild.field, '');
        this.dom.searchInput.focus();
    }

    async _showValueSuggestions(field, partial) {
        try {
            let url = `/assets/values?field=${encodeURIComponent(field)}&limit=20`;
            if (partial) url += `&search=${encodeURIComponent(partial)}`;
            const filters = this._buildServerFilters();
            if (filters.length > 0) url += `&filters=${encodeURIComponent(JSON.stringify(filters))}`;

            const data = await this.apiClient.get(url);
            if (this._destroyed) return;
            const resultData = data.data || data;
            const values = resultData.values || [];

            if (values.length === 0) {
                this.dom.suggestions.style.display = 'none';
                return;
            }

            this._valueSuggestions = values;

            // "Apply filter" row at top when any-of has values collected
            let doneHtml = '';
            if (this._chipBuild && this._chipBuild.operator === 'any') {
                const current = this._extractValueFromInput();
                const parts = current ? current.split(',').map(s => s.trim()).filter(Boolean) : [];
                if (parts.length > 0) {
                    doneHtml = `<div class="ddt-suggestion-item ddt-any-done">Apply filter <span class="ddt-suggestion-hint">${parts.length} selected</span></div>`;
                }
            }

            this.dom.suggestions.innerHTML = doneHtml + values.map((v, i) =>
                `<div class="ddt-suggestion-item ddt-value-item" data-idx="${i}">${this._esc(String(v.value))} <span class="ddt-suggestion-hint">(${v.count})</span></div>`
            ).join('');
            this.dom.suggestions.style.display = 'block';

            const doneItem = this.dom.suggestions.querySelector('.ddt-any-done');
            if (doneItem) {
                doneItem.addEventListener('click', () => this._finalizeAnyOf());
            }

            this.dom.suggestions.querySelectorAll('.ddt-value-item').forEach(item => {
                item.addEventListener('click', () => {
                    if (!this._chipBuild) return;
                    const suggestion = this._valueSuggestions[parseInt(item.dataset.idx)];
                    if (!suggestion) return;
                    const val = String(suggestion.value);
                    if (this._chipBuild.operator === 'any') {
                        // Append to comma-separated list
                        const prefix = this._getInlinePrefix();
                        const current = this._extractValueFromInput();
                        const parts = current ? current.split(',').map(s => s.trim()).filter(Boolean) : [];
                        parts.push(val);
                        this.dom.searchInput.value = prefix + parts.join(', ');
                        this._setCursorToEnd();
                        this.dom.searchInput.focus();
                        // Re-render suggestions to update the done row count
                        this._showValueSuggestions(this._chipBuild.field, '');
                    } else {
                        this._addChip(this._chipBuild.field, this._chipBuild.operator, val);
                    }
                });
            });
        } catch (err) {
            console.error('[DataTable] Error fetching values:', err);
            this.dom.suggestions.style.display = 'none';
        }
    }

    _addChip(field, operator, value) {
        const chip = {
            id: ++this.state.chipIdCounter,
            field,
            operator,
            value
        };
        this.state.searchChips.push(chip);
        this._chipBuild = null;
        this._highlightIdx = -1;
        this.dom.searchInput.value = '';
        this.dom.searchInput.placeholder = 'Search assets...';
        this.dom.suggestions.style.display = 'none';
        this._renderChips();
        this._loadAssets(1);
        this.dom.searchInput.focus();
    }

    _renderChips() {
        this.dom.chips.innerHTML = this.state.searchChips.map(chip => {
            const fieldName = this.state.columnNames[chip.field] || chip.field;
            const op = DotDNADataTable.OPERATORS[chip.operator] || {};
            const opText = op.text || chip.operator;
            let valText = '';
            if (chip.value !== null && chip.value !== undefined) {
                if (Array.isArray(chip.value)) {
                    valText = '(' + chip.value.join(', ') + ')';
                } else {
                    valText = String(chip.value);
                }
            }
            const display = valText ? `${fieldName} ${opText} ${valText}` : `${fieldName} ${opText}`;

            return `<span class="ddt-chip" data-chip-id="${chip.id}">
                ${this._esc(display)}
                <button class="ddt-chip-remove" data-chip-id="${chip.id}">&times;</button>
            </span>`;
        }).join('');

        this.dom.chips.querySelectorAll('.ddt-chip-remove').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const id = parseInt(btn.dataset.chipId);
                this.state.searchChips = this.state.searchChips.filter(c => c.id !== id);
                this._renderChips();
                this._loadAssets(1);
            });
        });
    }

    // ── Pivot Mode ────────────────────────────────────────────────

    _togglePivot() {
        this.state.pivotMode = !this.state.pivotMode;
        this.dom.pivotToggle.classList.toggle('active', this.state.pivotMode);
        this.dom.pivotControls.style.display = this.state.pivotMode ? 'flex' : 'none';

        if (this.state.pivotMode) {
            this.dom.pagination.style.display = 'none';
            this._renderPivot();
        } else {
            this._renderTable();
            this._updatePagination();
        }
    }

    _togglePivotColumns() {
        const pc = this.state.pivotConfig;
        pc.columnsEnabled = !pc.columnsEnabled;
        this.dom.colToggle.classList.toggle('active', pc.columnsEnabled);
        this.dom.pivotCol.style.display = pc.columnsEnabled ? '' : 'none';

        if (pc.columnsEnabled) {
            pc.columnDimension = this.dom.pivotCol.value;
        } else {
            pc.columnDimension = '';
        }

        if (this.state.pivotMode) this._renderPivot();
    }

    _populatePivotSelectors() {
        const excludeKeys = ['id', 'hostname', 'ip', 'mac', 'serial_number', 'description', 'fqdn'];
        const pivotable = this.state.allFieldsFlat.filter(f => {
            const dt = f.dataType || 'string';
            if (dt === 'string_array' || dt === 'timestamp') return false;
            if (excludeKeys.includes(f.key)) return false;
            return true;
        });

        const optionsHtml = pivotable.map(f =>
            `<option value="${this._esc(f.key)}">${this._esc(f.name)}</option>`
        ).join('');

        this.dom.pivotRow.innerHTML = optionsHtml;
        this.dom.pivotCol.innerHTML = optionsHtml;

        // Set defaults
        const catOption = pivotable.find(f => f.key === 'category');
        if (catOption) this.dom.pivotRow.value = catOption.key;
        const osOption = pivotable.find(f => f.key === 'os_normalized');
        if (osOption) this.dom.pivotCol.value = osOption.key;
    }

    async _renderPivot() {
        const pc = this.state.pivotConfig;
        pc.rowDimension = this.dom.pivotRow.value;

        try {
            let url = `/assets/aggregate?row_field=${encodeURIComponent(pc.rowDimension)}`;
            if (pc.columnsEnabled && pc.columnDimension) {
                url += `&col_field=${encodeURIComponent(pc.columnDimension)}`;
            }
            const filters = this._buildServerFilters();
            if (filters.length > 0) {
                url += `&filters=${encodeURIComponent(JSON.stringify(filters))}`;
            }

            const response = await this.apiClient.get(url);
            const rd = response.data || response;
            const pivot = rd.data || {};
            const rows = rd.rows || [];
            const columns = rd.columns || [];
            const grandTotal = rd.grand_total || 0;
            const displayMode = pc.displayMode;

            const isRowOnly = !pc.columnsEnabled || !pc.columnDimension;

            if (isRowOnly) {
                this._renderRowOnlyPivot(pivot, rows, grandTotal, displayMode);
            } else {
                this._renderFullPivot(pivot, rows, columns, grandTotal, displayMode);
            }
        } catch (err) {
            console.error('[DataTable] Pivot error:', err);
            this.dom.tbody.innerHTML = '<tr><td style="padding:20px;text-align:center">Failed to load pivot data</td></tr>';
        }
    }

    _renderRowOnlyPivot(pivot, rows, grandTotal, displayMode) {
        const rowName = this.state.columnNames[this.state.pivotConfig.rowDimension] || this.state.pivotConfig.rowDimension;

        const getCount = (val) => typeof val === 'number' ? val : (val?.count || Object.values(val || {}).reduce((a, b) => a + b, 0));

        const sorted = [...rows].sort((a, b) => getCount(pivot[b]) - getCount(pivot[a]));

        let headerHtml = `<th>${this._esc(rowName)}</th><th>Count</th>`;
        if (displayMode !== 'count') headerHtml += '<th>%</th>';
        this.dom.thead.innerHTML = headerHtml;

        let bodyHtml = sorted.map(row => {
            const count = getCount(pivot[row]);
            const pct = grandTotal > 0 ? ((count / grandTotal) * 100).toFixed(1) + '%' : '0%';
            let html = `<tr><td><strong>${this._esc(row)}</strong></td><td>${count}</td>`;
            if (displayMode !== 'count') html += `<td>${pct}</td>`;
            html += '</tr>';
            return html;
        }).join('');

        // Total row
        bodyHtml += `<tr class="ddt-total-row"><td><strong>Total</strong></td><td><strong>${grandTotal}</strong></td>`;
        if (displayMode !== 'count') bodyHtml += '<td><strong>100%</strong></td>';
        bodyHtml += '</tr>';

        this.dom.tbody.innerHTML = bodyHtml;
        this.dom.empty.style.display = grandTotal === 0 ? 'flex' : 'none';
        this.dom.pagination.style.display = 'none';
        this.dom.resultsCount.textContent = `${rows.length} group${rows.length !== 1 ? 's' : ''}`;
    }

    _renderFullPivot(pivot, rows, columns, grandTotal, displayMode) {
        const rowName = this.state.columnNames[this.state.pivotConfig.rowDimension] || this.state.pivotConfig.rowDimension;

        // Calculate totals
        const rowTotals = {};
        const colTotals = {};
        let gt = 0;
        rows.forEach(r => {
            rowTotals[r] = 0;
            columns.forEach(c => {
                const v = pivot[r]?.[c] || 0;
                rowTotals[r] += v;
                colTotals[c] = (colTotals[c] || 0) + v;
                gt += v;
            });
        });

        // Header
        this.dom.thead.innerHTML = `<th>${this._esc(rowName)}</th>` +
            columns.map(c => `<th>${this._esc(c)}</th>`).join('') +
            '<th>Total</th>';

        // Body
        let bodyHtml = rows.map(r => {
            let html = `<tr><td><strong>${this._esc(r)}</strong></td>`;
            columns.forEach(c => {
                const count = pivot[r]?.[c] || 0;
                const display = this._pivotValue(count, rowTotals[r], colTotals[c], gt, displayMode);
                html += `<td>${display}</td>`;
            });
            const rtDisplay = displayMode === 'count' ? rowTotals[r] : this._pivotValue(rowTotals[r], rowTotals[r], gt, gt, 'percent_total');
            html += `<td><strong>${rtDisplay}</strong></td></tr>`;
            return html;
        }).join('');

        // Total row
        bodyHtml += '<tr class="ddt-total-row"><td><strong>Total</strong></td>';
        columns.forEach(c => {
            const display = displayMode === 'count' ? (colTotals[c] || 0) : this._pivotValue(colTotals[c] || 0, gt, colTotals[c], gt, 'percent_total');
            bodyHtml += `<td><strong>${display}</strong></td>`;
        });
        const gtDisplay = displayMode === 'count' ? gt : '100%';
        bodyHtml += `<td><strong>${gtDisplay}</strong></td></tr>`;

        this.dom.tbody.innerHTML = bodyHtml;
        this.dom.empty.style.display = gt === 0 ? 'flex' : 'none';
        this.dom.pagination.style.display = 'none';
        this.dom.resultsCount.textContent = `${rows.length} row${rows.length !== 1 ? 's' : ''} x ${columns.length} col${columns.length !== 1 ? 's' : ''}`;
    }

    _pivotValue(count, rowTotal, colTotal, grandTotal, displayMode) {
        if (displayMode === 'count') return count;
        const pct = grandTotal > 0 ? (count / grandTotal) * 100 : 0;
        return pct === 0 ? '0%' : pct.toFixed(1) + '%';
    }

    // ── Column Selector ───────────────────────────────────────────

    _openColumnSelector() {
        const sel = this.dom.columnSelector;

        if (sel.style.display !== 'none') {
            sel.style.display = 'none';
            return;
        }

        // Build a snapshot of current visibility
        const tempVisible = { ...this.state.visibleColumns };

        // Group fields
        const groups = [
            { label: 'Standard Fields', fields: this.state.availableFields.standard, expanded: true },
            { label: 'Advanced Fields', fields: this.state.availableFields.advanced, expanded: false }
        ];
        Object.entries(this.state.availableFields.extended).forEach(([source, fields]) => {
            groups.push({ label: source, fields, expanded: false });
        });

        let html = `<div class="ddt-col-sel-header">
            <strong>Select Columns</strong>
            <input class="ddt-col-sel-search" type="text" placeholder="Filter..." />
        </div>
        <div class="ddt-col-sel-body">`;

        groups.forEach((g, gi) => {
            if (!g.fields || g.fields.length === 0) return;
            html += `<div class="ddt-col-group" data-group="${gi}">
                <div class="ddt-col-group-header ${g.expanded ? 'expanded' : ''}" data-group="${gi}">
                    ${this._esc(g.label)} <span class="ddt-col-group-arrow">${g.expanded ? '\u25BC' : '\u25B6'}</span>
                </div>
                <div class="ddt-col-group-items" style="display:${g.expanded ? 'block' : 'none'}">`;
            g.fields.forEach(f => {
                const checked = tempVisible[f.key] ? 'checked' : '';
                html += `<label class="ddt-col-item"><input type="checkbox" value="${this._esc(f.key)}" ${checked} /> ${this._esc(f.name)}</label>`;
            });
            html += '</div></div>';
        });

        html += `</div>
        <div class="ddt-col-sel-footer">
            <button class="ddt-btn ddt-col-apply">Apply</button>
        </div>`;

        sel.innerHTML = html;
        sel.style.display = 'flex';

        // Collapsible groups
        sel.querySelectorAll('.ddt-col-group-header').forEach(header => {
            header.addEventListener('click', () => {
                const items = header.nextElementSibling;
                const arrow = header.querySelector('.ddt-col-group-arrow');
                const open = items.style.display !== 'none';
                items.style.display = open ? 'none' : 'block';
                arrow.textContent = open ? '\u25B6' : '\u25BC';
                header.classList.toggle('expanded', !open);
            });
        });

        // Search filter
        const searchInput = sel.querySelector('.ddt-col-sel-search');
        searchInput.addEventListener('input', () => {
            const q = searchInput.value.toLowerCase();
            sel.querySelectorAll('.ddt-col-item').forEach(item => {
                const name = item.textContent.toLowerCase();
                item.style.display = name.includes(q) ? '' : 'none';
            });
        });

        // Apply button
        sel.querySelector('.ddt-col-apply').addEventListener('click', () => {
            const newVisible = {};
            const newOrder = [];
            sel.querySelectorAll('.ddt-col-item input:checked').forEach(cb => {
                newVisible[cb.value] = true;
                newOrder.push(cb.value);
            });

            // Keep existing order for columns that remain visible
            const orderedResult = this.state.columnOrder.filter(c => newVisible[c]);
            newOrder.forEach(c => {
                if (!orderedResult.includes(c)) orderedResult.push(c);
            });

            this.state.visibleColumns = newVisible;
            this.state.columnOrder = orderedResult;
            this._savePreferences();
            sel.style.display = 'none';

            if (this.state.pivotMode) {
                this._renderPivot();
            } else {
                this._loadAssets(1);
            }
        });
    }

    // ── Column Resize ────────────────────────────────────────────

    _bindResizeHandles() {
        this.dom.thead.querySelectorAll('.ddt-resize-handle').forEach(handle => {
            handle.addEventListener('mousedown', (e) => {
                e.preventDefault();
                e.stopPropagation();

                const th = handle.parentElement;
                const col = th.dataset.col;
                const startX = e.clientX;
                const startW = th.offsetWidth;

                const onMove = (ev) => {
                    const delta = ev.clientX - startX;
                    const newW = Math.max(50, startW + delta);
                    th.style.width = newW + 'px';
                    this.state.columnWidths[col] = newW;
                    // Ensure fixed layout is active
                    const table = th.closest('.ddt-table');
                    if (table) table.style.tableLayout = 'fixed';
                };

                const onUp = () => {
                    document.removeEventListener('mousemove', onMove);
                    document.removeEventListener('mouseup', onUp);
                    this._didResize = true;
                    this._savePreferences();
                    // Reset flag after a tick so the click handler can check it
                    setTimeout(() => { this._didResize = false; }, 0);
                };

                document.addEventListener('mousemove', onMove);
                document.addEventListener('mouseup', onUp);
            });
        });
    }

    // ── DotDNA Interface ──────────────────────────────────────────

    setData(/* data */) {
        // DataTable loads its own data — no-op
    }

    resize(gridSize) {
        this.gridSize = gridSize;
        // Table auto-adjusts via CSS — no explicit resize needed
    }

    destroy() {
        this._destroyed = true;
        if (this._outsideClick) {
            document.removeEventListener('click', this._outsideClick);
        }
        this.container.innerHTML = '';
    }
}

// Export to window for wizard access
if (typeof window !== 'undefined') {
    window.DotDNADataTable = DotDNADataTable;
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNADataTable;
}
