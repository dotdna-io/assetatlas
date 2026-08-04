class DotDNAAnimatedRadialBar extends DotDNASVGChartBase {
    constructor(containerId, options = {}) {
        super(containerId, options);

        this.options = {
            data: options.data || null,
            centerX: options.centerX || 150,
            centerY: options.centerY || 150,
            maxRadius: options.maxRadius || 120,
            minRadius: options.minRadius || 40,
            showLegend: options.showLegend !== false,
            animationDuration: options.animationDuration || 1500,
            ...options
        };

        this.layoutSize = options.layoutSize || '2x2';
        this.svg = null;
        this.bars = [];
        this.legend = null;

        if (this.options.data) {
            this.render();
        } else {
            this.showNoData();
        }
    }

    generateDemoData() {
        return [
            { name: 'Critical', value: 85, gradient: ['#ef4444', '#dc2626'] },
            { name: 'High', value: 65, gradient: ['#f97316', '#ea580c'] },
            { name: 'Medium', value: 45, gradient: ['#eab308', '#ca8a04'] },
            { name: 'Low', value: 25, gradient: ['#3b82f6', '#2563eb'] }
        ];
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
        this.container.style.width = '100%';
        this.container.style.height = '100%';
        this.container.style.display = 'flex';
        this.container.style.alignItems = 'center';
        this.container.style.justifyContent = 'center';
        this.container.style.position = 'relative';

        // Detect layout size
        this.layoutSize = this.detectLayoutSize();
        const is3x2 = this.layoutSize === '3x2';

        // Create wrapper for layout
        const wrapper = document.createElement('div');

        if (is3x2) {
            // Use CSS grid for 3x2 to lock the layout
            wrapper.style.display = 'grid';
            wrapper.style.gridTemplateColumns = '1fr auto';
            wrapper.style.gridTemplateRows = '1fr';
            wrapper.style.gap = '24px';
            wrapper.style.alignItems = 'start';
            wrapper.style.paddingTop = '25px';  // Move chart down more
        } else {
            // Use flex for 2x2
            wrapper.style.display = 'flex';
            wrapper.style.flexDirection = 'column';
            wrapper.style.alignItems = 'center';
            wrapper.style.justifyContent = 'center';
            wrapper.style.gap = '0px';  // No gap, legend uses negative margin instead
        }

        wrapper.style.width = '100%';
        wrapper.style.height = '100%';

        // Create SVG container
        const svgContainer = document.createElement('div');
        svgContainer.style.flex = is3x2 ? '0 0 auto' : '1';
        svgContainer.style.display = 'flex';
        svgContainer.style.alignItems = 'center';
        svgContainer.style.justifyContent = 'center';

        // Adjust chart size for 3x2 layout
        if (is3x2) {
            svgContainer.style.flex = '1';
        }

        // Create SVG
        this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        this.svg.setAttribute('viewBox', `0 0 300 300`);
        this.svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
        this.svg.style.overflow = 'visible';
        this.svg.style.width = is3x2 ? '67%' : '100%';  // Match pie sizing
        this.svg.style.height = is3x2 ? '67%' : '100%';

        // Create defs with gradients
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
        this.options.data.forEach((item, index) => {
            const gradient = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
            gradient.setAttribute('id', `radialGradient${index}`);
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

        // Draw radial bars
        this.drawRadialBars();

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
        this.animateBars();
    }

    drawRadialBars() {
        const is3x2 = this.layoutSize === '3x2';
        const centerX = 150;
        const centerY = is3x2 ? 150 : 160;  // Center in 3x2, moved down in 2x2
        const maxRadius = 108;  // Reduced by 10% (120 * 0.9 = 108)
        const innerRadius = 20;
        const barCount = this.options.data.length;
        const totalAngle = (340 / 360) * (Math.PI * 2);  // 340 degrees instead of 360
        const startOffset = ((360 - 340) / 360) * (Math.PI * 2) / 2;  // Center the gap at top
        const angleSlice = totalAngle / barCount;
        const sliceGap = 0.02; // Gap between slices in radians

        // Draw grid circles with labels
        const levels = 5;
        for (let i = 0; i < levels; i++) {
            const radius = innerRadius + ((i + 1) / levels) * (maxRadius - innerRadius);
            const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            circle.setAttribute('cx', centerX);
            circle.setAttribute('cy', centerY);
            circle.setAttribute('r', radius);
            circle.setAttribute('fill', 'none');
            circle.setAttribute('stroke', 'rgba(0, 0, 0, 0.15)');
            circle.setAttribute('stroke-width', '1.5');
            circle.setAttribute('stroke-dasharray', '2,4');
            circle.classList.add('radial-bg');
            this.svg.appendChild(circle);

            // Add percentage labels
            const percentage = ((i + 1) / levels) * 100;
            const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            label.setAttribute('x', centerX);
            label.setAttribute('y', centerY - radius - 5);
            label.setAttribute('fill', 'rgba(0, 0, 0, 0.4)');
            label.setAttribute('font-size', '10');
            label.setAttribute('text-anchor', 'middle');
            label.setAttribute('dominant-baseline', 'middle');
            label.classList.add('radial-label');
            label.textContent = `${Math.round(percentage)}%`;
            this.svg.appendChild(label);
        }

        // Draw grid lines
        this.options.data.forEach((item, index) => {
            const angle = startOffset + index * angleSlice - Math.PI / 2 + angleSlice / 2;
            const x = centerX + Math.cos(angle) * maxRadius;
            const y = centerY + Math.sin(angle) * maxRadius;
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line.setAttribute('x1', centerX + Math.cos(angle) * innerRadius);
            line.setAttribute('y1', centerY + Math.sin(angle) * innerRadius);
            line.setAttribute('x2', x);
            line.setAttribute('y2', y);
            line.setAttribute('stroke', 'rgba(0, 0, 0, 0.08)');
            line.setAttribute('stroke-width', '1');
            line.setAttribute('stroke-dasharray', '2,4');
            line.classList.add('radial-bg');
            this.svg.appendChild(line);
        });

        // Draw radial bar slices
        this.options.data.forEach((item, index) => {
            const startAngle = startOffset + index * angleSlice - Math.PI / 2 + sliceGap / 2;
            const endAngle = startOffset + (index + 1) * angleSlice - Math.PI / 2 - sliceGap / 2;
            const outerRadius = innerRadius + ((item.value / 100) * (maxRadius - innerRadius));

            // Create a group for each slice to handle translation
            const sliceGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            sliceGroup.style.transition = 'transform 0.3s ease';
            sliceGroup.setAttribute('transform', 'translate(0, 0)');

            // Calculate midpoint angle for hover effect
            const midAngle = (startAngle + endAngle) / 2;

            // Create slice path
            const path = this.createSlicePath(centerX, centerY, innerRadius, outerRadius, startAngle, endAngle);

            path.setAttribute('fill', `url(#radialGradient${index})`);
            path.setAttribute('stroke', 'transparent');
            path.setAttribute('stroke-width', '2');
            path.style.filter = 'drop-shadow(0 2px 4px rgba(0, 0, 0, 0.1))';
            path.style.transformOrigin = `${centerX}px ${centerY}px`;
            path.style.transform = 'scale(0)';
            path.style.transition = `transform ${this.options.animationDuration}ms cubic-bezier(0.4, 0, 0.2, 1), filter 0.3s ease`;
            path.style.cursor = 'pointer';

            // Add tooltip and hover effect listeners
            path.addEventListener('mouseenter', (e) => {
                // Pop-out effect like pie chart
                const offsetDistance = 10;
                const offsetX = Math.cos(midAngle) * offsetDistance;
                const offsetY = Math.sin(midAngle) * offsetDistance;
                sliceGroup.setAttribute('transform', `translate(${offsetX}, ${offsetY})`);
                path.style.filter = 'drop-shadow(0 4px 8px rgba(0, 0, 0, 0.2))';

                this.showTooltip(`${item.name}: ${item.value}%`, e);
            });

            path.addEventListener('mousemove', (e) => {
                this.showTooltip(`${item.name}: ${item.value}%`, e);
            });

            path.addEventListener('mouseleave', () => {
                // Reset position
                sliceGroup.setAttribute('transform', 'translate(0, 0)');
                path.style.filter = 'drop-shadow(0 2px 4px rgba(0, 0, 0, 0.1))';

                this.hideTooltip();
            });

            sliceGroup.appendChild(path);
            this.svg.appendChild(sliceGroup);
            this.bars.push({ path, item, index, group: sliceGroup });
        });

        // Center circle
        const centerCircle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        centerCircle.setAttribute('cx', centerX);
        centerCircle.setAttribute('cy', centerY);
        centerCircle.setAttribute('r', innerRadius);
        centerCircle.setAttribute('fill', 'white');
        centerCircle.setAttribute('stroke', 'rgba(0, 0, 0, 0.1)');
        centerCircle.setAttribute('stroke-width', '2');
        centerCircle.classList.add('radial-center');
        this.svg.appendChild(centerCircle);
    }

    createSlicePath(centerX, centerY, innerRadius, outerRadius, startAngle, endAngle) {
        const x1 = centerX + Math.cos(startAngle) * innerRadius;
        const y1 = centerY + Math.sin(startAngle) * innerRadius;
        const x2 = centerX + Math.cos(startAngle) * outerRadius;
        const y2 = centerY + Math.sin(startAngle) * outerRadius;
        const x3 = centerX + Math.cos(endAngle) * outerRadius;
        const y3 = centerY + Math.sin(endAngle) * outerRadius;
        const x4 = centerX + Math.cos(endAngle) * innerRadius;
        const y4 = centerY + Math.sin(endAngle) * innerRadius;

        const largeArcFlag = endAngle - startAngle > Math.PI ? 1 : 0;

        const pathData = `
            M ${x1} ${y1}
            L ${x2} ${y2}
            A ${outerRadius} ${outerRadius} 0 ${largeArcFlag} 1 ${x3} ${y3}
            L ${x4} ${y4}
            A ${innerRadius} ${innerRadius} 0 ${largeArcFlag} 0 ${x1} ${y1}
            Z
        `;

        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', pathData);
        return path;
    }

    animateBars() {
        // Animate bars with stagger
        this.bars.forEach((bar, index) => {
            setTimeout(() => {
                bar.path.style.transform = 'scale(1)';
            }, index * 100);
        });

        // Show legend after animation
        if (this.legend) {
            setTimeout(() => {
                this.legend.style.opacity = '1';
            }, this.options.animationDuration);
        }
    }

    createContainerTooltip() {
        this.tooltip = document.createElement('div');
        this.tooltip.style.cssText = `
            position: absolute !important;
            background-color: rgba(31, 41, 55, 0.95) !important;
            color: white !important;
            padding: 4px 8px !important;
            border-radius: 3px !important;
            font-size: 11px !important;
            font-weight: 400 !important;
            font-family: system-ui, -apple-system, sans-serif !important;
            pointer-events: none !important;
            opacity: 0 !important;
            transition: opacity 0.15s !important;
            z-index: 1000 !important;
            white-space: nowrap !important;
            box-shadow: 0 1px 4px rgba(0, 0, 0, 0.2) !important;
            line-height: 1 !important;
            letter-spacing: 0.01em !important;
            margin: 0 !important;
            border: none !important;
            max-width: none !important;
            min-width: auto !important;
            width: auto !important;
            height: auto !important;
        `;
        this.container.appendChild(this.tooltip);
    }

    showTooltip(text, event) {
        if (!this.tooltip) return;
        this.tooltip.textContent = text;
        this.tooltip.style.opacity = '1';

        const containerRect = this.container.getBoundingClientRect();
        this.tooltip.style.left = (event.clientX - containerRect.left + 10) + 'px';
        this.tooltip.style.top = (event.clientY - containerRect.top - 10) + 'px';
    }

    hideTooltip() {
        if (!this.tooltip) return;
        this.tooltip.style.opacity = '0';
    }

    createLegend(is3x2) {
        const legend = document.createElement('div');
        legend.style.display = 'flex';
        legend.style.flexDirection = is3x2 ? 'column' : 'row';
        legend.style.flexWrap = is3x2 ? 'nowrap' : 'wrap';
        legend.style.gap = is3x2 ? '12px' : '16px';
        legend.style.alignItems = is3x2 ? 'flex-start' : 'center';
        legend.style.justifyContent = is3x2 ? 'center' : 'center';
        legend.style.padding = is3x2 ? '0' : '0';
        legend.style.flex = is3x2 ? '0 0 auto' : 'none';
        legend.style.minWidth = is3x2 ? '140px' : 'auto';
        legend.style.marginLeft = is3x2 ? '-60px' : '0';  // Negative margin to move legend left
        legend.style.marginTop = is3x2 ? '0' : '-20px';  // Negative margin for 2x2 to bring legend closer
        legend.style.alignSelf = is3x2 ? 'center' : 'auto';  // Center legend vertically in 3x2

        this.options.data.forEach((item, index) => {
            const legendItem = document.createElement('div');
            legendItem.style.display = 'flex';
            legendItem.style.alignItems = 'center';
            legendItem.style.gap = '8px';
            legendItem.style.userSelect = 'none';

            // Color indicator
            const colorBox = document.createElement('div');
            colorBox.style.width = '12px';
            colorBox.style.height = '12px';
            colorBox.style.borderRadius = '2px';
            colorBox.style.background = `linear-gradient(135deg, ${item.gradient[0]}, ${item.gradient[1]})`;
            colorBox.style.flexShrink = '0';

            // Label
            const label = document.createElement('div');
            label.style.fontSize = '13px';
            label.style.fontWeight = '500';
            label.style.whiteSpace = 'nowrap';
            label.textContent = item.name;

            legendItem.appendChild(colorBox);
            legendItem.appendChild(label);
            legend.appendChild(legendItem);
        });

        return legend;
    }

    updateData(newData) {
        this.options.data = newData;
        this.render();
    }

    destroy() {
        this.container.innerHTML = '';
    }
}

// Export to window for wizard access
if (typeof window !== 'undefined') {
    window.DotDNAAnimatedRadialBar = DotDNAAnimatedRadialBar;
}
