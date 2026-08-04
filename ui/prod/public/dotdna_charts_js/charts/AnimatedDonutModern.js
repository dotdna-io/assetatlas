/**
 * AnimatedDonutModern - Modern donut chart with rotating animation and flower-petal style
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 */

class DotDNAAnimatedDonutModern extends DotDNASVGChartBase {
    constructor(containerId, options = {}) {
        super(containerId, options);

        this.options = {
            ...this.options,
            outerRadius: 210,
            innerRadius: 145,
            petalGap: options.petalGap || 5,  // Gap between petals in degrees - increased
            hoverScale: options.hoverScale || 1.08,
            animationDuration: options.animationDuration || 2000,
            rotationDuration: options.rotationDuration || 3000,
            data: options.data || null,
            showLegend: options.showLegend !== undefined ? options.showLegend : false,
            ...options
        };

        this.hoveredSegment = null;
        this.selectedSegment = null;
        this.rotation = 0;
        this.isRotating = true;
        this.maxRotation = 45;  // Stop at 45 degrees

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

    calculatePath(startAngle, endAngle, innerR, outerR) {
        // Skip gap when there's only one segment (full circle). Never let the
        // gap exceed the segment's own sweep, or the inset edges would cross
        // and the wedge would paint backwards.
        const segmentCount = this.options.data.filter(d => d.value > 0).length;
        const sweepInRadians = ((endAngle - startAngle) * Math.PI) / 180;
        const gapInRadians = segmentCount <= 1 ? 0 : Math.min(
            (this.options.petalGap * Math.PI) / 180,
            Math.max(sweepInRadians, 0)
        );
        const startAngleRad = (startAngle * Math.PI) / 180 + gapInRadians / 2;
        const endAngleRad = (endAngle * Math.PI) / 180 - gapInRadians / 2;

        const cx = this.options.centerX;
        const cy = this.options.centerY;

        // SVG arcs cannot draw a full 360° — split into two semicircles
        const sweep = endAngleRad - startAngleRad;
        if (sweep >= Math.PI * 2 - 0.001) {
            const midRad = startAngleRad + Math.PI;
            const ox1 = cx + Math.cos(startAngleRad) * outerR;
            const oy1 = cy + Math.sin(startAngleRad) * outerR;
            const ox2 = cx + Math.cos(midRad) * outerR;
            const oy2 = cy + Math.sin(midRad) * outerR;
            const ix1 = cx + Math.cos(startAngleRad) * innerR;
            const iy1 = cy + Math.sin(startAngleRad) * innerR;
            const ix2 = cx + Math.cos(midRad) * innerR;
            const iy2 = cy + Math.sin(midRad) * innerR;
            return `
                M ${ox1} ${oy1}
                A ${outerR} ${outerR} 0 1 1 ${ox2} ${oy2}
                A ${outerR} ${outerR} 0 1 1 ${ox1} ${oy1}
                Z
                M ${ix1} ${iy1}
                A ${innerR} ${innerR} 0 1 0 ${ix2} ${iy2}
                A ${innerR} ${innerR} 0 1 0 ${ix1} ${iy1}
                Z
            `;
        }

        const x1 = cx + Math.cos(startAngleRad) * outerR;
        const y1 = cy + Math.sin(startAngleRad) * outerR;
        const x2 = cx + Math.cos(endAngleRad) * outerR;
        const y2 = cy + Math.sin(endAngleRad) * outerR;
        const x3 = cx + Math.cos(endAngleRad) * innerR;
        const y3 = cy + Math.sin(endAngleRad) * innerR;
        const x4 = cx + Math.cos(startAngleRad) * innerR;
        const y4 = cy + Math.sin(startAngleRad) * innerR;

        const largeArcFlag = sweep > Math.PI ? 1 : 0;

        return `
            M ${x1} ${y1}
            A ${outerR} ${outerR} 0 ${largeArcFlag} 1 ${x2} ${y2}
            L ${x3} ${y3}
            A ${innerR} ${innerR} 0 ${largeArcFlag} 0 ${x4} ${y4}
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
        // tooltip. Per-segment mouseleave can miss this if the path is
        // removed mid-hover (animation re-render).
        this.svg.addEventListener('mouseleave', () => this.hideTooltip());

        // Create defs with gradients
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');

        this.options.data.forEach((item, index) => {
            const gradient = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
            gradient.setAttribute('id', `donut-gradient-${this.container.id}-${index}`);
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

        // Create rotating group
        this.rotatingGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        this.svg.appendChild(this.rotatingGroup);

        // Create center text (non-rotating) - initially hidden
        const totalCount = this.options.data.reduce((sum, item) => sum + item.count, 0);
        this.centerGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        this.centerGroup.style.opacity = '0';
        this.centerGroup.style.transition = 'opacity 0.6s ease';

        const centerText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        centerText.setAttribute('x', this.options.centerX);
        centerText.setAttribute('y', this.options.centerY - 12);
        centerText.setAttribute('text-anchor', 'middle');
        centerText.setAttribute('dominant-baseline', 'middle');
        centerText.setAttribute('font-size', '48');
        centerText.setAttribute('font-weight', '700');
        centerText.setAttribute('class', 'donut-center-text');
        centerText.textContent = totalCount.toLocaleString();

        const centerLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        centerLabel.setAttribute('x', this.options.centerX);
        centerLabel.setAttribute('y', this.options.centerY + 26);
        centerLabel.setAttribute('text-anchor', 'middle');
        centerLabel.setAttribute('dominant-baseline', 'middle');
        centerLabel.setAttribute('font-size', '13');
        centerLabel.setAttribute('font-weight', '500');
        centerLabel.setAttribute('letter-spacing', '0.08em');
        centerLabel.setAttribute('class', 'donut-center-label');
        centerLabel.textContent = 'TOTAL';

        this.centerGroup.appendChild(centerText);
        this.centerGroup.appendChild(centerLabel);
        this.svg.appendChild(this.centerGroup);

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

        // Start animations
        this.animateSegments();
        this.startRotation();
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
            legendItem.style.gap = '5px';
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

    animateSegments() {
        const startTime = Date.now();
        const duration = this.options.animationDuration;

        const animate = () => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);

            // Ease-out cubic
            const easedProgress = 1 - Math.pow(1 - progress, 3);

            this.updateSegments(easedProgress);

            // Fade in center text and legend during animation
            if (progress > 0.4) {  // Start showing at 40% progress
                if (this.centerGroup) {
                    this.centerGroup.style.opacity = '1';
                }
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

    updateSegments(animationProgress = 1) {
        // Hide tooltip — paths are about to be wiped and recreated, so any
        // in-flight hover state would be orphaned (no mouseleave will fire on
        // a removed element).
        this.hideTooltip();
        this.rotatingGroup.innerHTML = '';
        this.petalGroups = [];  // Store references to petal groups

        let currentAngle = -90; // Start from top
        const sweeps = this.computeSliceSweeps(this.options.petalGap);

        this.options.data.forEach((item, index) => {
            const startAngle = currentAngle;
            const sweepAngle = sweeps[index] * animationProgress;
            const endAngle = currentAngle + sweepAngle;
            currentAngle += sweeps[index];

            if (sweepAngle > 0) {
                // Calculate mid angle for offset direction
                const midAngle = ((startAngle + endAngle) / 2) * Math.PI / 180;

                // Create petal group
                const petalGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
                petalGroup.style.transition = 'transform 0.3s ease';
                petalGroup.setAttribute('transform', 'translate(0, 0)');
                petalGroup.dataset.index = index;
                petalGroup.dataset.midAngle = midAngle;

                const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                path.setAttribute('d', this.calculatePath(
                    startAngle,
                    endAngle,
                    this.options.innerRadius,
                    this.options.outerRadius
                ));
                path.setAttribute('fill', `url(#donut-gradient-${this.container.id}-${index})`);
                path.setAttribute('fill-rule', 'evenodd');
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

                petalGroup.appendChild(path);
                this.rotatingGroup.appendChild(petalGroup);
                this.petalGroups[index] = { group: petalGroup, path: path, midAngle: midAngle };
            }
        });
    }

    updateHoverState(index, isHovered) {
        if (!this.petalGroups || !this.petalGroups[index]) return;

        const { group, path, midAngle } = this.petalGroups[index];

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

    startRotation() {
        const rotate = () => {
            if (this.isRotating && this.rotation < this.maxRotation) {
                this.rotation += 0.3;
                this.rotatingGroup.setAttribute('transform',
                    `rotate(${this.rotation} ${this.options.centerX} ${this.options.centerY})`
                );
                this.rotationFrameId = requestAnimationFrame(rotate);
            } else if (this.rotation >= this.maxRotation) {
                // Stop at 45 degrees
                this.isRotating = false;
            } else if (!this.isRotating) {
                // Continue animation frame loop even when paused (for hover)
                this.rotationFrameId = requestAnimationFrame(rotate);
            }
        };

        rotate();
    }

    update(newOptions) {
        if (this.rotationFrameId) {
            cancelAnimationFrame(this.rotationFrameId);
        }

        this.options = { ...this.options, ...newOptions };
        this.hoveredSegment = null;
        this.selectedSegment = null;
        this.rotation = 0;
        this.isRotating = true;

        this.render();
    }

    destroy() {
        if (this.rotationFrameId) {
            cancelAnimationFrame(this.rotationFrameId);
        }
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
    module.exports = DotDNAAnimatedDonutModern;
}

// Make available globally for browser usage
if (typeof window !== 'undefined') {
    window.DotDNAAnimatedDonutModern = DotDNAAnimatedDonutModern;
}
