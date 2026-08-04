/**
 * CleanSegmentedVerticalBar - Vertical bar chart with smooth grow animation
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 *
 * Features:
 * - Solid bars with palette colors
 * - Sequential animation: bars grow one after another
 * - Hover tooltip with value
 * - Y-axis scale and X-axis labels
 */

class DotDNACleanSegmentedVerticalBar extends DotDNASVGChartBase {
    constructor(containerId, options = {}) {
        super(containerId, options);

        this.options = {
            data: options.data || null,
            animationDuration: options.animationDuration || 1200,
            staggerDelay: options.staggerDelay || 80,
            showLegend: options.showLegend !== false,
            ...options
        };

        this.hoveredBar = null;
        this.isVisible = false;
        this.layoutSize = null;

        // Only render if initial data provided; otherwise wait for setData()
        if (this.options.data) {
            if (!Array.isArray(this.options.data) && this.options.data.mode === 'grouped') {
                this.options.groupedData = this.options.data;
                this.options.data = null;
                this.renderGrouped();
            } else {
                this.render();
            }
        } else {
            this.showNoData();
        }
    }

    detectLayoutSize() {
        if (this.options.gridSize) {
            return this.options.gridSize;
        }
        const containerRect = this.container.getBoundingClientRect();
        return containerRect.height > 400 ? '4x2' : '3x2';
    }

    generateDemoData() {
        const palette = window.ChartPalette ? window.ChartPalette.gradients : [
            { colors: ['#EF4444', '#DC2626'] },
            { colors: ['#F59E0B', '#D97706'] },
            { colors: ['#3B82F6', '#2563EB'] },
            { colors: ['#10B981', '#059669'] },
            { colors: ['#8B5CF6', '#7C3AED'] },
            { colors: ['#06B6D4', '#0891B2'] },
            { colors: ['#EC4899', '#DB2777'] }
        ];

        const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul'];
        return months.map((name, i) => ({
            name,
            value: Math.floor(Math.random() * 250) + 100,
            color: palette[i % palette.length].colors[0]
        }));
    }

    render() {
        // Bail out for missing or empty data — otherwise the y-axis scale
        // computes `Math.max(...[])` as -Infinity and the legend renders
        // "NaN -Infinity -Infinity ..." across the chart. Silent (no "No
        // data available" message) so it doesn't overlap with the editor's
        // own "Configure the chart to see a preview" overlay.
        if (!this.options.data || !this.options.data.length) {
            this.container.innerHTML = '';
            return;
        }

        this.container.innerHTML = '';
        this.container.className = 'dotdna-chart';
        this.container.style.width = '100%';
        this.container.style.height = '100%';
        this.container.style.display = 'flex';
        this.container.style.alignItems = 'center';
        this.container.style.justifyContent = 'center';
        this.container.style.position = 'relative';

        this.layoutSize = this.detectLayoutSize();
        const is4x2 = this.layoutSize === '4x2';
        const isDark = document.body.classList.contains('dark-mode');

        const wrapper = document.createElement('div');
        wrapper.style.width = '100%';
        wrapper.style.height = '100%';
        wrapper.style.display = 'flex';
        wrapper.style.flexDirection = 'column';
        wrapper.style.padding = '24px 16px 16px 16px';
        wrapper.style.boxSizing = 'border-box';

        const maxValue = Math.max(...this.options.data.map(d => d.value));

        // Main content (Y-axis + bars)
        const contentContainer = document.createElement('div');
        contentContainer.style.display = 'flex';
        contentContainer.style.flexDirection = 'row';
        contentContainer.style.flex = '1';
        contentContainer.style.minHeight = '0';
        contentContainer.style.marginTop = '24px';

        // Y-axis scale
        const yAxisScale = document.createElement('div');
        yAxisScale.style.display = 'flex';
        yAxisScale.style.flexDirection = 'column';
        yAxisScale.style.justifyContent = 'space-between';
        yAxisScale.style.fontSize = '10px';
        yAxisScale.style.color = isDark ? '#6B7280' : '#9CA3AF';
        yAxisScale.style.paddingRight = '8px';
        yAxisScale.style.width = '32px';
        yAxisScale.style.paddingTop = '4px';
        yAxisScale.style.paddingBottom = '4px';

        const scaleSteps = 4;
        for (let i = scaleSteps; i >= 0; i--) {
            const value = Math.round((maxValue / scaleSteps) * i);
            const label = document.createElement('span');
            label.textContent = value;
            label.style.textAlign = 'right';
            label.style.lineHeight = '1';
            yAxisScale.appendChild(label);
        }

        contentContainer.appendChild(yAxisScale);

        // Bars container
        const barsContainer = document.createElement('div');
        barsContainer.style.flex = '1';
        barsContainer.style.display = 'flex';
        barsContainer.style.flexDirection = 'row';
        barsContainer.style.gap = is4x2 ? '12px' : '8px';
        barsContainer.style.alignItems = 'flex-end';
        barsContainer.style.justifyContent = 'center';
        barsContainer.style.minHeight = '0';

        // Create unique ID for gradients
        const uid = `vbar-${Date.now()}`;

        this.options.data.forEach((item, barIndex) => {
            const barWrapper = document.createElement('div');
            barWrapper.style.position = 'relative';
            barWrapper.style.flex = '1';
            // no maxWidth — flex:1 fills available space
            barWrapper.style.cursor = 'pointer';
            barWrapper.style.display = 'flex';
            barWrapper.style.flexDirection = 'column';
            barWrapper.style.alignItems = 'center';
            barWrapper.style.height = '100%';
            barWrapper.style.justifyContent = 'flex-end';

            // Get palette gradient colors for this bar
            const palette = window.ChartPalette ? window.ChartPalette.gradients : null;
            const gradColors = palette
                ? palette[barIndex % palette.length].colors
                : [item.color, item.color];

            // The bar itself
            const barHeightPercent = (item.value / maxValue) * 100;
            const bar = document.createElement('div');
            bar.style.width = '85%';
            bar.style.margin = '0 auto';
            bar.style.height = '0%';
            bar.style.borderRadius = '4px 4px 0 0';
            bar.style.background = `linear-gradient(to top, ${gradColors[1]}, ${gradColors[0]})`;
            bar.style.transition = `height ${this.options.animationDuration}ms cubic-bezier(0.34, 1.56, 0.64, 1)`;
            bar.style.transformOrigin = 'bottom center';
            bar.style.position = 'relative';

            barWrapper.appendChild(bar);

            // Animate bar with stagger
            setTimeout(() => {
                bar.style.height = barHeightPercent + '%';
            }, 150 + barIndex * this.options.staggerDelay);

            // Value label — inside bar if tall enough, above bar otherwise
            const barTooSmall = barHeightPercent < 15;
            const valueLabel = document.createElement('div');
            valueLabel.style.position = 'absolute';
            valueLabel.style.left = '0';
            valueLabel.style.right = '0';
            valueLabel.style.textAlign = 'center';
            valueLabel.style.fontSize = '11px';
            valueLabel.style.fontWeight = '600';
            valueLabel.style.opacity = '0';
            valueLabel.style.pointerEvents = 'none';
            valueLabel.style.whiteSpace = 'nowrap';
            valueLabel.style.transition = 'opacity 0.15s ease-out';
            valueLabel.textContent = item.value;

            if (barTooSmall) {
                // Show above the bar in chart text color
                valueLabel.style.bottom = '100%';
                valueLabel.style.marginBottom = '2px';
                valueLabel.style.color = isDark ? '#D1D5DB' : '#374151';
            } else {
                // Show inside bar at top
                valueLabel.style.top = '4px';
                valueLabel.style.color = '#fff';
                valueLabel.style.textShadow = '0 1px 2px rgba(0,0,0,0.3)';
            }
            bar.appendChild(valueLabel);

            // Hover
            barWrapper.addEventListener('mouseenter', () => {
                this.hoveredBar = barIndex;
                valueLabel.style.opacity = '1';
                bar.style.filter = 'brightness(1.15)';
            });

            barWrapper.addEventListener('mouseleave', () => {
                this.hoveredBar = null;
                valueLabel.style.opacity = '0';
                bar.style.filter = 'brightness(1)';
            });

            barsContainer.appendChild(barWrapper);
        });

        contentContainer.appendChild(barsContainer);
        wrapper.appendChild(contentContainer);

        // X-axis labels — must match barsContainer layout exactly
        const labelsContainer = document.createElement('div');
        labelsContainer.style.display = 'flex';
        labelsContainer.style.flexDirection = 'row';
        labelsContainer.style.gap = is4x2 ? '12px' : '8px';
        labelsContainer.style.justifyContent = 'center';
        labelsContainer.style.flex = '0 0 auto';
        labelsContainer.style.paddingLeft = '40px'; // Y-axis width (32px) + padding (8px)
        labelsContainer.style.paddingRight = '0';
        labelsContainer.style.marginTop = '12px';

        this.options.data.forEach((item, index) => {
            const labelWrapper = document.createElement('div');
            labelWrapper.style.flex = '1';
            labelWrapper.style.display = 'flex';
            labelWrapper.style.justifyContent = 'center';
            labelWrapper.style.minWidth = '0';

            const label = document.createElement('div');
            label.style.fontSize = '12px';
            label.style.fontWeight = '500';
            label.style.color = isDark ? '#9CA3AF' : '#6B7280';
            label.style.textAlign = 'center';
            label.style.opacity = '0';
            label.style.transition = 'opacity 0.3s ease-out';
            // Truncate like grouped mode — a long name would otherwise widen
            // its column past the bar underneath and push the row off-chart.
            label.style.overflow = 'hidden';
            label.style.textOverflow = 'ellipsis';
            label.style.whiteSpace = 'nowrap';
            label.title = item.name;
            label.textContent = item.name;
            labelWrapper.appendChild(label);

            labelsContainer.appendChild(labelWrapper);

            // Fade in label in sync with its bar
            setTimeout(() => {
                label.style.opacity = '1';
            }, index * this.options.staggerDelay + this.options.animationDuration * 0.3);
        });

        wrapper.appendChild(labelsContainer);
        this.container.appendChild(wrapper);

        this.isVisible = true;
    }

    setData(data) {
        // Grouped mode: {mode: 'grouped', groups, legend}
        if (data && !Array.isArray(data) && data.mode === 'grouped') {
            this.options.groupedData = data;
            this.options.data = null;
            this.renderGrouped();
            return;
        }

        if (!data || !Array.isArray(data)) return;

        // Simple mode: assign palette colors if not provided
        this.options.groupedData = null;
        const palette = window.ChartPalette ? window.ChartPalette.gradients : null;
        this.options.data = data.map((item, i) => ({
            ...item,
            color: item.color || (palette ? palette[i % palette.length].colors[0] : '#3B82F6')
        }));

        this.render();
    }

    renderGrouped() {
        const { groups, legend } = this.options.groupedData;
        if (!groups || groups.length === 0) return;

        // Cancel any in-flight animation timers from prior render
        (this._animTimers || []).forEach(id => clearTimeout(id));
        this._animTimers = [];

        this.container.innerHTML = '';
        this.container.className = 'dotdna-chart';
        this.container.style.width = '100%';
        this.container.style.height = '100%';
        this.container.style.display = 'flex';
        this.container.style.alignItems = 'center';
        this.container.style.justifyContent = 'center';
        this.container.style.position = 'relative';

        this.layoutSize = this.detectLayoutSize();
        const isDark = document.body.classList.contains('dark-mode');

        const wrapper = document.createElement('div');
        wrapper.style.width = '100%';
        wrapper.style.height = '100%';
        wrapper.style.display = 'flex';
        wrapper.style.flexDirection = 'column';
        wrapper.style.padding = '24px 16px 8px 16px';
        wrapper.style.boxSizing = 'border-box';

        // Find global max value
        let maxValue = 0;
        groups.forEach(g => g.bars.forEach(b => { if (b.value > maxValue) maxValue = b.value; }));
        if (maxValue === 0) maxValue = 1;

        // Main content (Y-axis + clusters)
        const contentContainer = document.createElement('div');
        contentContainer.style.display = 'flex';
        contentContainer.style.flexDirection = 'row';
        contentContainer.style.flex = '1';
        contentContainer.style.minHeight = '0';
        contentContainer.style.marginTop = '16px';

        // Y-axis scale
        const yAxisScale = document.createElement('div');
        yAxisScale.style.display = 'flex';
        yAxisScale.style.flexDirection = 'column';
        yAxisScale.style.justifyContent = 'space-between';
        yAxisScale.style.fontSize = '10px';
        yAxisScale.style.color = isDark ? '#6B7280' : '#9CA3AF';
        yAxisScale.style.paddingRight = '8px';
        yAxisScale.style.width = '32px';
        yAxisScale.style.paddingTop = '4px';
        yAxisScale.style.paddingBottom = '4px';

        const scaleSteps = 4;
        for (let i = scaleSteps; i >= 0; i--) {
            const value = Math.round((maxValue / scaleSteps) * i);
            const label = document.createElement('span');
            label.textContent = value;
            label.style.textAlign = 'right';
            label.style.lineHeight = '1';
            yAxisScale.appendChild(label);
        }
        contentContainer.appendChild(yAxisScale);

        // Clusters container
        const clustersContainer = document.createElement('div');
        clustersContainer.style.flex = '1';
        clustersContainer.style.display = 'flex';
        clustersContainer.style.flexDirection = 'row';
        clustersContainer.style.gap = '12px';
        clustersContainer.style.alignItems = 'flex-end';
        clustersContainer.style.justifyContent = 'center';
        clustersContainer.style.minHeight = '0';

        groups.forEach((group, gIdx) => {
            const clusterWrapper = document.createElement('div');
            clusterWrapper.style.flex = '1';
            clusterWrapper.style.display = 'flex';
            clusterWrapper.style.flexDirection = 'row';
            clusterWrapper.style.gap = '2px';
            clusterWrapper.style.alignItems = 'flex-end';
            clusterWrapper.style.height = '100%';

            group.bars.forEach((bar, bIdx) => {
                const barWrapper = document.createElement('div');
                barWrapper.style.flex = '1';
                barWrapper.style.display = 'flex';
                barWrapper.style.flexDirection = 'column';
                barWrapper.style.alignItems = 'center';
                barWrapper.style.height = '100%';
                barWrapper.style.justifyContent = 'flex-end';
                barWrapper.style.cursor = 'pointer';
                barWrapper.style.position = 'relative';

                const barHeightPercent = (bar.value / maxValue) * 100;
                const barEl = document.createElement('div');
                barEl.style.width = '100%';
                barEl.style.height = '0%';
                barEl.style.borderRadius = '3px 3px 0 0';
                barEl.style.background = bar.color;
                barEl.style.transition = `height ${this.options.animationDuration}ms cubic-bezier(0.34, 1.56, 0.64, 1)`;
                barEl.style.position = 'relative';
                barWrapper.appendChild(barEl);

                // Animate
                const delay = 150 + gIdx * this.options.staggerDelay + bIdx * 40;
                this._animTimers.push(setTimeout(() => { barEl.style.height = barHeightPercent + '%'; }, delay));

                // Value label — inside bar if tall enough, above bar otherwise
                const barTooSmall = barHeightPercent < 15;
                const valueLabel = document.createElement('div');
                valueLabel.style.position = 'absolute';
                valueLabel.style.left = '0';
                valueLabel.style.right = '0';
                valueLabel.style.textAlign = 'center';
                valueLabel.style.fontSize = '10px';
                valueLabel.style.fontWeight = '600';
                valueLabel.style.opacity = '0';
                valueLabel.style.pointerEvents = 'none';
                valueLabel.style.transition = 'opacity 0.15s ease-out';
                valueLabel.textContent = bar.value;

                if (barTooSmall) {
                    valueLabel.style.bottom = '100%';
                    valueLabel.style.marginBottom = '2px';
                    valueLabel.style.color = isDark ? '#D1D5DB' : '#374151';
                } else {
                    valueLabel.style.top = '2px';
                    valueLabel.style.color = '#fff';
                    valueLabel.style.textShadow = '0 1px 2px rgba(0,0,0,0.3)';
                }
                barEl.appendChild(valueLabel);

                // Tooltip on hover
                barWrapper.addEventListener('mouseenter', () => {
                    valueLabel.style.opacity = '1';
                    barEl.style.filter = 'brightness(1.15)';
                    this._showGroupedTooltip(barWrapper, group.name, bar.label, bar.value);
                });
                barWrapper.addEventListener('mouseleave', () => {
                    valueLabel.style.opacity = '0';
                    barEl.style.filter = 'brightness(1)';
                    this._hideGroupedTooltip();
                });

                clusterWrapper.appendChild(barWrapper);
            });

            clustersContainer.appendChild(clusterWrapper);
        });

        contentContainer.appendChild(clustersContainer);
        wrapper.appendChild(contentContainer);

        // X-axis labels (group names)
        const labelsContainer = document.createElement('div');
        labelsContainer.style.display = 'flex';
        labelsContainer.style.flexDirection = 'row';
        labelsContainer.style.gap = '12px';
        labelsContainer.style.justifyContent = 'center';
        labelsContainer.style.paddingLeft = '40px';
        labelsContainer.style.marginTop = '8px';

        groups.forEach((group, gIdx) => {
            const labelWrapper = document.createElement('div');
            labelWrapper.style.flex = '1';
            labelWrapper.style.display = 'flex';
            labelWrapper.style.justifyContent = 'center';

            const label = document.createElement('div');
            label.style.fontSize = '11px';
            label.style.fontWeight = '500';
            label.style.color = isDark ? '#9CA3AF' : '#6B7280';
            label.style.textAlign = 'center';
            label.style.opacity = '0';
            label.style.transition = 'opacity 0.3s ease-out';
            label.style.overflow = 'hidden';
            label.style.textOverflow = 'ellipsis';
            label.style.whiteSpace = 'nowrap';
            label.textContent = group.name;
            labelWrapper.appendChild(label);
            labelsContainer.appendChild(labelWrapper);

            this._animTimers.push(setTimeout(() => { label.style.opacity = '1'; },
                gIdx * this.options.staggerDelay + this.options.animationDuration * 0.3));
        });

        wrapper.appendChild(labelsContainer);

        // Legend
        if (legend && legend.length > 0) {
            const legendContainer = document.createElement('div');
            legendContainer.style.display = 'flex';
            legendContainer.style.flexWrap = 'wrap';
            legendContainer.style.gap = '12px';
            legendContainer.style.justifyContent = 'center';
            legendContainer.style.marginTop = '8px';
            legendContainer.style.paddingLeft = '40px';

            legend.forEach(item => {
                const entry = document.createElement('div');
                entry.style.display = 'flex';
                entry.style.alignItems = 'center';
                entry.style.gap = '4px';

                const swatch = document.createElement('div');
                swatch.style.width = '10px';
                swatch.style.height = '10px';
                swatch.style.borderRadius = '2px';
                swatch.style.background = item.color;
                swatch.style.flexShrink = '0';

                const text = document.createElement('span');
                text.style.fontSize = '10px';
                text.style.color = isDark ? '#9CA3AF' : '#6B7280';
                text.textContent = item.label;

                entry.appendChild(swatch);
                entry.appendChild(text);
                legendContainer.appendChild(entry);
            });

            wrapper.appendChild(legendContainer);
        }

        this.container.appendChild(wrapper);
        this.isVisible = true;
    }

    _showGroupedTooltip(anchor, groupName, segmentName, value) {
        this._hideGroupedTooltip();
        const isDark = document.body.classList.contains('dark-mode');
        const tip = document.createElement('div');
        tip.className = 'dotdna-grouped-tooltip';
        tip.style.position = 'absolute';
        tip.style.bottom = '100%';
        tip.style.left = '50%';
        tip.style.transform = 'translateX(-50%)';
        tip.style.marginBottom = '4px';
        tip.style.padding = '4px 8px';
        tip.style.borderRadius = '4px';
        tip.style.background = isDark ? '#374151' : '#1F2937';
        tip.style.color = '#fff';
        tip.style.fontSize = '11px';
        tip.style.whiteSpace = 'nowrap';
        tip.style.pointerEvents = 'none';
        tip.style.zIndex = '10';
        tip.innerHTML = `<strong>${groupName}</strong>: ${segmentName} = ${value}`;
        anchor.appendChild(tip);
        this._activeTooltip = tip;
    }

    _hideGroupedTooltip() {
        if (this._activeTooltip) {
            this._activeTooltip.remove();
            this._activeTooltip = null;
        }
    }

    refresh() {
        this.isVisible = false;
        if (!this.options.data && !this.options.groupedData) return;
        this.options.groupedData ? this.renderGrouped() : this.render();
    }

    updateData(newData) {
        this.setData(newData);
    }

    resize(gridSize) {
        this.options.gridSize = gridSize;
        if (!this.options.data && !this.options.groupedData) return;
        this.options.groupedData ? this.renderGrouped() : this.render();
    }

    destroy() {
        (this._animTimers || []).forEach(id => clearTimeout(id));
        this._animTimers = [];
        this._activeTooltip = null;
        this.container.innerHTML = '';
    }
}

// Export to window for wizard access
if (typeof window !== 'undefined') {
    window.DotDNACleanSegmentedVerticalBar = DotDNACleanSegmentedVerticalBar;
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNACleanSegmentedVerticalBar;
}
