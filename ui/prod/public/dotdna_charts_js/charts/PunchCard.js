/**
 * PunchCard - Asset Inventory Matrix with bubble visualization
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 *
 * DOM-based layout with bubbles at grid-line intersections.
 * Bubbles sized by value, colored by row (category) using palette.
 * Sizes: 3x2, 3x3, 4x2, 4x3
 */

class DotDNAPunchCard extends DotDNASVGChartBase {
    constructor(containerId, config = {}) {
        super(containerId, config);

        this.config = {
            animationDuration: config.animationDuration || 1800,
            gridSize: config.gridSize || '3x2'
        };

        this.data = null;

        // Only render if initial data provided; otherwise wait for setData()
        if (config.data) {
            this.setData(config.data);
        } else {
            this.showNoData();
        }
    }

    generateRandomData() {
        return {
            categories: ['Server', 'Workstation', 'Mobile', 'Network', 'IoT', 'Cloud'],
            sources: ['EDR', 'Active Dir.', 'Firewall', 'SIEM', 'Cloud Prov.', 'Net Scanner'],
            matrix: [
                [245, 189, 156, 298, 123, 267],
                [512, 445, 178, 234, 156, 289],
                [134, 67, 89, 234, 98, 145],
                [0, 89, 234, 167, 45, 312],
                [23, 0, 156, 123, 289, 398],
                [67, 89, 134, 267, 412, 356]
            ]
        };
    }

    setData(data) {
        if (!data || !data.categories || !data.sources || !data.matrix) return;
        this.data = data;
        this.render();
    }

    render() {
        this.container.innerHTML = '';
        this.container.className = 'dotdna-chart';
        this.container.style.width = '100%';
        this.container.style.height = '100%';
        this.container.style.overflow = 'hidden';

        const isDark = document.body.classList.contains('dark-mode');
        const textColor = isDark ? '#9CA3AF' : '#6B7280';
        const labelColor = isDark ? '#D1D5DB' : '#374151';
        const gridColor = isDark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)';

        const palette = window.ChartPalette;
        const colors = palette
            ? palette.gradients.map(g => g.colors)
            : [['#EF4444','#DC2626'],['#F59E0B','#D97706'],['#3B82F6','#2563EB'],['#10B981','#059669'],['#8B5CF6','#7C3AED'],['#06B6D4','#0891B2'],['#EC4899','#DB2777'],['#6366F1','#4F46E5']];

        const numRows = this.data.categories.length;
        const numCols = this.data.sources.length;
        const maxValue = Math.max(...this.data.matrix.flat());

        // Outer wrapper with padding for card title clearance
        const wrapper = document.createElement('div');
        wrapper.style.width = '100%';
        wrapper.style.height = '100%';
        wrapper.style.padding = '28px 12px 6px 6px';
        wrapper.style.boxSizing = 'border-box';

        // Canvas: single positioned container for all elements
        const canvas = document.createElement('div');
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        canvas.style.position = 'relative';

        // Chart area bounds (% of canvas) — inset for labels and bubble overhang
        const chartLeft = 18;
        const chartRight = 90;
        const chartTop = 12;
        const chartBottom = 70;

        const gridX = (col) => chartLeft + (numCols > 1 ? (col / (numCols - 1)) * (chartRight - chartLeft) : (chartRight - chartLeft) / 2);
        const gridY = (row) => chartTop + (numRows > 1 ? (row / (numRows - 1)) * (chartBottom - chartTop) : (chartBottom - chartTop) / 2);

        const allBubbles = [];
        const allLabels = [];

        // Horizontal grid lines
        for (let row = 0; row < numRows; row++) {
            const y = gridY(row);
            const line = document.createElement('div');
            line.style.position = 'absolute';
            line.style.left = chartLeft + '%';
            line.style.width = (chartRight - chartLeft) + '%';
            line.style.top = y + '%';
            line.style.height = '0';
            line.style.borderTop = `1px dashed ${gridColor}`;
            line.style.pointerEvents = 'none';
            canvas.appendChild(line);
        }

        // Vertical grid lines
        for (let col = 0; col < numCols; col++) {
            const x = gridX(col);
            const line = document.createElement('div');
            line.style.position = 'absolute';
            line.style.left = x + '%';
            line.style.top = chartTop + '%';
            line.style.height = (chartBottom - chartTop) + '%';
            line.style.width = '0';
            line.style.borderLeft = `1px dashed ${gridColor}`;
            line.style.pointerEvents = 'none';
            canvas.appendChild(line);
        }

        // X label max width is one column-spacing wide (less a tiny gap) so
        // adjacent labels can't crash into each other when columns are
        // tight. Falls back to the full chart width for the single-col case.
        const colSpacing = numCols > 1
            ? (chartRight - chartLeft) / (numCols - 1)
            : (chartRight - chartLeft);
        const xLabelMaxWidth = Math.max(colSpacing - 1, 8);

        // Y-axis labels (right-aligned to left of chart area)
        for (let row = 0; row < numRows; row++) {
            const y = gridY(row);
            const yLabel = document.createElement('div');
            yLabel.style.position = 'absolute';
            yLabel.style.left = '0';
            yLabel.style.width = (chartLeft - 4) + '%';
            yLabel.style.top = y + '%';
            yLabel.style.transform = 'translateY(-50%)';
            yLabel.style.textAlign = 'right';
            yLabel.style.paddingRight = '6px';
            yLabel.style.boxSizing = 'border-box';
            yLabel.style.fontSize = '11px';
            yLabel.style.fontWeight = '600';
            yLabel.style.color = labelColor;
            yLabel.style.whiteSpace = 'nowrap';
            yLabel.style.overflow = 'hidden';
            yLabel.style.textOverflow = 'ellipsis';
            yLabel.style.lineHeight = '1';
            yLabel.style.opacity = '0';
            yLabel.style.transition = 'opacity 0.4s ease-out';
            yLabel.textContent = this.data.categories[row];
            yLabel.title = this.data.categories[row];
            canvas.appendChild(yLabel);
            allLabels.push({ el: yLabel, delay: row * 60 });
        }

        // X-axis labels (centered below each column line)
        for (let col = 0; col < numCols; col++) {
            const x = gridX(col);
            const xLabel = document.createElement('div');
            xLabel.style.position = 'absolute';
            xLabel.style.left = x + '%';
            xLabel.style.top = (chartBottom + 14) + '%';
            xLabel.style.transform = 'translateX(-50%)';
            xLabel.style.maxWidth = xLabelMaxWidth + '%';
            xLabel.style.fontSize = '10px';
            xLabel.style.fontWeight = '500';
            xLabel.style.color = textColor;
            xLabel.style.textAlign = 'center';
            xLabel.style.whiteSpace = 'nowrap';
            xLabel.style.overflow = 'hidden';
            xLabel.style.textOverflow = 'ellipsis';
            xLabel.style.lineHeight = '1';
            xLabel.style.opacity = '0';
            xLabel.style.transition = 'opacity 0.4s ease-out';
            xLabel.textContent = this.data.sources[col];
            xLabel.title = this.data.sources[col];
            canvas.appendChild(xLabel);
            allLabels.push({ el: xLabel, delay: col * 60 });
        }

        // Bubbles at grid intersections
        for (let row = 0; row < numRows; row++) {
            for (let col = 0; col < numCols; col++) {
                const value = this.data.matrix[row][col];
                if (value <= 0) continue;

                const x = gridX(col);
                const y = gridY(row);
                const normalized = value / maxValue;
                const colorPair = colors[row % colors.length];

                const minSize = 16;
                const maxSize = 48;
                const size = minSize + Math.sqrt(normalized) * (maxSize - minSize);

                const bubble = document.createElement('div');
                bubble.style.position = 'absolute';
                bubble.style.left = x + '%';
                bubble.style.top = y + '%';
                bubble.style.width = size + 'px';
                bubble.style.height = size + 'px';
                bubble.style.borderRadius = '50%';
                bubble.style.background = `radial-gradient(circle at 35% 35%, ${colorPair[0]}, ${colorPair[1]})`;
                bubble.style.boxShadow = '0 2px 8px rgba(0,0,0,0.25)';
                bubble.style.display = 'flex';
                bubble.style.alignItems = 'center';
                bubble.style.justifyContent = 'center';
                bubble.style.cursor = 'pointer';
                bubble.style.transform = 'translate(-50%, -50%) scale(0)';
                bubble.style.transition = 'transform 0.5s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.3s ease';
                bubble.style.zIndex = '1';

                // Value text
                const showNum = size > 28;
                const numEl = document.createElement('span');
                numEl.style.fontSize = Math.max(9, size * 0.28) + 'px';
                numEl.style.fontWeight = '600';
                numEl.style.color = 'white';
                numEl.style.opacity = showNum ? '0.95' : '0';
                numEl.style.pointerEvents = 'none';
                numEl.style.transition = 'all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)';
                numEl.style.whiteSpace = 'nowrap';
                numEl.textContent = value >= 1000 ? (value / 1000).toFixed(1).replace(/\.0$/, '') + 'K' : value;
                bubble.appendChild(numEl);

                // Hover: expand and show number
                bubble.addEventListener('mouseenter', () => {
                    bubble.style.transform = 'translate(-50%, -50%) scale(1.4)';
                    bubble.style.zIndex = '10';
                    bubble.style.boxShadow = '0 4px 16px rgba(0,0,0,0.4)';
                    numEl.style.opacity = '1';
                    numEl.style.fontSize = Math.max(12, size * 0.32) + 'px';
                    numEl.textContent = value.toLocaleString();
                });

                bubble.addEventListener('mouseleave', () => {
                    bubble.style.transform = 'translate(-50%, -50%) scale(1)';
                    bubble.style.zIndex = '1';
                    bubble.style.boxShadow = '0 2px 8px rgba(0,0,0,0.25)';
                    numEl.style.opacity = showNum ? '0.95' : '0';
                    numEl.style.fontSize = Math.max(9, size * 0.28) + 'px';
                    numEl.textContent = value >= 1000 ? (value / 1000).toFixed(1).replace(/\.0$/, '') + 'K' : value;
                });

                canvas.appendChild(bubble);
                allBubbles.push({ el: bubble, row, col });
            }
        }

        wrapper.appendChild(canvas);
        this.container.appendChild(wrapper);

        // Animation: labels fade in, then bubbles pop in wave
        allLabels.forEach(({ el, delay }) => {
            setTimeout(() => { el.style.opacity = '1'; }, 100 + delay);
        });

        allBubbles.forEach(({ el, row, col }) => {
            const delay = 300 + row * 100 + col * 40;
            setTimeout(() => { el.style.transform = 'translate(-50%, -50%) scale(1)'; }, delay);
        });
    }

    resize(gridSize) {
        this.config.gridSize = gridSize;
        if (this.data) this.render();
    }

    refresh() {
        if (this.data) this.render();
    }

    // Deprecated compatibility
    update(newOptions) {
        if (newOptions.data) this.setData(newOptions.data);
        if (newOptions.gridSize) this.resize(newOptions.gridSize);
    }

    destroy() {
        if (this.container) this.container.innerHTML = '';
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNAPunchCard;
}

if (typeof window !== 'undefined') {
    window.DotDNAPunchCard = DotDNAPunchCard;
}
