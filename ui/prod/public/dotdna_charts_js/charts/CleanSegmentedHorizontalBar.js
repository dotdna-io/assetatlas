/**
 * CleanSegmentedHorizontalBar - Horizontal bar chart with smooth grow animation
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 *
 * Features:
 * - Solid bars with palette colors
 * - Sequential animation: bars grow one after another
 * - Hover tooltip with value
 * - Y-axis labels and X-axis scale
 */

class DotDNACleanSegmentedHorizontalBar extends DotDNASVGChartBase {
    constructor(containerId, options = {}) {
        super(containerId, options);

        this.options = {
            data: options.data || null,
            animationDuration: options.animationDuration || 1200,
            staggerDelay: options.staggerDelay || 80,
            labelWidth: options.labelWidth || 50,
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
        return containerRect.height > 400 ? '3x3' : '3x2';
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
        // Bail out for missing or empty data — otherwise the x-axis scale
        // computes `Math.max(...[])` as -Infinity and the legend renders
        // "NaN -Infinity -Infinity ..." across the bottom. Silent (no "No
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
        const isTall = this.layoutSize === '3x3';
        const isDark = document.body.classList.contains('dark-mode');

        // Measure longest label to auto-size the label column
        const measurer = document.createElement('span');
        measurer.style.cssText = 'font:500 12px system-ui,-apple-system,sans-serif;visibility:hidden;position:absolute;white-space:nowrap';
        document.body.appendChild(measurer);
        let labelWidth = 40;
        this.options.data.forEach(d => {
            measurer.textContent = d.name;
            labelWidth = Math.max(labelWidth, measurer.offsetWidth);
        });
        document.body.removeChild(measurer);
        labelWidth = Math.min(labelWidth + 6, 130);

        const wrapper = document.createElement('div');
        wrapper.style.width = '100%';
        wrapper.style.height = '100%';
        wrapper.style.display = 'flex';
        wrapper.style.flexDirection = 'column';
        // Right padding gives the bar room to overshoot during the bouncy
        // grow animation (cubic-bezier(0.34, 1.56, 0.64, 1) peaks around
        // 1.08x the target width) without hitting the card edge.
        wrapper.style.padding = '42px 56px 8px 16px';
        wrapper.style.boxSizing = 'border-box';

        const maxValue = Math.max(...this.options.data.map(d => d.value));

        // Main content (labels + bars)
        const chartContainer = document.createElement('div');
        chartContainer.style.position = 'relative';
        chartContainer.style.flex = '1';
        chartContainer.style.display = 'flex';
        chartContainer.style.flexDirection = 'row';
        chartContainer.style.alignItems = 'stretch';

        // Y-axis labels — must match barsContainer layout exactly
        const labelsContainer = document.createElement('div');
        labelsContainer.style.width = labelWidth + 'px';
        labelsContainer.style.flexShrink = '0';
        labelsContainer.style.display = 'flex';
        labelsContainer.style.flexDirection = 'column';
        labelsContainer.style.gap = isTall ? '12px' : '4px';
        labelsContainer.style.marginRight = '12px';

        this.options.data.forEach((item, index) => {
            const label = document.createElement('div');
            label.style.fontSize = '12px';
            label.style.fontWeight = '500';
            label.style.color = isDark ? '#9CA3AF' : '#6B7280';
            label.style.textAlign = 'right';
            label.style.opacity = '0';
            label.style.transition = 'opacity 0.3s ease-out';
            label.style.flex = '1';
            label.style.display = 'flex';
            label.style.alignItems = 'center';
            label.style.justifyContent = 'flex-end';
            label.textContent = item.name;
            labelsContainer.appendChild(label);

            // Fade in label in sync with its bar
            setTimeout(() => {
                label.style.opacity = '1';
            }, index * this.options.staggerDelay + this.options.animationDuration * 0.3);
        });

        chartContainer.appendChild(labelsContainer);

        // Bars container
        const barsContainer = document.createElement('div');
        barsContainer.style.flex = '1';
        barsContainer.style.display = 'flex';
        barsContainer.style.flexDirection = 'column';
        barsContainer.style.gap = isTall ? '12px' : '4px';

        this.options.data.forEach((item, barIndex) => {
            const barWrapper = document.createElement('div');
            barWrapper.style.position = 'relative';
            barWrapper.style.flex = '1';
            barWrapper.style.cursor = 'pointer';

            // Get palette gradient colors for this bar
            const palette = window.ChartPalette ? window.ChartPalette.gradients : null;
            const gradColors = palette
                ? palette[barIndex % palette.length].colors
                : [item.color, item.color];

            // The bar itself
            const barWidthPercent = (item.value / maxValue) * 100;
            const bar = document.createElement('div');
            bar.style.width = '0%';
            bar.style.height = '100%';
            bar.style.borderRadius = '0 4px 4px 0';
            bar.style.background = `linear-gradient(to right, ${gradColors[1]}, ${gradColors[0]})`;
            // Mild spring (max overshoot ≈ 1.3%) — the previous (0.34, 1.56,
            // 0.64, 1) bezier overshot ~10%, which on a 6-col bar pushed
            // the longest bar visibly past the card edge regardless of the
            // wrapper's right padding.
            bar.style.transition = `width ${this.options.animationDuration}ms cubic-bezier(0.34, 1.2, 0.64, 1)`;
            bar.style.transformOrigin = 'left center';

            barWrapper.appendChild(bar);

            // Animate bar with stagger
            setTimeout(() => {
                bar.style.width = barWidthPercent + '%';
            }, 150 + barIndex * this.options.staggerDelay);

            // Value label — inside bar if wide enough, to the right otherwise
            const barTooSmall = barWidthPercent < 15;
            const valueLabel = document.createElement('div');
            valueLabel.style.position = 'absolute';
            valueLabel.style.top = '0';
            valueLabel.style.bottom = '0';
            valueLabel.style.display = 'flex';
            valueLabel.style.alignItems = 'center';
            valueLabel.style.fontSize = '11px';
            valueLabel.style.fontWeight = '600';
            valueLabel.style.opacity = '0';
            valueLabel.style.pointerEvents = 'none';
            valueLabel.style.whiteSpace = 'nowrap';
            valueLabel.style.transition = 'opacity 0.15s ease-out';
            valueLabel.textContent = item.value;

            if (barTooSmall) {
                // Show to the right of the bar in chart text color
                valueLabel.style.left = '100%';
                valueLabel.style.marginLeft = '6px';
                valueLabel.style.color = isDark ? '#D1D5DB' : '#374151';
            } else {
                // Show inside bar at right end
                valueLabel.style.right = '6px';
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

        chartContainer.appendChild(barsContainer);
        wrapper.appendChild(chartContainer);

        // X-axis scale
        const scaleContainer = document.createElement('div');
        scaleContainer.style.display = 'flex';
        scaleContainer.style.marginTop = '12px';
        scaleContainer.style.paddingLeft = (labelWidth + 12) + 'px';

        const scaleInner = document.createElement('div');
        scaleInner.style.flex = '1';
        scaleInner.style.display = 'flex';
        scaleInner.style.justifyContent = 'space-between';
        scaleInner.style.fontSize = '10px';
        scaleInner.style.color = isDark ? '#6B7280' : '#9CA3AF';

        const scaleSteps = 4;
        for (let i = 0; i <= scaleSteps; i++) {
            const value = Math.round((maxValue / scaleSteps) * i);
            const label = document.createElement('span');
            label.textContent = value;
            scaleInner.appendChild(label);
        }

        scaleContainer.appendChild(scaleInner);
        wrapper.appendChild(scaleContainer);
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
        (this._animTimers || []).forEach(id => clearTimeout(id));
        this._animTimers = [];

        const { groups, legend } = this.options.groupedData;
        if (!groups || groups.length === 0) return;

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

        // Measure longest group label
        const measurer = document.createElement('span');
        measurer.style.cssText = 'font:500 11px system-ui,-apple-system,sans-serif;visibility:hidden;position:absolute;white-space:nowrap';
        document.body.appendChild(measurer);
        let labelWidth = 40;
        groups.forEach(g => {
            measurer.textContent = g.name;
            labelWidth = Math.max(labelWidth, measurer.offsetWidth);
        });
        document.body.removeChild(measurer);
        labelWidth = Math.min(labelWidth + 6, 130);

        const wrapper = document.createElement('div');
        wrapper.style.width = '100%';
        wrapper.style.height = '100%';
        wrapper.style.display = 'flex';
        wrapper.style.flexDirection = 'column';
        wrapper.style.padding = '34px 56px 8px 16px';
        wrapper.style.boxSizing = 'border-box';

        // Find global max value
        let maxValue = 0;
        groups.forEach(g => g.bars.forEach(b => { if (b.value > maxValue) maxValue = b.value; }));
        if (maxValue === 0) maxValue = 1;

        // Main content (labels + clusters)
        const chartContainer = document.createElement('div');
        chartContainer.style.position = 'relative';
        chartContainer.style.flex = '1';
        chartContainer.style.display = 'flex';
        chartContainer.style.flexDirection = 'row';
        chartContainer.style.alignItems = 'stretch';

        // Y-axis labels (group names)
        const labelsContainer = document.createElement('div');
        labelsContainer.style.width = labelWidth + 'px';
        labelsContainer.style.flexShrink = '0';
        labelsContainer.style.display = 'flex';
        labelsContainer.style.flexDirection = 'column';
        labelsContainer.style.gap = '8px';
        labelsContainer.style.marginRight = '12px';

        groups.forEach((group, gIdx) => {
            const label = document.createElement('div');
            label.style.fontSize = '11px';
            label.style.fontWeight = '500';
            label.style.color = isDark ? '#9CA3AF' : '#6B7280';
            label.style.textAlign = 'right';
            label.style.opacity = '0';
            label.style.transition = 'opacity 0.3s ease-out';
            label.style.flex = '1';
            label.style.display = 'flex';
            label.style.alignItems = 'center';
            label.style.justifyContent = 'flex-end';
            label.style.overflow = 'hidden';
            label.style.textOverflow = 'ellipsis';
            label.style.whiteSpace = 'nowrap';
            label.textContent = group.name;
            labelsContainer.appendChild(label);

            this._animTimers.push(setTimeout(() => { label.style.opacity = '1'; },
                gIdx * this.options.staggerDelay + this.options.animationDuration * 0.3));
        });

        chartContainer.appendChild(labelsContainer);

        // Clusters container (rows of grouped bars)
        const clustersContainer = document.createElement('div');
        clustersContainer.style.flex = '1';
        clustersContainer.style.display = 'flex';
        clustersContainer.style.flexDirection = 'column';
        clustersContainer.style.gap = '8px';

        groups.forEach((group, gIdx) => {
            const clusterWrapper = document.createElement('div');
            clusterWrapper.style.flex = '1';
            clusterWrapper.style.display = 'flex';
            clusterWrapper.style.flexDirection = 'column';
            clusterWrapper.style.gap = '2px';
            clusterWrapper.style.justifyContent = 'center';

            group.bars.forEach((bar, bIdx) => {
                const barWrapper = document.createElement('div');
                barWrapper.style.position = 'relative';
                barWrapper.style.flex = '1';
                barWrapper.style.cursor = 'pointer';
                barWrapper.style.minHeight = '0';

                const barWidthPercent = (bar.value / maxValue) * 100;
                const barEl = document.createElement('div');
                barEl.style.width = '0%';
                barEl.style.height = '100%';
                barEl.style.borderRadius = '0 3px 3px 0';
                barEl.style.background = bar.color;
                barEl.style.transition = `width ${this.options.animationDuration}ms cubic-bezier(0.34, 1.2, 0.64, 1)`;
                barEl.style.position = 'relative';
                barWrapper.appendChild(barEl);

                // Animate
                const delay = 150 + gIdx * this.options.staggerDelay + bIdx * 40;
                this._animTimers.push(setTimeout(() => { barEl.style.width = barWidthPercent + '%'; }, delay));

                // Value label — inside bar if wide enough, to the right otherwise
                const barTooSmall = barWidthPercent < 15;
                const valueLabel = document.createElement('div');
                valueLabel.style.position = 'absolute';
                valueLabel.style.top = '0';
                valueLabel.style.bottom = '0';
                valueLabel.style.display = 'flex';
                valueLabel.style.alignItems = 'center';
                valueLabel.style.fontSize = '10px';
                valueLabel.style.fontWeight = '600';
                valueLabel.style.opacity = '0';
                valueLabel.style.pointerEvents = 'none';
                valueLabel.style.transition = 'opacity 0.15s ease-out';
                valueLabel.textContent = bar.value;

                if (barTooSmall) {
                    valueLabel.style.left = '100%';
                    valueLabel.style.marginLeft = '6px';
                    valueLabel.style.color = isDark ? '#D1D5DB' : '#374151';
                } else {
                    valueLabel.style.right = '6px';
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

        chartContainer.appendChild(clustersContainer);
        wrapper.appendChild(chartContainer);

        // X-axis scale
        const scaleContainer = document.createElement('div');
        scaleContainer.style.display = 'flex';
        scaleContainer.style.marginTop = '8px';
        scaleContainer.style.paddingLeft = (labelWidth + 12) + 'px';

        const scaleInner = document.createElement('div');
        scaleInner.style.flex = '1';
        scaleInner.style.display = 'flex';
        scaleInner.style.justifyContent = 'space-between';
        scaleInner.style.fontSize = '10px';
        scaleInner.style.color = isDark ? '#6B7280' : '#9CA3AF';

        const scaleSteps = 4;
        for (let i = 0; i <= scaleSteps; i++) {
            const value = Math.round((maxValue / scaleSteps) * i);
            const label = document.createElement('span');
            label.textContent = value;
            scaleInner.appendChild(label);
        }

        scaleContainer.appendChild(scaleInner);
        wrapper.appendChild(scaleContainer);

        // Legend
        if (legend && legend.length > 0) {
            const legendContainer = document.createElement('div');
            legendContainer.style.display = 'flex';
            legendContainer.style.flexWrap = 'wrap';
            legendContainer.style.gap = '12px';
            legendContainer.style.justifyContent = 'center';
            legendContainer.style.marginTop = '8px';
            legendContainer.style.paddingLeft = (labelWidth + 12) + 'px';

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
        tip.style.top = '50%';
        tip.style.left = '100%';
        tip.style.transform = 'translateY(-50%)';
        tip.style.marginLeft = '4px';
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
    window.DotDNACleanSegmentedHorizontalBar = DotDNACleanSegmentedHorizontalBar;
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNACleanSegmentedHorizontalBar;
}
