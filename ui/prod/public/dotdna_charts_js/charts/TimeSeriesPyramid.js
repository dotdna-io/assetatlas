/**
 * TimeSeriesPyramid - Vertical pyramid chart with time series labels
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 * Supports 4x2, 4x3, 5x3, 6x3, 6x2, 5x2 grid layouts
 */

class DotDNATimeSeriesPyramid extends DotDNASVGChartBase {
    constructor(containerId, options = {}) {
        super(containerId, options);

        this.options = {
            data: options.data || null,
            barWidth: options.barWidth || 24, // Increased from 16
            barGap: options.barGap || 12, // Increased from 8
            maxBarHeight: options.maxBarHeight || 100, // Reduced to make room for x-axis labels
            animationDuration: options.animationDuration || 600,
            topColor: options.topColor || ['#10b981', '#34d399'], // Green for positive
            bottomColor: options.bottomColor || ['#4b5563', '#6b7280'], // Dark grey for negative
            topLabel: options.topLabel || 'True Positive',
            bottomLabel: options.bottomLabel || 'False Positive',
            ...options
        };

        this.hoveredBar = null;
        this.animationKey = 0;
        this.isVisible = false;
        this.layoutSize = null;
        this.allowedSizes = ['4x2', '4x3', '5x3', '6x3', '6x2', '5x2'];

        if (this.options.data) {
            this.render();
        } else {
            this.showNoData();
        }
    }

    detectLayoutSize() {
        // Use gridSize from options if provided (set by dashboard)
        if (this.options.gridSize) {
            return this.options.gridSize;
        }

        // Fallback: Get container dimensions to determine layout
        const containerRect = this.container.getBoundingClientRect();
        const width = containerRect.width;
        const height = containerRect.height;
        const aspectRatio = height / width;

        // Determine layout based on dimensions
        // Size progression: 4x2 (default) → 4x3 → 5x3 → 6x3 → 6x2 → 5x2
        // Very tall layouts (aspect > 1.2): prefer x3 heights
        // Moderate height (aspect 0.6-1.2): prefer x2 or x3 based on width
        // Wide layouts (aspect < 0.6): prefer x2 heights

        if (aspectRatio > 1.2) {
            // Tall: prefer 3-row heights
            if (width > 1000) return '6x3';
            if (width > 800) return '5x3';
            return '4x3';
        } else if (aspectRatio > 0.6) {
            // Moderate: balance width and height
            if (width > 1000) return '6x2';
            if (width > 800) return '5x2';
            return '4x2';
        } else {
            // Wide: prefer 2-row heights for wide layouts
            if (width > 1000) return '6x2';
            if (width > 800) return '5x2';
            return '4x2';
        }
    }

    generateDemoData() {
        const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        const data = [];

        for (let index = 0; index < 12; index++) {
            const position = index / 12;
            const leftWave = Math.sin(position * Math.PI * 2) * 0.3 + 0.5;
            const rightWave = Math.cos(position * Math.PI * 2) * 0.3 + 0.5;

            data.push({
                label: months[index],
                leftValue: Math.round(leftWave * 80) + 20 + Math.round(Math.random() * 20),
                rightValue: Math.round(rightWave * 80) + 20 + Math.round(Math.random() * 20)
            });
        }

        return data;
    }

    render() {
        this.container.innerHTML = '';
        this.container.className = 'dotdna-chart';
        this.container.style.width = '100%';
        this.container.style.height = '100%';
        this.container.style.display = 'flex';
        this.container.style.alignItems = 'center';
        this.container.style.justifyContent = 'center';
        this.container.style.position = 'relative';

        // Detect layout size
        this.layoutSize = this.detectLayoutSize();

        // Detect dark mode - check if body has dark-mode class
        const isDarkMode = document.body.classList.contains('dark-mode');
        const textColor = isDarkMode ? '#FFFFFF' : '#6B7280';

        const wrapper = document.createElement('div');
        wrapper.style.width = '100%';
        wrapper.style.height = '100%';
        wrapper.style.display = 'flex';
        wrapper.style.flexDirection = 'column';
        wrapper.style.padding = '20px';
        wrapper.style.boxSizing = 'border-box';

        // Chart container
        const chartContainer = document.createElement('div');
        chartContainer.style.flex = '1';
        chartContainer.style.position = 'relative';
        chartContainer.style.minHeight = '0';
        chartContainer.style.marginTop = '60px'; // Increased to prevent columns from being truncated at top

        // SVG dimensions
        const horizontalPadding = 20;
        const containerRect = this.container.getBoundingClientRect();
        const svgWidth = Math.max(containerRect.width - 40, 200); // Ensure minimum width
        const svgHeight = Math.max(containerRect.height - 180, 100); // Ensure minimum height
        const centerY = svgHeight / 2;

        // Adjust bar sizing based on layout - smaller bars for narrower layouts
        let barWidth, barGap;
        if (this.layoutSize === '4x2' || this.layoutSize === '4x3') {
            barWidth = 18;
            barGap = 10;
        } else if (this.layoutSize === '5x2' || this.layoutSize === '5x3') {
            barWidth = 20;
            barGap = 11;
        } else { // 6x2 or 6x3
            barWidth = this.options.barWidth; // 24
            barGap = this.options.barGap; // 12
        }

        // Calculate positions - distribute evenly across width
        const availableWidth = svgWidth - (horizontalPadding * 2);
        const barSpacing = availableWidth / this.options.data.length;
        const startX = horizontalPadding;

        // Create SVG
        this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        this.svg.setAttribute('width', svgWidth);
        this.svg.setAttribute('height', svgHeight);
        this.svg.style.display = 'block';

        // Define gradients
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');

        // Top gradient
        const topGradient = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
        topGradient.setAttribute('id', `topGradient-${this.animationKey}`);
        topGradient.setAttribute('x1', '0%');
        topGradient.setAttribute('y1', '0%');
        topGradient.setAttribute('x2', '0%');
        topGradient.setAttribute('y2', '100%');

        const topStop1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        topStop1.setAttribute('offset', '0%');
        topStop1.setAttribute('stop-color', this.options.topColor[0]);
        topStop1.setAttribute('stop-opacity', (this.layoutSize === '4x2' || this.layoutSize === '4x3') ? '0.65' : '0.9');

        const topStop2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        topStop2.setAttribute('offset', '100%');
        topStop2.setAttribute('stop-color', this.options.topColor[1]);
        topStop2.setAttribute('stop-opacity', (this.layoutSize === '4x2' || this.layoutSize === '4x3') ? '0.55' : '0.8');

        topGradient.appendChild(topStop1);
        topGradient.appendChild(topStop2);
        defs.appendChild(topGradient);

        // Bottom gradient
        const bottomGradient = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
        bottomGradient.setAttribute('id', `bottomGradient-${this.animationKey}`);
        bottomGradient.setAttribute('x1', '0%');
        bottomGradient.setAttribute('y1', '0%');
        bottomGradient.setAttribute('x2', '0%');
        bottomGradient.setAttribute('y2', '100%');

        const bottomStop1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        bottomStop1.setAttribute('offset', '0%');
        bottomStop1.setAttribute('stop-color', this.options.bottomColor[0]);
        bottomStop1.setAttribute('stop-opacity', (this.layoutSize === '4x2' || this.layoutSize === '4x3') ? '0.55' : '0.8');

        const bottomStop2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        bottomStop2.setAttribute('offset', '100%');
        bottomStop2.setAttribute('stop-color', this.options.bottomColor[1]);
        bottomStop2.setAttribute('stop-opacity', (this.layoutSize === '4x2' || this.layoutSize === '4x3') ? '0.65' : '0.9');

        bottomGradient.appendChild(bottomStop1);
        bottomGradient.appendChild(bottomStop2);
        defs.appendChild(bottomGradient);

        this.svg.appendChild(defs);

        // Center line - more visible with solid line
        const centerLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        centerLine.setAttribute('x1', '0');
        centerLine.setAttribute('y1', centerY);
        centerLine.setAttribute('x2', svgWidth);
        centerLine.setAttribute('y2', centerY);
        centerLine.setAttribute('stroke', '#D1D5DB');
        centerLine.setAttribute('stroke-width', '3');
        this.svg.appendChild(centerLine);

        // Create bars
        this.options.data.forEach((item, index) => {
            // Center each bar in its allocated space
            const x = startX + (index * barSpacing) + (barSpacing / 2) - (barWidth / 2);
            const topHeight = (item.leftValue / 100) * this.options.maxBarHeight;
            const bottomHeight = (item.rightValue / 100) * this.options.maxBarHeight;

            const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            group.style.cursor = 'pointer';

            // Top bar
            const topBar = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            topBar.setAttribute('x', x);
            topBar.setAttribute('y', centerY - topHeight);
            topBar.setAttribute('width', barWidth);
            topBar.setAttribute('height', topHeight);
            topBar.setAttribute('rx', barWidth / 2);
            topBar.setAttribute('fill', `url(#topGradient-${this.animationKey})`);
            topBar.dataset.index = index;
            topBar.dataset.type = 'top';

            // Bottom bar
            const bottomBar = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            bottomBar.setAttribute('x', x);
            bottomBar.setAttribute('y', centerY);
            bottomBar.setAttribute('width', barWidth);
            bottomBar.setAttribute('height', bottomHeight);
            bottomBar.setAttribute('rx', barWidth / 2);
            bottomBar.setAttribute('fill', `url(#bottomGradient-${this.animationKey})`);
            bottomBar.dataset.index = index;
            bottomBar.dataset.type = 'bottom';

            // Center dot
            const centerDot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            centerDot.setAttribute('cx', x + barWidth / 2);
            centerDot.setAttribute('cy', centerY);
            centerDot.setAttribute('r', '3');
            centerDot.setAttribute('fill', '#6366F1');
            centerDot.setAttribute('opacity', '0.6');

            group.appendChild(topBar);
            group.appendChild(bottomBar);
            group.appendChild(centerDot);

            // Hover events
            group.addEventListener('mouseenter', () => {
                this.hoveredBar = index;
                topBar.style.filter = 'brightness(1.1) drop-shadow(0 4px 8px rgba(236, 72, 153, 0.3))';
                bottomBar.style.filter = 'brightness(1.1) drop-shadow(0 4px 8px rgba(59, 130, 246, 0.3))';
                centerDot.setAttribute('opacity', '1');
                this.showTooltip(index, x + barWidth / 2, centerY);
            });

            group.addEventListener('mouseleave', () => {
                this.hoveredBar = null;
                topBar.style.filter = 'none';
                bottomBar.style.filter = 'none';
                centerDot.setAttribute('opacity', '0.6');
                this.hideTooltip();
            });

            this.svg.appendChild(group);

            // Animate bars
            const animationDelay = index * 20;
            setTimeout(() => {
                topBar.style.transform = 'scaleY(1)';
                topBar.style.transformOrigin = `${x + barWidth / 2}px ${centerY}px`;
                topBar.style.transition = `all ${this.options.animationDuration}ms cubic-bezier(0.4, 0, 0.2, 1)`;

                bottomBar.style.transform = 'scaleY(1)';
                bottomBar.style.transformOrigin = `${x + barWidth / 2}px ${centerY}px`;
                bottomBar.style.transition = `all ${this.options.animationDuration}ms cubic-bezier(0.4, 0, 0.2, 1)`;

                centerDot.style.transform = 'scale(1)';
                centerDot.style.transformOrigin = `${x + barWidth / 2}px ${centerY}px`;
                centerDot.style.transition = `all 300ms ease-out ${animationDelay + 300}ms`;
            }, animationDelay);

            // Initial state
            topBar.style.transform = 'scaleY(0)';
            bottomBar.style.transform = 'scaleY(0)';
            centerDot.style.transform = 'scale(0)';
        });

        chartContainer.appendChild(this.svg);
        wrapper.appendChild(chartContainer);

        // X-axis labels - positioned to align with bars
        const xLabelsContainer = document.createElement('div');
        xLabelsContainer.style.position = 'relative';
        xLabelsContainer.style.height = '20px';
        xLabelsContainer.style.marginTop = '8px';
        xLabelsContainer.style.marginLeft = horizontalPadding + 'px';
        xLabelsContainer.style.width = availableWidth + 'px';

        this.options.data.forEach((item, index) => {
            const label = document.createElement('div');
            label.style.position = 'absolute';
            label.style.left = `${(index * barSpacing) + (barSpacing / 2)}px`;
            label.style.transform = 'translateX(-50%)';
            label.style.fontSize = '11px';
            label.style.fontWeight = '500';
            label.style.color = textColor; // Adapts to dark/light mode
            label.style.textAlign = 'center';
            label.style.opacity = '0';
            label.textContent = item.label;
            xLabelsContainer.appendChild(label);

            // Animate label
            setTimeout(() => {
                label.style.transition = 'opacity 0.3s ease-out';
                label.style.opacity = '1';
            }, 200 + index * 50);
        });

        wrapper.appendChild(xLabelsContainer);

        // Create tooltip (hidden by default)
        this.tooltip = document.createElement('div');
        this.tooltip.style.cssText = `
            position: absolute;
            background-color: #1F2937;
            color: white;
            padding: 12px 16px;
            border-radius: 8px;
            font-size: 12px;
            opacity: 0;
            pointer-events: none;
            z-index: 1000;
            white-space: nowrap;
            transition: opacity 0.2s ease-out;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
        `;
        wrapper.appendChild(this.tooltip);

        // Legend
        const legend = document.createElement('div');
        legend.style.display = 'flex';
        legend.style.justifyContent = 'center';
        legend.style.gap = '24px';
        legend.style.marginTop = '28px';

        const topLegend = document.createElement('div');
        topLegend.style.display = 'flex';
        topLegend.style.alignItems = 'center';
        topLegend.style.gap = '8px';

        const topDot = document.createElement('div');
        topDot.style.width = '12px';
        topDot.style.height = '12px';
        topDot.style.borderRadius = '50%';
        topDot.style.background = `linear-gradient(to bottom, ${this.options.topColor[0]}, ${this.options.topColor[1]})`;

        const topText = document.createElement('span');
        topText.style.fontSize = '13px';
        topText.style.fontWeight = '500';
        topText.style.color = textColor; // Adapts to dark/light mode
        topText.textContent = this.options.topLabel;

        topLegend.appendChild(topDot);
        topLegend.appendChild(topText);

        const bottomLegend = document.createElement('div');
        bottomLegend.style.display = 'flex';
        bottomLegend.style.alignItems = 'center';
        bottomLegend.style.gap = '8px';

        const bottomDot = document.createElement('div');
        bottomDot.style.width = '12px';
        bottomDot.style.height = '12px';
        bottomDot.style.borderRadius = '50%';
        bottomDot.style.background = `linear-gradient(to bottom, ${this.options.bottomColor[0]}, ${this.options.bottomColor[1]})`;

        const bottomText = document.createElement('span');
        bottomText.style.fontSize = '13px';
        bottomText.style.fontWeight = '500';
        bottomText.style.color = textColor; // Adapts to dark/light mode
        bottomText.textContent = this.options.bottomLabel;

        bottomLegend.appendChild(bottomDot);
        bottomLegend.appendChild(bottomText);

        legend.appendChild(topLegend);
        legend.appendChild(bottomLegend);

        wrapper.appendChild(legend);
        this.container.appendChild(wrapper);

        this.isVisible = true;
    }

    showTooltip(index, x, y) {
        const item = this.options.data[index];
        const truePositive = Math.round(item.leftValue);
        const falsePositive = Math.round(item.rightValue);
        const total = truePositive + falsePositive;
        const truePositivePercent = Math.round((truePositive / total) * 100);
        const falsePositivePercent = Math.round((falsePositive / total) * 100);

        this.tooltip.innerHTML = `
            <div style="font-weight: 600; margin-bottom: 8px;">${item.label}</div>
            <div style="margin-bottom: 4px;">
                <span style="color: ${this.options.topColor[1]};">●</span>
                <span style="margin-left: 6px;">${this.options.topLabel}:</span>
                <span style="font-weight: 600; margin-left: 8px;">${truePositive} (${truePositivePercent}%)</span>
            </div>
            <div style="margin-bottom: 8px;">
                <span style="color: ${this.options.bottomColor[0]};">●</span>
                <span style="margin-left: 6px;">${this.options.bottomLabel}:</span>
                <span style="font-weight: 600; margin-left: 8px;">${falsePositive} (${falsePositivePercent}%)</span>
            </div>
            <div style="padding-top: 8px; border-top: 1px solid rgba(255, 255, 255, 0.2);">
                <span style="color: #9CA3AF;">Total:</span>
                <span style="font-weight: 600; margin-left: 8px;">${total}</span>
            </div>
        `;

        const tooltipRect = this.tooltip.getBoundingClientRect();
        const svgRect = this.svg.getBoundingClientRect();

        this.tooltip.style.left = (x - tooltipRect.width / 2) + 'px';
        this.tooltip.style.top = (y - tooltipRect.height / 2) + 'px';
        this.tooltip.style.opacity = '1';
    }

    hideTooltip() {
        this.tooltip.style.opacity = '0';
    }

    refresh() {
        this.animationKey++;
        this.isVisible = false;
        this.render();
    }

    updateData(newData) {
        this.options.data = newData;
        this.refresh();
    }

    destroy() {
        this.container.innerHTML = '';
    }
}

// Export to window for wizard access
if (typeof window !== 'undefined') {
    window.DotDNATimeSeriesPyramid = DotDNATimeSeriesPyramid;
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNATimeSeriesPyramid;
}
