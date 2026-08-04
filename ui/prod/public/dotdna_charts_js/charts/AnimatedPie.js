/**
 * AnimatedPie - Pie chart with flower-like animation growing from center
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 */

class DotDNAAnimatedPie extends DotDNASVGChartBase {
    constructor(containerId, options = {}) {
        super(containerId, options);

        this.options = {
            ...this.options,
            outerRadius: options.outerRadius || 210,
            petalGap: options.petalGap || 5,  // Gap between slices in degrees
            hoverScale: options.hoverScale || 1.08,
            animationDuration: options.animationDuration || 3500,
            data: options.data || null,
            showLegend: options.showLegend !== undefined ? options.showLegend : false,
            ...options
        };

        this.hoveredSegment = null;
        this.selectedSegment = null;

        // Only render if initial data provided; otherwise wait for setData()
        if (this.options.data) {
            this.render();
        } else {
            this.showNoData();
        }
    }

    generateRandomData() {
        const palette = window.ChartPalette ? window.ChartPalette.gradients : [
            { name: 'Red',    colors: ['#EF4444', '#DC2626'] },
            { name: 'Amber',  colors: ['#F59E0B', '#D97706'] },
            { name: 'Blue',   colors: ['#3B82F6', '#2563EB'] },
            { name: 'Green',  colors: ['#10B981', '#059669'] },
            { name: 'Purple', colors: ['#8B5CF6', '#7C3AED'] },
            { name: 'Teal',   colors: ['#06B6D4', '#0891B2'] },
            { name: 'Rose',   colors: ['#EC4899', '#DB2777'] },
            { name: 'Indigo', colors: ['#6366F1', '#4F46E5'] }
        ];

        let remaining = 100;
        return palette.map((entry, index) => {
            const isLast = index === palette.length - 1;
            const value = isLast
                ? remaining
                : Math.max(10, Math.min(remaining - (palette.length - index - 1) * 10, Math.random() * 30 + 15));

            remaining -= value;

            return {
                name: entry.name,
                gradient: entry.colors,
                value: Math.round(value),
                count: Math.floor(Math.random() * 900) + 100
            };
        });
    }

    calculatePath(startAngle, endAngle, radius) {
        // Add gap between slices — never wider than the slice itself, or the
        // inset edges would cross and the wedge would paint backwards.
        const sweepInRadians = ((endAngle - startAngle) * Math.PI) / 180;
        const gapInRadians = Math.min(
            (this.options.petalGap * Math.PI) / 180,
            Math.max(sweepInRadians, 0)
        );
        const startAngleRad = (startAngle * Math.PI) / 180 + gapInRadians / 2;
        const endAngleRad = (endAngle * Math.PI) / 180 - gapInRadians / 2;

        const cx = this.options.centerX;
        const cy = this.options.centerY;

        const x1 = cx + Math.cos(startAngleRad) * radius;
        const y1 = cy + Math.sin(startAngleRad) * radius;
        const x2 = cx + Math.cos(endAngleRad) * radius;
        const y2 = cy + Math.sin(endAngleRad) * radius;

        const largeArcFlag = endAngleRad - startAngleRad > Math.PI ? 1 : 0;

        // Pie chart path - starts from center
        return `
            M ${cx} ${cy}
            L ${x1} ${y1}
            A ${radius} ${radius} 0 ${largeArcFlag} 1 ${x2} ${y2}
            Z
        `;
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
        const aspectRatio = width / height;

        // Determine if this is 2x2 (square-ish) or 3x2 (wide)
        // 3x2 aspect ratio is 1.5, 2x2 is 1.0
        // Use 1.1 threshold to keep 3x2 layout even when narrow
        return aspectRatio > 1.1 ? '3x2' : '2x2';
    }

    render() {
        this.container.innerHTML = '';
        this.container.className = 'dotdna-chart';
        this.container.style.background = 'transparent';

        // Detect layout size
        this.layoutSize = this.detectLayoutSize();
        const is3x2 = this.layoutSize === '3x2';

        // Create wrapper for layout
        const wrapper = document.createElement('div');

        // Always use flex column — chart on top, legend below
        wrapper.style.display = 'flex';
        wrapper.style.flexDirection = 'column';
        wrapper.style.alignItems = 'center';
        wrapper.style.justifyContent = 'center';
        wrapper.style.gap = '0px';

        wrapper.style.width = '100%';
        wrapper.style.height = '100%';

        // Create SVG container
        const svgContainer = document.createElement('div');
        svgContainer.style.flex = '1';
        svgContainer.style.display = 'flex';
        svgContainer.style.alignItems = 'center';
        svgContainer.style.justifyContent = 'center';
        svgContainer.style.minHeight = '0';

        // Create SVG
        this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        this.svg.setAttribute('viewBox', `0 0 ${this.options.width} ${this.options.height}`);
        this.svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
        this.svg.style.overflow = 'visible';
        this.svg.style.width = '100%';
        this.svg.style.height = '100%';

        // Safety net: when the cursor leaves the SVG entirely, hide the
        // tooltip. Per-slice mouseleave can miss this if the path is removed
        // mid-hover (animation re-render) or if the mouse skips between slices.
        this.svg.addEventListener('mouseleave', () => this.hideTooltip());

        // Create defs with gradients
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');

        this.options.data.forEach((item, index) => {
            const gradient = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
            gradient.setAttribute('id', `pie-gradient-${this.container.id}-${index}`);
            gradient.setAttribute('x1', '0%');
            gradient.setAttribute('y1', '0%');
            gradient.setAttribute('x2', '100%');
            gradient.setAttribute('y2', '100%');

            const stop1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
            stop1.setAttribute('offset', '0%');
            stop1.setAttribute('stop-color', item.gradient[0]);

            const stop2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
            stop2.setAttribute('offset', '100%');
            stop2.setAttribute('stop-color', item.gradient[1]);

            gradient.appendChild(stop1);
            gradient.appendChild(stop2);
            defs.appendChild(gradient);
        });

        this.svg.appendChild(defs);

        // Create main group for slices
        this.slicesGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        this.svg.appendChild(this.slicesGroup);

        svgContainer.appendChild(this.svg);
        wrapper.appendChild(svgContainer);

        // Create legend if enabled - initially hidden
        if (this.options.showLegend) {
            this.legend = this.createLegend(is3x2);
            this.legend.style.opacity = '0';
            this.legend.style.transition = 'opacity 0.6s ease';
            wrapper.appendChild(this.legend);
        }

        this.container.appendChild(wrapper);

        // Create tooltip
        this.createContainerTooltip();

        // Start animation
        this.animateSlices();
    }

    createLegend(is3x2) {
        const legend = document.createElement('div');
        legend.style.display = 'flex';
        legend.style.flexDirection = 'row';
        legend.style.flexWrap = 'wrap';
        legend.style.gap = '2px 10px';
        legend.style.alignItems = 'center';
        legend.style.justifyContent = 'center';
        legend.style.padding = '0 12px 6px';
        legend.style.flex = '0 0 auto';

        this.options.data.forEach((item, index) => {
            const legendItem = document.createElement('div');
            legendItem.style.display = 'flex';
            legendItem.style.alignItems = 'center';
            legendItem.style.gap = '8px';
            legendItem.style.userSelect = 'none';

            // Color indicator
            const colorBox = document.createElement('div');
            colorBox.style.width = '8px';
            colorBox.style.height = '8px';
            colorBox.style.borderRadius = '2px';
            colorBox.style.background = `linear-gradient(135deg, ${item.gradient[0]}, ${item.gradient[1]})`;
            colorBox.style.flexShrink = '0';

            // Label
            const label = document.createElement('div');
            label.style.fontSize = '11px';
            label.style.fontWeight = '500';
            label.style.whiteSpace = 'nowrap';
            label.textContent = item.name;

            legendItem.appendChild(colorBox);
            legendItem.appendChild(label);
            legend.appendChild(legendItem);
        });

        return legend;
    }

    createContainerTooltip() {
        this.tooltip = document.createElement('div');
        this.tooltip.style.position = 'fixed';
        this.tooltip.style.padding = '8px 12px';
        this.tooltip.style.borderRadius = '8px';
        this.tooltip.style.fontSize = '12px';
        this.tooltip.style.pointerEvents = 'none';
        this.tooltip.style.opacity = '0';
        this.tooltip.style.transition = 'opacity 0.2s';
        this.tooltip.style.zIndex = '10000';
        this.tooltip.style.lineHeight = '1.5';
        this.tooltip.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        this._applyTooltipTheme();
        document.body.appendChild(this.tooltip);
    }

    _applyTooltipTheme() {
        const isDark = document.body.classList.contains('dark-mode');
        if (isDark) {
            this.tooltip.style.backgroundColor = '#303034';
            this.tooltip.style.color = '#f0f0f0';
            this.tooltip.style.border = '1px solid rgba(255,255,255,0.1)';
        } else {
            this.tooltip.style.backgroundColor = '#ffffff';
            this.tooltip.style.color = '#1e293b';
            this.tooltip.style.border = '1px solid rgba(0,0,0,0.08)';
        }
    }

    showTooltip(text, event) {
        if (!this.tooltip) return;
        this._applyTooltipTheme();
        this.tooltip.innerHTML = text;
        this.tooltip.style.opacity = '1';
        this.tooltip.style.left = (event.clientX + 12) + 'px';
        this.tooltip.style.top = (event.clientY - 10) + 'px';
    }

    hideTooltip() {
        if (!this.tooltip) return;
        this.tooltip.style.opacity = '0';
    }

    animateSlices() {
        const startTime = Date.now();
        const duration = this.options.animationDuration;

        const animate = () => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);

            // Ease-out cubic
            const easedProgress = 1 - Math.pow(1 - progress, 3);

            this.updateSlices(easedProgress);

            // Fade in legend during animation
            if (progress > 0.2) {  // Start showing at 20% progress
                if (this.legend) {
                    this.legend.style.opacity = '1';
                }
            }

            if (progress < 1) {
                requestAnimationFrame(animate);
            }
        };

        animate();
    }

    updateSlices(animationProgress = 1) {
        // Hide tooltip — paths are about to be wiped and recreated, so any
        // in-flight hover state would be orphaned (no mouseleave will fire on
        // a removed element).
        this.hideTooltip();
        this.slicesGroup.innerHTML = '';
        this.sliceGroups = [];  // Store references to slice groups

        let currentAngle = -90; // Start from top
        const numSlices = this.options.data.length;
        const sweeps = this.computeSliceSweeps(this.options.petalGap);

        this.options.data.forEach((item, index) => {
            const startAngle = currentAngle;
            const sweepAngle = sweeps[index];
            const endAngle = currentAngle + sweepAngle;
            currentAngle += sweepAngle;

            // Calculate progress for this specific slice (sequential animation)
            // Each slice gets 1/numSlices of the total time
            const sliceStart = index / numSlices;
            const sliceEnd = (index + 1) / numSlices;
            const sliceProgress = Math.max(0, Math.min(1, (animationProgress - sliceStart) / (sliceEnd - sliceStart)));

            // Animate radius from 0 to full size (flower effect) - one slice at a time
            const currentRadius = this.options.outerRadius * sliceProgress;

            if (sweepAngle > 0 && currentRadius > 0) {
                // Calculate mid angle for offset direction
                const midAngle = ((startAngle + endAngle) / 2) * Math.PI / 180;

                // Create slice group
                const sliceGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
                sliceGroup.style.transition = 'transform 0.3s ease';
                sliceGroup.setAttribute('transform', 'translate(0, 0)');
                sliceGroup.dataset.index = index;
                sliceGroup.dataset.midAngle = midAngle;

                const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                path.setAttribute('d', this.calculatePath(
                    startAngle,
                    endAngle,
                    currentRadius
                ));
                path.setAttribute('fill', `url(#pie-gradient-${this.container.id}-${index})`);
                path.style.cursor = 'pointer';
                path.style.transition = 'filter 0.3s ease';
                path.style.filter = 'drop-shadow(0 2px 4px rgba(0, 0, 0, 0.15))';

                // Event listeners
                path.addEventListener('mouseenter', (e) => {
                    this.hoveredSegment = index;
                    this.updateHoverState(index, true);
                    this.showTooltip(`<strong style="color:${item.gradient[0]}">${item.name}</strong><br><span style="opacity:0.7">${item.count.toLocaleString()} &middot; ${item.value}%</span>`, e);
                });

                path.addEventListener('mousemove', (e) => {
                    this.showTooltip(`<strong style="color:${item.gradient[0]}">${item.name}</strong><br><span style="opacity:0.7">${item.count.toLocaleString()} &middot; ${item.value}%</span>`, e);
                });

                path.addEventListener('mouseleave', () => {
                    this.hoveredSegment = null;
                    this.updateHoverState(index, false);
                    this.hideTooltip();
                });

                sliceGroup.appendChild(path);
                this.slicesGroup.appendChild(sliceGroup);
                this.sliceGroups[index] = { group: sliceGroup, path: path, midAngle: midAngle };
            }
        });
    }

    updateHoverState(index, isHovered) {
        if (!this.sliceGroups || !this.sliceGroups[index]) return;

        const { group, path, midAngle } = this.sliceGroups[index];

        if (isHovered) {
            const offsetDistance = 10;
            const offsetX = Math.cos(midAngle) * offsetDistance;
            const offsetY = Math.sin(midAngle) * offsetDistance;
            group.setAttribute('transform', `translate(${offsetX}, ${offsetY})`);
            path.style.filter = 'drop-shadow(0 4px 12px rgba(0, 0, 0, 0.3)) brightness(1.1)';
        } else {
            group.setAttribute('transform', 'translate(0, 0)');
            path.style.filter = 'drop-shadow(0 2px 4px rgba(0, 0, 0, 0.15))';
        }
    }

    update(newOptions) {
        this.options = { ...this.options, ...newOptions };
        this.hoveredSegment = null;
        this.selectedSegment = null;

        this.render();
    }

    destroy() {
        if (this.tooltip && this.tooltip.parentNode) {
            this.tooltip.parentNode.removeChild(this.tooltip);
        }
        if (this.container) {
            this.container.innerHTML = '';
        }
    }
}

// Export for module systems or global scope
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNAAnimatedPie;
}

// Make available globally for browser usage
if (typeof window !== 'undefined') {
    window.DotDNAAnimatedPie = DotDNAAnimatedPie;
}
