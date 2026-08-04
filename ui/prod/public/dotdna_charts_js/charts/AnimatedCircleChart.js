/**
 * DotDNA AnimatedCircleChart - Vanilla JavaScript
 * Concentric circles showing multiple metrics with smooth animations
 */
class DotDNAAnimatedCircleChart extends DotDNASVGChartBase {
    constructor(containerId, options = {}) {
        super(containerId, options);

        // Default options
        this.options = {
            data: options.data || null,
            centerX: options.centerX || 150,
            centerY: options.centerY || 150,
            baseRadius: options.baseRadius || 80,
            strokeWidth: options.strokeWidth || 12,
            gap: options.gap || 18,
            animationDuration: options.animationDuration || 1500,
            showLegend: options.showLegend !== false,
            ...options
        };

        this.isAnimating = false;
        this.animationFrameId = null;
        this.hoveredCircle = null;
        this.circleElements = [];

        if (this.options.data) {
            this.render();
            this.animate();
        } else {
            this.showNoData();
        }
    }

    generateDemoData() {
        const categories = [
            { name: 'Critical', gradient: ['#EF4444', '#DC2626'] },
            { name: 'High', gradient: ['#F59E0B', '#D97706'] },
            { name: 'Medium', gradient: ['#3B82F6', '#2563EB'] },
            { name: 'Low', gradient: ['#10B981', '#059669'] },
            { name: 'Info', gradient: ['#8B5CF6', '#7C3AED'] }
        ];

        return categories.map(category => ({
            ...category,
            current: Math.floor(Math.random() * 900) + 100,
            total: Math.floor(Math.random() * 8000) + 2000,
            percentage: Math.floor(Math.random() * 40) + 60
        }));
    }

    calculateAverageScore() {
        const sum = this.options.data.reduce((acc, item) => acc + item.percentage, 0);
        return Math.floor(sum / this.options.data.length);
    }

    calculateCirclePath(radius, percentage) {
        const circumference = 2 * Math.PI * radius;
        const dashArray = circumference;
        const dashOffset = circumference - (circumference * percentage) / 100;
        return { dashArray, dashOffset };
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

        if (is3x2) {
            // Use CSS grid for 3x2 to lock the layout
            wrapper.style.display = 'grid';
            wrapper.style.gridTemplateColumns = '1fr auto';
            wrapper.style.gridTemplateRows = '1fr';
            wrapper.style.gap = '24px';
            wrapper.style.alignItems = 'start';  // Align to start like donut/pie
            wrapper.style.paddingTop = '80px';  // Move chart down more
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
        svgContainer.style.position = 'relative';

        // Adjust chart size for 3x2 layout
        if (is3x2) {
            svgContainer.style.flex = '1';
        }

        // Create SVG
        const svgWidth = this.options.centerX * 2;
        const svgHeight = this.options.centerY * 2;
        this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        this.svg.setAttribute('viewBox', `0 0 ${svgWidth} ${svgHeight}`);
        this.svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
        this.svg.style.overflow = 'visible';
        // Scale to match donut/pie size - adjusted for better alignment
        this.svg.style.width = is3x2 ? '46%' : '80%';  // Increased 3x2 by 20% (38% * 1.2 = 45.6%)
        this.svg.style.height = is3x2 ? '46%' : '80%';  // Keep 2x2 at 80%
        this.svg.style.transform = 'rotate(-90deg)';

        // Create background circles
        this.createBackgroundCircles();

        // Create progress circles group
        this.progressGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        this.svg.appendChild(this.progressGroup);

        svgContainer.appendChild(this.svg);

        // Create center content
        this.createCenterContent(svgContainer);

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
    }

    createBackgroundCircles() {
        const { data, centerX, centerY, baseRadius, gap, strokeWidth } = this.options;

        data.forEach((item, index) => {
            const radius = baseRadius + index * gap;
            const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            circle.setAttribute('cx', centerX);
            circle.setAttribute('cy', centerY);
            circle.setAttribute('r', radius);
            circle.setAttribute('fill', 'none');
            circle.setAttribute('stroke', 'rgba(0, 0, 0, 0.1)');
            circle.setAttribute('stroke-width', strokeWidth);
            circle.classList.add('circle-bg');
            this.svg.appendChild(circle);
        });
    }

    createCenterContent(svgContainer) {
        const centerDiv = document.createElement('div');
        centerDiv.style.position = 'absolute';
        centerDiv.style.inset = '0';
        centerDiv.style.display = 'flex';
        centerDiv.style.flexDirection = 'column';
        centerDiv.style.alignItems = 'center';
        centerDiv.style.justifyContent = 'center';
        centerDiv.style.pointerEvents = 'none';

        const averageScore = this.calculateAverageScore();
        const is3x2 = this.layoutSize === '3x2';

        const scoreDiv = document.createElement('div');
        scoreDiv.style.fontSize = is3x2 ? '42px' : '36px';  // 42px for 3x2, 36px for 2x2
        scoreDiv.style.fontWeight = '700';
        scoreDiv.style.opacity = '0';
        scoreDiv.style.transition = 'opacity 0.6s ease';
        scoreDiv.textContent = averageScore + '%';
        scoreDiv.className = 'circle-center-text';
        scoreDiv.classList.add('chart-text-primary');

        const labelDiv = document.createElement('div');
        labelDiv.style.display = 'flex';
        labelDiv.style.alignItems = 'center';
        labelDiv.style.marginTop = '-5px';  // Negative margin to bring it very close
        labelDiv.style.fontSize = '12px';
        labelDiv.style.opacity = '0';
        labelDiv.style.transition = 'opacity 0.6s ease';
        labelDiv.textContent = 'Average';  // Removed icon, just text
        labelDiv.classList.add('chart-text-secondary');

        this.centerTextElements = { scoreDiv, labelDiv };

        centerDiv.appendChild(scoreDiv);
        centerDiv.appendChild(labelDiv);
        svgContainer.appendChild(centerDiv);
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
        legend.style.marginLeft = is3x2 ? '-60px' : '0';
        legend.style.marginTop = is3x2 ? '0' : '-20px';  // Negative margin for 2x2 to bring legend closer
        legend.style.alignSelf = is3x2 ? 'center' : 'auto';

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

    updateProgressCircles(progress) {
        this.progressGroup.innerHTML = '';
        this.circleElements = [];  // Store references to circles

        const { data, centerX, centerY, baseRadius, gap, strokeWidth } = this.options;

        data.forEach((item, index) => {
            const radius = baseRadius + index * gap;
            const { dashArray, dashOffset } = this.calculateCirclePath(radius, item.percentage);

            const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            circle.setAttribute('cx', centerX);
            circle.setAttribute('cy', centerY);
            circle.setAttribute('r', radius);
            circle.setAttribute('fill', 'none');
            circle.setAttribute('stroke', item.gradient[0]);
            circle.setAttribute('stroke-width', strokeWidth);
            circle.setAttribute('stroke-linecap', 'round');
            circle.setAttribute('stroke-dasharray', dashArray);
            circle.style.cursor = 'pointer';
            circle.style.transition = 'stroke-width 0.3s, filter 0.3s';
            circle.style.filter = 'none';

            // Animate from full (dashArray) to target (dashOffset)
            const animatedOffset = dashArray - (dashArray - dashOffset) * progress;
            circle.setAttribute('stroke-dashoffset', animatedOffset);

            // Add tooltip interaction
            circle.addEventListener('mouseenter', (e) => {
                this.hoveredCircle = index;
                this.updateCircleHoverState(index, true);
                this.showTooltip(item, e);
            });
            circle.addEventListener('mousemove', (e) => {
                if (this.hoveredCircle === index) {
                    this.showTooltip(item, e);
                }
            });
            circle.addEventListener('mouseleave', () => {
                this.hoveredCircle = null;
                this.updateCircleHoverState(index, false);
                this.hideTooltip();
            });

            this.progressGroup.appendChild(circle);
            this.circleElements[index] = { circle: circle, strokeWidth: strokeWidth };
        });

        // Fade in center text and legend during animation
        if (progress > 0.4 && this.centerTextElements) {
            this.centerTextElements.scoreDiv.style.opacity = '1';
            this.centerTextElements.labelDiv.style.opacity = '1';
        }
        if (progress > 0.2 && this.legend) {
            this.legend.style.opacity = '1';
        }
    }

    createContainerTooltip() {
        this.tooltip = document.createElement('div');
        this.tooltip.style.position = 'absolute';
        this.tooltip.style.backgroundColor = '#1F2937';
        this.tooltip.style.color = 'white';
        this.tooltip.style.padding = '8px 12px';
        this.tooltip.style.borderRadius = '6px';
        this.tooltip.style.fontSize = '12px';
        this.tooltip.style.pointerEvents = 'none';
        this.tooltip.style.opacity = '0';
        this.tooltip.style.transition = 'opacity 0.2s';
        this.tooltip.style.zIndex = '1000';
        this.tooltip.style.whiteSpace = 'nowrap';
        this.container.appendChild(this.tooltip);
    }

    showTooltip(data, event) {
        this.tooltip.innerHTML = `
            <div style="font-weight: 600; margin-bottom: 4px;">${data.name}</div>
            <div>Progress: ${data.percentage}%</div>
            <div>Current: ${data.current.toLocaleString()}</div>
            <div>Total: ${data.total.toLocaleString()}</div>
        `;
        this.tooltip.style.opacity = '1';

        const containerRect = this.container.getBoundingClientRect();
        this.tooltip.style.left = (event.clientX - containerRect.left + 10) + 'px';
        this.tooltip.style.top = (event.clientY - containerRect.top - 10) + 'px';
    }

    hideTooltip() {
        this.tooltip.style.opacity = '0';
    }

    animate() {
        this.isAnimating = true;
        const startTime = Date.now();
        const { animationDuration } = this.options;

        const animateFrame = () => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / animationDuration, 1);

            // Ease-out cubic
            const easedProgress = 1 - Math.pow(1 - progress, 3);

            this.updateProgressCircles(easedProgress);

            if (progress < 1) {
                this.animationFrameId = requestAnimationFrame(animateFrame);
            } else {
                this.isAnimating = false;
            }
        };

        this.animationFrameId = requestAnimationFrame(animateFrame);
    }

    updateCircleHoverState(index, isHovered) {
        if (!this.circleElements || !this.circleElements[index]) {
            console.log('No circle element at index', index, this.circleElements);
            return;
        }

        const { circle, strokeWidth } = this.circleElements[index];

        if (isHovered) {
            circle.setAttribute('stroke-width', strokeWidth + 4);  // Increased from +2 to +4 for more visible effect
            circle.style.filter = 'brightness(1.2) drop-shadow(0 0 8px currentColor)';
        } else {
            circle.setAttribute('stroke-width', strokeWidth);
            circle.style.filter = 'none';
        }
    }

    update(newOptions) {
        this.options = { ...this.options, ...newOptions };
        if (newOptions.data) {
            this.isAnimating = false;
            if (this.animationFrameId) {
                cancelAnimationFrame(this.animationFrameId);
            }
            this.render();
            this.animate();
        }
    }

    destroy() {
        if (this.animationFrameId) {
            cancelAnimationFrame(this.animationFrameId);
        }
        this.container.innerHTML = '';
    }
}

// Make available globally for browser usage
if (typeof window !== 'undefined') {
    window.DotDNAAnimatedCircleChart = DotDNAAnimatedCircleChart;
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNAAnimatedCircleChart;
}
