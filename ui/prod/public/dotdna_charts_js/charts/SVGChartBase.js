/**
 * SVGChartBase - Base class for SVG-based charts
 * Provides common SVG utilities and path generation
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 */

class DotDNASVGChartBase {
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        if (!this.container) {
            throw new Error(`Container with id "${containerId}" not found`);
        }

        this.options = {
            width: options.width || 500,
            height: options.height || 500,
            centerX: options.centerX || 250,
            centerY: options.centerY || 250,
            animate: options.animate !== undefined ? options.animate : true,
            ...options
        };

        this.svg = null;
        this.tooltip = null;
    }

    /**
     * Create the main SVG element
     */
    createSVG() {
        this.container.className = 'dotdna-chart';

        this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        this.svg.setAttribute('viewBox', `0 0 ${this.options.width} ${this.options.height}`);
        this.svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');

        this.container.innerHTML = '';
        this.container.appendChild(this.svg);

        this.createTooltip();

        return this.svg;
    }

    /**
     * Create tooltip element
     */
    createTooltip() {
        // Check if tooltip already exists globally
        this.tooltip = document.getElementById('dotdna-tooltip');
        if (!this.tooltip) {
            this.tooltip = document.createElement('div');
            this.tooltip.id = 'dotdna-tooltip';
            this.tooltip.className = 'dotdna-tooltip';
            document.body.appendChild(this.tooltip);
        }
    }

    /**
     * Show tooltip
     */
    showTooltip(text, event) {
        if (!this.tooltip) return;

        this.tooltip.textContent = text;
        this.tooltip.style.left = `${event.clientX + 10}px`;
        this.tooltip.style.top = `${event.clientY - 30}px`;
        this.tooltip.classList.add('visible');
    }

    /**
     * Update tooltip position
     */
    moveTooltip(event) {
        if (!this.tooltip) return;

        this.tooltip.style.left = `${event.clientX + 10}px`;
        this.tooltip.style.top = `${event.clientY - 30}px`;
    }

    /**
     * Hide tooltip
     */
    hideTooltip() {
        if (!this.tooltip) return;
        this.tooltip.classList.remove('visible');
    }

    /**
     * Angular sweep (degrees) per data item from its percentage `value`.
     *
     * Slices thinner than the inter-slice gap would invert once the path
     * insets both edges by gap/2 — the wedge flips and paints backwards over
     * its neighbours. Give every non-zero slice a floor of gap + minVisibleDeg
     * so it stays visible, then rescale all slices so the total is unchanged.
     */
    computeSliceSweeps(gapDeg, minVisibleDeg = 1.5) {
        const raw = this.options.data.map(item => (item.value / 100) * 360);
        const total = raw.reduce((sum, v) => sum + v, 0);
        if (total <= 0) return raw;

        const floor = gapDeg + minVisibleDeg;
        const allocated = raw.map(v => (v > 0 ? Math.max(v, floor) : 0));
        const allocatedTotal = allocated.reduce((sum, v) => sum + v, 0);
        const scale = total / allocatedTotal;
        return allocated.map(v => v * scale);
    }

    /**
     * Create arc/donut path (for pie/donut charts)
     */
    createArcPath(cx, cy, outerRadius, innerRadius, startAngle, endAngle) {
        const startRad = startAngle * Math.PI / 180;
        const endRad = endAngle * Math.PI / 180;

        const x1 = cx + outerRadius * Math.cos(startRad);
        const y1 = cy + outerRadius * Math.sin(startRad);
        const x2 = cx + outerRadius * Math.cos(endRad);
        const y2 = cy + outerRadius * Math.sin(endRad);
        const x3 = cx + innerRadius * Math.cos(endRad);
        const y3 = cy + innerRadius * Math.sin(endRad);
        const x4 = cx + innerRadius * Math.cos(startRad);
        const y4 = cy + innerRadius * Math.sin(startRad);

        const largeArc = (endAngle - startAngle) > 180 ? 1 : 0;

        if (innerRadius === 0) {
            // Pie chart (no inner radius)
            return `
                M ${cx} ${cy}
                L ${x1} ${y1}
                A ${outerRadius} ${outerRadius} 0 ${largeArc} 1 ${x2} ${y2}
                Z
            `;
        } else {
            // Donut chart (with inner radius)
            return `
                M ${x1} ${y1}
                A ${outerRadius} ${outerRadius} 0 ${largeArc} 1 ${x2} ${y2}
                L ${x3} ${y3}
                A ${innerRadius} ${innerRadius} 0 ${largeArc} 0 ${x4} ${y4}
                Z
            `;
        }
    }

    /**
     * Create curved arc path (with bulge effect like FlowerRenderer)
     */
    createCurvedArcPath(cx, cy, outerRadius, innerRadius, startAngle, endAngle, bulge = 0) {
        const startRad = startAngle * Math.PI / 180;
        const endRad = endAngle * Math.PI / 180;

        const x1 = cx + outerRadius * Math.cos(startRad);
        const y1 = cy + outerRadius * Math.sin(startRad);
        const x2 = cx + outerRadius * Math.cos(endRad);
        const y2 = cy + outerRadius * Math.sin(endRad);
        const x3 = cx + innerRadius * Math.cos(endRad);
        const y3 = cy + innerRadius * Math.sin(endRad);
        const x4 = cx + innerRadius * Math.cos(startRad);
        const y4 = cy + innerRadius * Math.sin(startRad);

        const largeArc = (endAngle - startAngle) > 180 ? 1 : 0;

        return `
            M ${x1} ${y1}
            A ${outerRadius} ${outerRadius} 0 ${largeArc} 1 ${x2} ${y2}
            L ${x3} ${y3}
            A ${innerRadius} ${innerRadius} 0 ${largeArc} 0 ${x4} ${y4}
            Z
        `;
    }

    /**
     * Create defs for filters and gradients
     */
    createDefs() {
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');

        // Inner shadow filter
        const innerShadowFilter = document.createElementNS('http://www.w3.org/2000/svg', 'filter');
        innerShadowFilter.setAttribute('id', `innerShadow-${this.container.id}`);

        const feGaussianBlur = document.createElementNS('http://www.w3.org/2000/svg', 'feGaussianBlur');
        feGaussianBlur.setAttribute('in', 'SourceAlpha');
        feGaussianBlur.setAttribute('stdDeviation', '3');
        feGaussianBlur.setAttribute('result', 'blur');

        const feOffset = document.createElementNS('http://www.w3.org/2000/svg', 'feOffset');
        feOffset.setAttribute('in', 'blur');
        feOffset.setAttribute('dx', '0');
        feOffset.setAttribute('dy', '2');
        feOffset.setAttribute('result', 'offsetBlur');

        const feFlood = document.createElementNS('http://www.w3.org/2000/svg', 'feFlood');
        feFlood.setAttribute('flood-color', '#000000');
        feFlood.setAttribute('flood-opacity', '0.15');
        feFlood.setAttribute('result', 'color');

        const feComposite1 = document.createElementNS('http://www.w3.org/2000/svg', 'feComposite');
        feComposite1.setAttribute('in', 'color');
        feComposite1.setAttribute('in2', 'offsetBlur');
        feComposite1.setAttribute('operator', 'in');
        feComposite1.setAttribute('result', 'shadow');

        const feComposite2 = document.createElementNS('http://www.w3.org/2000/svg', 'feComposite');
        feComposite2.setAttribute('in', 'shadow');
        feComposite2.setAttribute('in2', 'SourceAlpha');
        feComposite2.setAttribute('operator', 'in');
        feComposite2.setAttribute('result', 'innerShadow');

        const feMerge = document.createElementNS('http://www.w3.org/2000/svg', 'feMerge');
        const feMergeNode1 = document.createElementNS('http://www.w3.org/2000/svg', 'feMergeNode');
        feMergeNode1.setAttribute('in', 'SourceGraphic');
        const feMergeNode2 = document.createElementNS('http://www.w3.org/2000/svg', 'feMergeNode');
        feMergeNode2.setAttribute('in', 'innerShadow');

        feMerge.appendChild(feMergeNode1);
        feMerge.appendChild(feMergeNode2);

        innerShadowFilter.appendChild(feGaussianBlur);
        innerShadowFilter.appendChild(feOffset);
        innerShadowFilter.appendChild(feFlood);
        innerShadowFilter.appendChild(feComposite1);
        innerShadowFilter.appendChild(feComposite2);
        innerShadowFilter.appendChild(feMerge);

        defs.appendChild(innerShadowFilter);

        return defs;
    }

    /**
     * Create center circle/corona
     */
    createCenterCircle(radius, color = 'white', opacity = 0.92) {
        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.setAttribute('cx', this.options.centerX);
        circle.setAttribute('cy', this.options.centerY);
        circle.setAttribute('r', radius);
        circle.setAttribute('fill', color);
        circle.setAttribute('fill-opacity', opacity);
        circle.setAttribute('stroke', 'rgba(156, 163, 175, 0.6)');
        circle.setAttribute('stroke-width', '3');
        circle.setAttribute('filter', `url(#innerShadow-${this.container.id})`);

        return circle;
    }

    /**
     * Create center text elements
     */
    createCenterText(value, label) {
        const centerGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        centerGroup.classList.add('center-group');

        const countText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        countText.setAttribute('x', this.options.centerX);
        countText.setAttribute('y', this.options.centerY - 10);
        countText.setAttribute('text-anchor', 'middle');
        countText.setAttribute('dominant-baseline', 'middle');
        countText.classList.add('center-count');
        countText.textContent = value;

        const labelText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        labelText.setAttribute('x', this.options.centerX);
        labelText.setAttribute('y', this.options.centerY + 22);
        labelText.setAttribute('text-anchor', 'middle');
        labelText.setAttribute('dominant-baseline', 'middle');
        labelText.classList.add('center-label');
        labelText.textContent = label;

        centerGroup.appendChild(countText);
        centerGroup.appendChild(labelText);

        return centerGroup;
    }

    /**
     * Show a "No data" placeholder when the chart has nothing to render.
     */
    showNoData() {
        if (!this.container) return;
        this.container.innerHTML = '';
        const isDark = document.body.classList.contains('dark-mode');
        const msg = document.createElement('div');
        msg.style.cssText = `width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:${isDark ? '#6B7280' : '#9CA3AF'};font-size:13px;font-weight:500;letter-spacing:0.01em;`;
        msg.textContent = 'No data available';
        this.container.appendChild(msg);
    }

    /**
     * Destroy chart and clean up
     */
    destroy() {
        if (this.container) {
            this.container.innerHTML = '';
        }
    }
}

// Export for module systems or global scope
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNASVGChartBase;
}

// Make available globally for browser usage
if (typeof window !== 'undefined') {
    window.DotDNASVGChartBase = DotDNASVGChartBase;
}
