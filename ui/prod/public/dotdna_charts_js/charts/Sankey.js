/**
 * Sankey - Enhanced Sankey diagram for flow visualization
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 * Based on the React enhanced-sankey-diagram implementation
 * Supports 3x2, 3x3, 4x2, and 4x3 grid layouts
 *
 * REFACTORED VERSION with JSON-first architecture:
 * - config: Static configuration (nodeWidth, animation settings, sizing)
 * - data: Dynamic data that can be updated without recreation
 * - setupDOM(): One-time DOM structure creation
 * - setData(): Update data dynamically
 * - resize(): Handle size changes
 */

class DotDNASankey extends DotDNASVGChartBase {
    constructor(containerId, config = {}) {
        super(containerId, config);

        // SEPARATE: Static configuration
        this.config = {
            animationDuration: config.animationDuration || 2000,
            nodeWidth: config.nodeWidth || 20,
            nodePadding: config.nodePadding || 30,
            showLabels: config.showLabels !== false,
            gridSize: config.gridSize || '3x2'
        };

        // SEPARATE: Dynamic data
        this.data = null;

        // State tracking
        this.hoveredFlow = null;
        this.hoveredNode = null;
        this.selectedFlow = null;
        this.animationProgress = 0;
        this.animationKey = 0;
        this.isAnimating = false;

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
     * GENERATE RANDOM DATA
     * Creates random Sankey flow data for initial display
     */
    generateRandomData() {
        return {
            sourceNodes: [
                { id: 'business', name: 'Business', value: 23987, sales: 540, color: '#5B9BD5' },
                { id: 'presentation', name: 'Presentation', value: 54641, sales: 1320, color: '#70A8E0' },
                { id: 'finance', name: 'Finance', value: 12657, sales: 430, color: '#85B8E5' },
                { id: 'development', name: 'Development', value: 33054, sales: 860, color: '#9AC8EA' }
            ],
            targetNodes: [
                { id: 'investments', name: 'Investments', value: 76644, period: '3 month', color: '#FFB6C1' },
                { id: 'startup', name: 'Startup', value: 5752, period: '6 month', color: '#FFDAB9' },
                { id: 'outsourcing', name: 'Outsourcing', value: 4978, period: '6 month', color: '#FFE4B5' },
                { id: 'main-projects', name: 'Main projects', value: 88642, period: '11 month', color: '#FFCCCB' }
            ],
            flows: [
                { source: 'business', target: 'investments', value: 15000 },
                { source: 'business', target: 'startup', value: 3000 },
                { source: 'business', target: 'main-projects', value: 5987 },

                { source: 'presentation', target: 'investments', value: 35000 },
                { source: 'presentation', target: 'main-projects', value: 15000 },
                { source: 'presentation', target: 'outsourcing', value: 4641 },

                { source: 'finance', target: 'investments', value: 8000 },
                { source: 'finance', target: 'startup', value: 2752 },
                { source: 'finance', target: 'outsourcing', value: 337 },
                { source: 'finance', target: 'main-projects', value: 1568 },

                { source: 'development', target: 'investments', value: 18644 },
                { source: 'development', target: 'main-projects', value: 14410 }
            ]
        };
    }

    /**
     * ONE-TIME DOM SETUP
     * Creates the wrapper and SVG container structure
     */
    setupDOM() {
        this.container.innerHTML = '';
        this.container.className = 'dotdna-chart';
        this.container.style.width = '100%';
        this.container.style.height = '100%';
        this.container.style.display = 'flex';
        this.container.style.flexDirection = 'column';
        this.container.style.position = 'relative';

        // Create wrapper with responsive padding based on grid size
        this.wrapper = document.createElement('div');
        this.wrapper.style.width = '100%';
        this.wrapper.style.height = '100%';
        this.wrapper.style.display = 'flex';
        this.wrapper.style.flexDirection = 'column';
        // Use minimal padding for 3x2, more padding for larger sizes
        const horizontalPadding = this.config.gridSize === '3x2' ? '0' : '15px';
        this.wrapper.style.padding = `15px ${horizontalPadding}`;
        this.wrapper.style.boxSizing = 'border-box';

        // Chart container
        this.chartContainer = document.createElement('div');
        this.chartContainer.style.flex = '1';
        this.chartContainer.style.position = 'relative';
        this.chartContainer.style.minHeight = '0';
        this.chartContainer.style.display = 'flex';
        this.chartContainer.style.alignItems = 'center';
        this.chartContainer.style.justifyContent = 'center';

        // Create SVG with fixed viewBox for proper scaling
        this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        this.svg.setAttribute('viewBox', '0 0 800 500');
        this.svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
        this.svg.style.width = '100%';
        this.svg.style.height = '100%';
        this.svg.style.display = 'block';
        this.svg.style.overflow = 'visible';

        // Define gradients and filters
        this.defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');

        // Glow filter
        const glowFilter = document.createElementNS('http://www.w3.org/2000/svg', 'filter');
        glowFilter.setAttribute('id', `glow-${this.container.id}`);
        const feGaussianBlur = document.createElementNS('http://www.w3.org/2000/svg', 'feGaussianBlur');
        feGaussianBlur.setAttribute('stdDeviation', '3');
        feGaussianBlur.setAttribute('result', 'coloredBlur');
        const feMerge = document.createElementNS('http://www.w3.org/2000/svg', 'feMerge');
        const feMergeNode1 = document.createElementNS('http://www.w3.org/2000/svg', 'feMergeNode');
        feMergeNode1.setAttribute('in', 'coloredBlur');
        const feMergeNode2 = document.createElementNS('http://www.w3.org/2000/svg', 'feMergeNode');
        feMergeNode2.setAttribute('in', 'SourceGraphic');
        feMerge.appendChild(feMergeNode1);
        feMerge.appendChild(feMergeNode2);
        glowFilter.appendChild(feGaussianBlur);
        glowFilter.appendChild(feMerge);
        this.defs.appendChild(glowFilter);

        this.svg.appendChild(this.defs);

        // Create main group for flows and nodes
        this.flowsGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        this.nodesGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        this.svg.appendChild(this.flowsGroup);
        this.svg.appendChild(this.nodesGroup);

        this.chartContainer.appendChild(this.svg);
        this.wrapper.appendChild(this.chartContainer);

        // Create tooltip
        this.tooltip = document.createElement('div');
        this.tooltip.style.cssText = `
            position: absolute;
            background-color: #1F2937;
            color: white;
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 11px;
            opacity: 0;
            pointer-events: none;
            z-index: 1000;
            white-space: nowrap;
            transition: opacity 0.2s ease-out;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
        `;
        this.wrapper.appendChild(this.tooltip);

        this.container.appendChild(this.wrapper);
    }

    /**
     * SET DATA - Update chart data dynamically
     * @param {Object} data - Chart data in format: {sourceNodes, targetNodes, flows}
     */
    setData(data) {
        if (!data || !data.sourceNodes || !data.targetNodes || !data.flows) {
            console.error('Sankey: setData() requires an object with sourceNodes, targetNodes, and flows');
            return;
        }

        this.data = data;
        this.renderSankey();
        this.startAnimation();
    }

    /**
     * RENDER SANKEY
     * Renders all flows and nodes based on current data
     */
    renderSankey() {
        this.flowsGroup.innerHTML = '';
        this.nodesGroup.innerHTML = '';
        this.defs.innerHTML = '';

        // Restore glow filter
        const glowFilter = document.createElementNS('http://www.w3.org/2000/svg', 'filter');
        glowFilter.setAttribute('id', `glow-${this.container.id}`);
        const feGaussianBlur = document.createElementNS('http://www.w3.org/2000/svg', 'feGaussianBlur');
        feGaussianBlur.setAttribute('stdDeviation', '3');
        feGaussianBlur.setAttribute('result', 'coloredBlur');
        const feMerge = document.createElementNS('http://www.w3.org/2000/svg', 'feMerge');
        const feMergeNode1 = document.createElementNS('http://www.w3.org/2000/svg', 'feMergeNode');
        feMergeNode1.setAttribute('in', 'coloredBlur');
        const feMergeNode2 = document.createElementNS('http://www.w3.org/2000/svg', 'feMergeNode');
        feMergeNode2.setAttribute('in', 'SourceGraphic');
        feMerge.appendChild(feMergeNode1);
        feMerge.appendChild(feMergeNode2);
        glowFilter.appendChild(feGaussianBlur);
        glowFilter.appendChild(feMerge);
        this.defs.appendChild(glowFilter);

        const isDarkMode = document.body.classList.contains('dark-mode');
        const textColor = isDarkMode ? '#FFFFFF' : '#374151';

        // SVG dimensions from viewBox
        const svgWidth = 800;
        const svgHeight = 500;

        // Calculate layout
        const layout = this.calculateLayout(svgWidth, svgHeight);

        // Create flow gradients
        layout.flowPaths.forEach((flowPath, index) => {
            const gradient = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
            gradient.setAttribute('id', `gradient-${index}-${this.container.id}`);
            gradient.setAttribute('x1', '0%');
            gradient.setAttribute('y1', '0%');
            gradient.setAttribute('x2', '100%');
            gradient.setAttribute('y2', '0%');

            const stop1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
            stop1.setAttribute('offset', '0%');
            stop1.setAttribute('stop-color', flowPath.sourceColor);
            stop1.setAttribute('stop-opacity', '0.8');

            const stop2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
            stop2.setAttribute('offset', '50%');
            stop2.setAttribute('stop-color', flowPath.sourceColor);
            stop2.setAttribute('stop-opacity', '0.6');

            const stop3 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
            stop3.setAttribute('offset', '100%');
            stop3.setAttribute('stop-color', flowPath.targetColor);
            stop3.setAttribute('stop-opacity', '0.8');

            gradient.appendChild(stop1);
            gradient.appendChild(stop2);
            gradient.appendChild(stop3);
            this.defs.appendChild(gradient);
        });

        // Draw flows
        layout.flowPaths.forEach((flowPath, index) => {
            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', flowPath.path);
            path.setAttribute('fill', `url(#gradient-${index}-${this.container.id})`);
            path.setAttribute('opacity', '0.7');
            path.setAttribute('class', 'sankey-flow');
            path.style.cursor = 'pointer';
            path.style.transition = 'opacity 0.3s ease, filter 0.3s ease';
            path.style.clipPath = 'inset(0 100% 0 0)';

            // Store flow data
            path.dataset.flowIndex = index;

            // Hover events
            path.addEventListener('mouseenter', (event) => {
                this.hoveredFlow = index;
                this.updateFlowHighlights();
                this.showFlowTooltip(flowPath, event);
            });

            path.addEventListener('mousemove', (e) => {
                this.updateTooltipPosition(e);
            });

            path.addEventListener('mouseleave', () => {
                this.hoveredFlow = null;
                this.updateFlowHighlights();
                this.hideTooltip();
            });

            path.addEventListener('click', () => {
                this.selectedFlow = this.selectedFlow === index ? null : index;
                this.updateFlowHighlights();
            });

            this.flowsGroup.appendChild(path);
        });

        // Draw source nodes
        layout.sourcePositions.forEach((pos, index) => {
            this.drawSourceNode(pos, index, textColor);
        });

        // Draw target nodes
        layout.targetPositions.forEach((pos, index) => {
            this.drawTargetNode(pos, index, textColor);
        });

        // Store layout for hover updates
        this.currentLayout = layout;
    }

    startAnimation() {
        if (this.isAnimating) return;

        this.isAnimating = true;
        this.animationProgress = 0;
        const startTime = Date.now();
        const duration = this.config.animationDuration;

        const animate = () => {
            if (!this.isAnimating) return;

            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);

            // Cubic easing out
            this.animationProgress = 1 - Math.pow(1 - progress, 3);

            this.updateAnimation();

            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                this.isAnimating = false;
            }
        };

        requestAnimationFrame(animate);
    }

    updateAnimation() {
        // Update flow clips
        const flows = this.svg.querySelectorAll('.sankey-flow');
        flows.forEach(flow => {
            flow.style.clipPath = `inset(0 ${100 - this.animationProgress * 100}% 0 0)`;
        });

        // Update node scales
        const sourceNodes = this.svg.querySelectorAll('.sankey-source-node');
        sourceNodes.forEach(node => {
            node.style.transform = `scaleX(${this.animationProgress})`;
        });

        const targetNodes = this.svg.querySelectorAll('.sankey-target-node');
        targetNodes.forEach(node => {
            node.style.transform = `scaleX(${this.animationProgress})`;
        });

        // Update label opacity
        const labels = this.svg.querySelectorAll('.sankey-label');
        labels.forEach(label => {
            label.setAttribute('opacity', this.animationProgress);
        });
    }

    drawSourceNode(pos, index, textColor) {
        const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');

        // Node rectangle
        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('x', pos.x);
        rect.setAttribute('y', pos.y);
        rect.setAttribute('width', pos.width);
        rect.setAttribute('height', pos.height);
        rect.setAttribute('rx', '2');
        rect.setAttribute('fill', pos.node.color);
        rect.setAttribute('class', 'sankey-source-node');
        rect.style.cursor = 'pointer';
        rect.style.transition = 'all 0.3s ease';
        rect.style.transformOrigin = `${pos.x}px ${pos.y + pos.height / 2}px`;

        rect.addEventListener('mouseenter', () => {
            this.hoveredNode = pos.node.id;
            this.updateFlowHighlights();
        });

        rect.addEventListener('mouseleave', () => {
            this.hoveredNode = null;
            this.updateFlowHighlights();
        });

        group.appendChild(rect);

        // Name label (above center)
        const nameLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        nameLabel.setAttribute('x', pos.x - 10);
        nameLabel.setAttribute('y', pos.y + pos.height / 2 - 8);
        nameLabel.setAttribute('text-anchor', 'end');
        nameLabel.setAttribute('dominant-baseline', 'middle');
        nameLabel.setAttribute('fill', textColor);
        nameLabel.setAttribute('font-size', '12px');
        nameLabel.setAttribute('font-weight', '500');
        nameLabel.setAttribute('opacity', '0');
        nameLabel.setAttribute('class', 'sankey-label');
        nameLabel.textContent = pos.node.name;
        group.appendChild(nameLabel);

        // Value label (below center)
        const valueLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        valueLabel.setAttribute('x', pos.x - 10);
        valueLabel.setAttribute('y', pos.y + pos.height / 2 + 8);
        valueLabel.setAttribute('text-anchor', 'end');
        valueLabel.setAttribute('dominant-baseline', 'middle');
        valueLabel.setAttribute('fill', textColor);
        valueLabel.setAttribute('font-size', '14px');
        valueLabel.setAttribute('font-weight', '700');
        valueLabel.setAttribute('opacity', '0');
        valueLabel.setAttribute('class', 'sankey-label');
        valueLabel.textContent = `$${pos.node.value.toLocaleString()}`;
        group.appendChild(valueLabel);

        this.nodesGroup.appendChild(group);
    }

    drawTargetNode(pos, index, textColor) {
        const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');

        // Node rectangle
        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('x', pos.x);
        rect.setAttribute('y', pos.y);
        rect.setAttribute('width', pos.width);
        rect.setAttribute('height', pos.height);
        rect.setAttribute('rx', '2');
        rect.setAttribute('fill', pos.node.color);
        rect.setAttribute('class', 'sankey-target-node');
        rect.style.cursor = 'pointer';
        rect.style.transition = 'all 0.3s ease';
        rect.style.transformOrigin = `${pos.x + pos.width}px ${pos.y + pos.height / 2}px`;

        rect.addEventListener('mouseenter', () => {
            this.hoveredNode = pos.node.id;
            this.updateFlowHighlights();
        });

        rect.addEventListener('mouseleave', () => {
            this.hoveredNode = null;
            this.updateFlowHighlights();
        });

        group.appendChild(rect);

        // Name label (above center)
        const nameLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        nameLabel.setAttribute('x', pos.x + pos.width + 10);
        nameLabel.setAttribute('y', pos.y + pos.height / 2 - 8);
        nameLabel.setAttribute('text-anchor', 'start');
        nameLabel.setAttribute('dominant-baseline', 'middle');
        nameLabel.setAttribute('fill', textColor);
        nameLabel.setAttribute('font-size', '12px');
        nameLabel.setAttribute('font-weight', '500');
        nameLabel.setAttribute('opacity', '0');
        nameLabel.setAttribute('class', 'sankey-label');
        nameLabel.textContent = pos.node.name;
        group.appendChild(nameLabel);

        // Value label (below center)
        const valueLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        valueLabel.setAttribute('x', pos.x + pos.width + 10);
        valueLabel.setAttribute('y', pos.y + pos.height / 2 + 8);
        valueLabel.setAttribute('text-anchor', 'start');
        valueLabel.setAttribute('dominant-baseline', 'middle');
        valueLabel.setAttribute('fill', textColor);
        valueLabel.setAttribute('font-size', '14px');
        valueLabel.setAttribute('font-weight', '700');
        valueLabel.setAttribute('opacity', '0');
        valueLabel.setAttribute('class', 'sankey-label');
        valueLabel.textContent = `$${pos.node.value.toLocaleString()}`;
        group.appendChild(valueLabel);

        this.nodesGroup.appendChild(group);
    }

    calculateLayout(width, height) {
        const { sourceNodes, targetNodes, flows } = this.data;
        const nodeWidth = this.config.nodeWidth;
        const nodePadding = this.config.nodePadding;

        // Use tighter margins for 3x2, more spacious for larger sizes
        const sideMargin = this.config.gridSize === '3x2' ? 80 : 120;
        const leftX = sideMargin;
        const rightX = width - sideMargin;
        const centerX = width / 2;

        // Calculate node positions
        const sourcePositions = this.calculateNodePositions(sourceNodes, leftX, height);
        const targetPositions = this.calculateNodePositions(targetNodes, rightX, height);

        // Create position maps
        const sourceMap = new Map(sourcePositions.map(pos => [pos.node.id, pos]));
        const targetMap = new Map(targetPositions.map(pos => [pos.node.id, pos]));

        // Calculate flow paths
        const sourceOffsets = new Map(sourceNodes.map(node => [node.id, 0]));
        const targetOffsets = new Map(targetNodes.map(node => [node.id, 0]));

        const flowPaths = flows.map((flow, index) => {
            const sourcePos = sourceMap.get(flow.source);
            const targetPos = targetMap.get(flow.target);

            if (!sourcePos || !targetPos) return null;

            const sourceHeight = (flow.value / sourcePos.node.value) * sourcePos.height;
            const targetHeight = (flow.value / targetPos.node.value) * targetPos.height;

            const sourceOffset = sourceOffsets.get(flow.source);
            const targetOffset = targetOffsets.get(flow.target);

            const sourceX = sourcePos.x + nodeWidth;
            const sourceY = sourcePos.y + sourceOffset;
            const targetX = targetPos.x;
            const targetY = targetPos.y + targetOffset;

            const controlPoint1X = sourceX + (centerX - sourceX) * 0.5;
            const controlPoint2X = targetX - (targetX - centerX) * 0.5;

            const pathData = `
                M ${sourceX} ${sourceY}
                C ${controlPoint1X} ${sourceY}, ${controlPoint2X} ${targetY}, ${targetX} ${targetY}
                L ${targetX} ${targetY + targetHeight}
                C ${controlPoint2X} ${targetY + targetHeight}, ${controlPoint1X} ${sourceY + sourceHeight}, ${sourceX} ${sourceY + sourceHeight}
                Z
            `;

            sourceOffsets.set(flow.source, sourceOffset + sourceHeight);
            targetOffsets.set(flow.target, targetOffset + targetHeight);

            return {
                index,
                path: pathData,
                flow,
                sourceColor: sourcePos.node.color,
                targetColor: targetPos.node.color,
                sourceY: sourceY + sourceHeight / 2,
                targetY: targetY + targetHeight / 2,
                value: flow.value,
                sourceId: flow.source,
                targetId: flow.target,
                sourceName: sourcePos.node.name,
                targetName: targetPos.node.name
            };
        }).filter(p => p !== null);

        return {
            sourcePositions,
            targetPositions,
            flowPaths,
            centerX
        };
    }

    calculateNodePositions(nodes, x, height) {
        const totalHeight = height - 100;
        const totalGaps = this.config.nodePadding * (nodes.length - 1);
        const availableHeight = totalHeight - totalGaps;

        const maxValue = Math.max(...nodes.map(n => n.value));
        let currentY = 50;

        return nodes.map(node => {
            const nodeHeight = (node.value / maxValue) * (availableHeight / nodes.length) * 2;
            const position = {
                x,
                y: currentY,
                width: this.config.nodeWidth,
                height: Math.max(nodeHeight, 20),
                node
            };
            currentY += position.height + this.config.nodePadding;
            return position;
        });
    }

    updateFlowHighlights() {
        if (!this.currentLayout) return;

        const flows = this.svg.querySelectorAll('.sankey-flow');

        flows.forEach((flow, index) => {
            const flowPath = this.currentLayout.flowPaths[index];
            if (!flowPath) return;

            const isConnectedToHoveredNode = this.hoveredNode &&
                (flowPath.sourceId === this.hoveredNode || flowPath.targetId === this.hoveredNode);
            const shouldHighlight = this.hoveredFlow === index || isConnectedToHoveredNode;
            const flowOpacity = (this.hoveredFlow !== null || this.hoveredNode !== null) && !shouldHighlight ? 0.3 : 0.7;

            flow.setAttribute('opacity', flowOpacity);
            flow.style.filter = shouldHighlight ? `url(#glow-${this.container.id})` : 'none';
        });
    }

    showFlowTooltip(flowPath, event) {
        this.tooltip.innerHTML = `
            <div style="font-weight: 600; margin-bottom: 6px;">${flowPath.sourceName} → ${flowPath.targetName}</div>
            <div style="color: #9CA3AF;">
                <span>Flow:</span>
                <span style="font-weight: 600; margin-left: 6px;">$${flowPath.value.toLocaleString()}</span>
            </div>
        `;

        this.updateTooltipPosition(event);
        this.tooltip.style.opacity = '1';
    }

    updateTooltipPosition(event) {
        const tooltipRect = this.tooltip.getBoundingClientRect();
        const containerRect = this.container.getBoundingClientRect();

        let left = event.clientX - containerRect.left - tooltipRect.width / 2;
        let top = event.clientY - containerRect.top - tooltipRect.height - 10;

        left = Math.max(10, Math.min(left, containerRect.width - tooltipRect.width - 10));
        top = Math.max(10, top);

        this.tooltip.style.left = left + 'px';
        this.tooltip.style.top = top + 'px';
    }

    hideTooltip() {
        this.tooltip.style.opacity = '0';
    }

    /**
     * RESIZE - Handle size changes
     * @param {String} gridSize - New grid size
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
        console.warn('Sankey: render() is deprecated. Use setData() instead.');
        if (data) {
            this.setData(data);
        }
    }

    // Deprecated: Use setData() instead
    updateData(newData) {
        console.warn('Sankey: updateData() is deprecated. Use setData() instead.');
        this.setData(newData);
    }

    // Deprecated: Use setData() with current data instead
    refresh() {
        console.warn('Sankey: refresh() is deprecated. Use setData() instead.');
        if (this.data) {
            this.setData(this.data);
        }
    }

    destroy() {
        this.isAnimating = false;
        this.container.innerHTML = '';
    }
}

// Export to window for wizard access
if (typeof window !== 'undefined') {
    window.DotDNASankey = DotDNASankey;
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNASankey;
}
