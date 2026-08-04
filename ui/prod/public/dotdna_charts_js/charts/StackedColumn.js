/**
 * StackedColumn - Stacked column chart for distribution visualization
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 *
 * REFACTORED VERSION with proper separation:
 * - config: Static configuration (size, colors, animation settings)
 * - data: Dynamic data that can be updated without recreation
 * - setupDOM(): One-time DOM structure creation
 * - setData(): Update data dynamically without distortion
 * - resize(): Handle size changes
 */

class DotDNAStackedColumn extends DotDNASVGChartBase {
    constructor(containerId, config = {}) {
        super(containerId, config);

        // SEPARATE: Static configuration (doesn't change after init)
        this.config = {
            barWidth: config.barWidth || 60,
            barGap: config.barGap || 16,
            maxBarHeight: config.maxBarHeight || 140,
            animationDuration: config.animationDuration || 1200,
            colors: config.colors || null,  // Resolved dynamically in getColor()
            showLegend: config.showLegend !== false,
            gridSize: config.gridSize || '4x2',  // '3x2' or '4x2'
            compactLegend: config.compactLegend || false  // single-row legend (monitoring)
        };

        // Display options
        this.normalize = config.normalize || false;

        // SEPARATE: Dynamic data (can be updated)
        this.data = null;

        // State tracking
        this.hoveredColumn = null;
        this.animationKey = 0;

        // Create DOM structure once
        this.setupDOM();

        // Only render if initial data provided; otherwise wait for setData()
        if (config.data) {
            this.setData(config.data);
        } else {
            this.showNoData();
        }
    }

    /**
     * Get color pair for a segment index.
     * Uses explicit config.colors if provided, otherwise ChartPalette (wraps around).
     */
    getColor(index) {
        if (this.config.colors && this.config.colors[index]) {
            return this.config.colors[index];
        }
        if (window.ChartPalette) {
            return window.ChartPalette.colors(index);
        }
        // Hardcoded fallback
        const fallback = [
            ['#EF4444', '#DC2626'], ['#F59E0B', '#D97706'], ['#3B82F6', '#2563EB'],
            ['#10B981', '#059669'], ['#8B5CF6', '#7C3AED'], ['#06B6D4', '#0891B2'],
            ['#EC4899', '#DB2777'], ['#6366F1', '#4F46E5']
        ];
        return fallback[index % fallback.length];
    }

    /**
     * GENERATE RANDOM DATA
     * Creates random data for initial display
     */
    generateRandomData() {
        return [
            {
                label: 'Jan',
                segments: [
                    { value: 45, label: 'Critical' },
                    { value: 65, label: 'High' },
                    { value: 85, label: 'Medium' },
                    { value: 55, label: 'Low' },
                    { value: 30, label: 'Info' }
                ]
            },
            {
                label: 'Feb',
                segments: [
                    { value: 38, label: 'Critical' },
                    { value: 72, label: 'High' },
                    { value: 90, label: 'Medium' },
                    { value: 48, label: 'Low' },
                    { value: 25, label: 'Info' }
                ]
            },
            {
                label: 'Mar',
                segments: [
                    { value: 52, label: 'Critical' },
                    { value: 58, label: 'High' },
                    { value: 78, label: 'Medium' },
                    { value: 62, label: 'Low' },
                    { value: 35, label: 'Info' }
                ]
            },
            {
                label: 'Apr',
                segments: [
                    { value: 41, label: 'Critical' },
                    { value: 68, label: 'High' },
                    { value: 88, label: 'Medium' },
                    { value: 51, label: 'Low' },
                    { value: 28, label: 'Info' }
                ]
            },
            {
                label: 'May',
                segments: [
                    { value: 48, label: 'Critical' },
                    { value: 61, label: 'High' },
                    { value: 82, label: 'Medium' },
                    { value: 58, label: 'Low' },
                    { value: 32, label: 'Info' }
                ]
            },
            {
                label: 'Jun',
                segments: [
                    { value: 35, label: 'Critical' },
                    { value: 75, label: 'High' },
                    { value: 92, label: 'Medium' },
                    { value: 45, label: 'Low' },
                    { value: 22, label: 'Info' }
                ]
            }
        ];
    }

    /**
     * ONE-TIME DOM SETUP
     * Creates the wrapper, SVG container, and legend structure
     * This only runs once or when resizing
     */
    setupDOM() {
        // Clear container
        this.container.innerHTML = '';
        this.container.className = 'dotdna-chart';
        this.container.style.width = '100%';
        this.container.style.height = '100%';
        this.container.style.display = 'flex';
        this.container.style.alignItems = 'center';
        this.container.style.justifyContent = 'center';
        this.container.style.position = 'relative';

        // Create main wrapper
        this.wrapper = document.createElement('div');
        this.wrapper.style.width = '100%';
        this.wrapper.style.height = '100%';
        this.wrapper.style.display = 'flex';
        this.wrapper.style.flexDirection = 'column';
        this.wrapper.style.padding = '40px 20px 8px';
        this.wrapper.style.boxSizing = 'border-box';
        this.wrapper.style.position = 'relative';

        // Chart area — flex row of columns aligned to bottom
        this.columnsContainer = document.createElement('div');
        this.columnsContainer.style.flex = '1';
        this.columnsContainer.style.display = 'flex';
        this.columnsContainer.style.flexDirection = 'row';
        this.columnsContainer.style.alignItems = 'flex-end';
        this.columnsContainer.style.justifyContent = 'center';
        this.columnsContainer.style.gap = '8px';
        this.columnsContainer.style.minHeight = '0';
        this.wrapper.appendChild(this.columnsContainer);

        // Create tooltip (themed to match pie/donut)
        const isDark = document.body.classList.contains('dark-mode');
        this.tooltip = document.createElement('div');
        this.tooltip.style.cssText = `
            position: absolute;
            background-color: ${isDark ? '#303034' : '#ffffff'};
            color: ${isDark ? '#f0f0f0' : '#1e293b'};
            border: 1px solid ${isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.08)'};
            padding: 12px 16px;
            border-radius: 8px;
            font-size: 12px;
            line-height: 1.5;
            opacity: 0;
            pointer-events: none;
            z-index: 1000;
            white-space: nowrap;
            transition: opacity 0.2s ease-out;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        `;
        this.wrapper.appendChild(this.tooltip);

        // X-axis labels container
        this.xLabelsContainer = document.createElement('div');
        this.xLabelsContainer.style.display = 'flex';
        this.xLabelsContainer.style.justifyContent = 'center';
        this.xLabelsContainer.style.gap = '8px';
        this.xLabelsContainer.style.marginTop = '8px';
        this.wrapper.appendChild(this.xLabelsContainer);

        // Legend container
        if (this.config.showLegend) {
            this.legendContainer = document.createElement('div');
            this.legendContainer.style.display = 'flex';
            this.legendContainer.style.justifyContent = 'center';
            this.legendContainer.style.gap = '16px';
            this.legendContainer.style.marginTop = '16px';
            this.legendContainer.style.flexWrap = 'wrap';
            // compactLegend (monitoring): keep the legend to a single row so it
            // never starves the flex:1 plot. Don't wrap; scroll horizontally so
            // every series stays reachable. Opt-in so Reporting/chat are unchanged.
            if (this.config.compactLegend) {
                this.legendContainer.style.flexWrap = 'nowrap';
                this.legendContainer.style.justifyContent = 'flex-start';
                this.legendContainer.style.overflowX = 'auto';
                this.legendContainer.style.overflowY = 'hidden';
                this.legendContainer.style.flexShrink = '0';
            }
            this.wrapper.appendChild(this.legendContainer);
        }

        this.container.appendChild(this.wrapper);
    }

    /**
     * SET DATA - Update chart data dynamically
     * @param {Array} data - Chart data in format: [{label, segments: [{value, label}]}]
     */
    setData(data) {
        if (!data || !Array.isArray(data)) {
            console.error('StackedColumn: setData() requires an array');
            return;
        }

        this.data = data;

        // Build a single source of truth for segment-label → color index.
        // Without this, columns with different segment orderings or partial
        // segment sets render the same label in different colours, and the
        // legend (which only walked data[0]) would be missing labels that
        // only appear in later columns.
        this._segmentColorMap = new Map();
        let nextIndex = 0;
        for (const column of this.data) {
            if (!column?.segments) continue;
            for (const segment of column.segments) {
                if (segment?.label != null && !this._segmentColorMap.has(segment.label)) {
                    this._segmentColorMap.set(segment.label, nextIndex++);
                }
            }
        }

        // Update legend
        if (this.config.showLegend) {
            this.updateLegend();
        }

        // Update X-axis labels
        this.updateXLabels();

        // Render columns
        this.renderColumns();
    }

    /**
     * Resolve a segment's colour index from the unified label map, falling
     * back to the supplied positional index when the label is unknown.
     */
    _segmentColorIndex(segment, fallbackIndex) {
        if (segment && this._segmentColorMap?.has(segment.label)) {
            return this._segmentColorMap.get(segment.label);
        }
        return fallbackIndex;
    }

    /**
     * UPDATE LEGEND
     * Recreates legend items based on current data
     */
    updateLegend() {
        if (!this.legendContainer || !this._segmentColorMap || this._segmentColorMap.size === 0) {
            return;
        }

        this.legendContainer.innerHTML = '';

        // Iterate the unified label→index map so the legend always reflects
        // every distinct segment that appears anywhere in the data, with the
        // same colour the columns render with.
        let i = 0;
        this._segmentColorMap.forEach((colorIndex, label) => {
            const legendItem = document.createElement('div');
            legendItem.style.display = 'flex';
            legendItem.style.alignItems = 'center';
            legendItem.style.gap = '8px';
            legendItem.style.opacity = '0';
            // Single-row legend: keep each item at full width so the row scrolls
            // horizontally rather than squeezing items.
            if (this.config.compactLegend) legendItem.style.flexShrink = '0';

            const dot = document.createElement('div');
            dot.style.width = '12px';
            dot.style.height = '12px';
            dot.style.borderRadius = '2px';
            const color = this.getColor(colorIndex);
            dot.style.background = `linear-gradient(135deg, ${color[0]}, ${color[1]})`;
            dot.style.flexShrink = '0';

            const text = document.createElement('span');
            text.style.fontSize = '13px';
            text.style.fontWeight = '500';
            text.style.whiteSpace = 'nowrap';
            text.textContent = label;

            legendItem.appendChild(dot);
            legendItem.appendChild(text);
            this.legendContainer.appendChild(legendItem);

            // Animate legend items
            const idx = i;
            setTimeout(() => {
                legendItem.style.transition = 'opacity 0.3s ease-out';
                legendItem.style.opacity = '1';
            }, 400 + idx * 50);
            i++;
        });
    }

    /**
     * UPDATE X-AXIS LABELS
     * Recreates X-axis labels based on current data
     */
    updateXLabels() {
        if (!this.xLabelsContainer) return;

        this.xLabelsContainer.innerHTML = '';

        this.data.forEach((column, index) => {
            const label = document.createElement('div');
            label.style.flex = '1';
            label.style.fontSize = '11px';
            label.style.fontWeight = '500';
            label.style.color = '#6B7280';
            label.style.textAlign = 'center';
            label.style.opacity = '0';
            label.style.overflow = 'hidden';
            label.style.textOverflow = 'ellipsis';
            label.style.whiteSpace = 'nowrap';
            label.textContent = column.label;
            this.xLabelsContainer.appendChild(label);

            // Animate label
            setTimeout(() => {
                label.style.transition = 'opacity 0.3s ease-out';
                label.style.opacity = '1';
            }, 200 + index * 50);
        });
    }

    /**
     * RENDER COLUMNS
     * Renders all columns with animation
     */
    renderColumns() {
        // Cancel any pending animation timers
        (this._animTimers || []).forEach(id => clearTimeout(id));
        this._animTimers = [];

        this.columnsContainer.innerHTML = '';
        // Hide container until animation begins — prevents flash
        this.columnsContainer.style.visibility = 'hidden';

        // Dense time-series (one column per timestamp — e.g. 240 at 1h/15s) would
        // be invisible under the roomy categorical defaults: the fixed 8px gaps
        // alone exceed the plot width (→ zero-width columns), and the per-column
        // animation stagger would take tens of seconds (never settling before the
        // dashboard's auto-refresh). Scale both down with the column count so the
        // stack renders as a continuous area; few-column charts (Reporting/chat)
        // keep the original spacious look unchanged.
        const columnCount = this.data.length;
        this.columnsContainer.style.gap =
            columnCount > 60 ? '0px' : columnCount > 24 ? '1px' : columnCount > 12 ? '4px' : '8px';
        const staggerMs = columnCount > 30 ? Math.max(0, Math.round(600 / columnCount)) : 100;

        // Calculate max total value for scaling
        const maxTotal = Math.max(...this.data.map(col =>
            col.segments.reduce((sum, seg) => sum + seg.value, 0)
        ));

        const wrappers = [];

        this.data.forEach((column, columnIndex) => {
            const columnTotal = column.segments.reduce((sum, seg) => sum + seg.value, 0);
            const heightPercent = this.normalize ? 100 : (columnTotal / maxTotal) * 100;

            // Column wrapper — starts at height 0, animates to target
            const colWrapper = document.createElement('div');
            colWrapper.style.flex = '1';
            colWrapper.style.display = 'flex';
            colWrapper.style.flexDirection = 'column-reverse';
            colWrapper.style.height = '0%';
            colWrapper.style.overflow = 'hidden';
            colWrapper.style.cursor = 'pointer';
            colWrapper.style.gap = '2px';
            colWrapper.style.position = 'relative';

            // Segments at their final proportional heights immediately
            column.segments.forEach((segment, segmentIndex) => {
                const segPercent = (segment.value / columnTotal) * 100;
                const color = this.getColor(this._segmentColorIndex(segment, segmentIndex));

                const seg = document.createElement('div');
                seg.style.width = '100%';
                seg.style.borderRadius = '2px';
                seg.style.background = `linear-gradient(135deg, ${color[0]}, ${color[1]})`;
                seg.style.flex = `${segPercent} 0 0`;
                seg.style.minHeight = '0';

                colWrapper.appendChild(seg);
            });

            wrappers.push({ el: colWrapper, heightPercent });
            this.columnsContainer.appendChild(colWrapper);

            // Hover
            colWrapper.addEventListener('mouseenter', () => {
                this.hoveredColumn = columnIndex;
                Array.from(colWrapper.children).forEach(s => { s.style.filter = 'brightness(1.15)'; });
                this.showTooltip(columnIndex, colWrapper);
            });
            colWrapper.addEventListener('mouseleave', () => {
                this.hoveredColumn = null;
                Array.from(colWrapper.children).forEach(s => { s.style.filter = 'none'; });
                this.hideTooltip();
            });
        });

        // Reveal container and start animations after browser paints height:0
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                this.columnsContainer.style.visibility = 'visible';
                wrappers.forEach(({ el, heightPercent }, i) => {
                    el.style.transition = `height ${this.config.animationDuration}ms cubic-bezier(0.34, 1.56, 0.64, 1)`;
                    this._animTimers.push(setTimeout(() => {
                        el.style.height = heightPercent + '%';
                    }, 150 + i * staggerMs));
                });
            });
        });
    }

    showTooltip(columnIndex, anchor) {
        const column = this.data?.[columnIndex];
        if (!column || !column.segments) return;
        const total = column.segments.reduce((sum, seg) => sum + seg.value, 0);

        let tooltipHTML = `<div style="font-weight: 600; margin-bottom: 8px;">${column.label}</div>`;

        // Reverse segments to match visual stacking order (top to bottom)
        [...column.segments].reverse().forEach((segment, reverseIndex) => {
            const index = column.segments.length - 1 - reverseIndex;
            const percent = Math.round((segment.value / total) * 100);
            const dotColor = this.getColor(this._segmentColorIndex(segment, index))[0];
            tooltipHTML += `
                <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
                    <div style="width: 8px; height: 8px; border-radius: 50%; background: ${dotColor};"></div>
                    <span style="font-weight: 600;">${segment.label}: ${segment.value} (${percent}%)</span>
                </div>
            `;
        });

        const isDark = document.body.classList.contains('dark-mode');
        tooltipHTML += `
            <div style="padding-top: 8px; margin-top: 8px; border-top: 1px solid ${isDark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.1)'};">
                <span style="color: #9CA3AF;">Total:</span>
                <span style="font-weight: 600; margin-left: 8px;">${total}</span>
            </div>
        `;

        this.tooltip.innerHTML = tooltipHTML;

        // Position tooltip above the column, centered
        const wrapperRect = this.wrapper.getBoundingClientRect();
        const anchorRect = anchor.getBoundingClientRect();
        const tooltipRect = this.tooltip.getBoundingClientRect();

        let left = (anchorRect.left - wrapperRect.left) + anchorRect.width / 2 - tooltipRect.width / 2;
        let top = (anchorRect.top - wrapperRect.top) - tooltipRect.height - 8;

        // Clamp within wrapper
        const maxLeft = wrapperRect.width - tooltipRect.width - 4;
        if (left < 4) left = 4;
        if (left > maxLeft) left = maxLeft;
        if (top < 4) top = (anchorRect.top - wrapperRect.top) + anchorRect.height / 2 - tooltipRect.height / 2;

        this.tooltip.style.left = left + 'px';
        this.tooltip.style.top = top + 'px';
        this.tooltip.style.opacity = '1';
    }

    hideTooltip() {
        this.tooltip.style.opacity = '0';
    }

    /**
     * RESIZE - Handle size changes (3x2 ↔ 4x2)
     * @param {String} gridSize - '3x2' or '4x2'
     */
    resize(gridSize) {
        if (this.config.gridSize === gridSize) return;

        this.config.gridSize = gridSize;
        const currentData = this.data;

        // Recreate DOM structure
        this.setupDOM();

        // Restore data if exists
        if (currentData) {
            this.setData(currentData);
        }
    }

    /**
     * DEPRECATED METHODS - For backward compatibility
     */

    // Deprecated: Use setData() instead
    render(data) {
        console.warn('StackedColumn: render() is deprecated. Use setData() instead.');
        if (data) {
            this.setData(data);
        }
    }

    // Deprecated: Use setData() instead
    updateData(newData) {
        console.warn('StackedColumn: updateData() is deprecated. Use setData() instead.');
        this.setData(newData);
    }

    // Deprecated: Use setData() instead
    refresh() {
        console.warn('StackedColumn: refresh() is deprecated. Use setData() with current data instead.');
        if (this.data) {
            this.animationKey++;
            this.setData(this.data);
        }
    }

    destroy() {
        this.container.innerHTML = '';
    }
}

// Export to window for wizard access
if (typeof window !== 'undefined') {
    window.DotDNAStackedColumn = DotDNAStackedColumn;
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNAStackedColumn;
}
