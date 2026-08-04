/**
 * LineChart - Smooth animated line chart with gradient fill
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 * Version: 2.0.0 - Complete rewrite for consistency - 2025-11-13 18:00
 * Supports 3x2, 4x2 grid layouts
 */

class DotDNALineChart extends DotDNASVGChartBase {
    constructor(containerId, config = {}) {
        super(containerId, config);

        // Static configuration
        this.config = {
            lineWidth: config.lineWidth || 3,
            pointRadius: config.pointRadius || 3.5,
            animationDuration: config.animationDuration || 2000,
            lineColor: config.lineColor || (window.ChartPalette ? window.ChartPalette.colors(3) : ['#10B981', '#059669']),
            gridSize: config.gridSize || '3x2',
            compactLegend: config.compactLegend || false,  // single-row legend (monitoring)
            animate: config.animate !== false,              // false → instant, seamless redraws (monitoring auto-refresh)
            showDots: config.showDots !== false             // false → smooth lines, no per-point dots (monitoring)
        };

        // Dynamic data
        this.data = null;

        // State tracking
        this.hoveredPoint = null;
        this.allowedSizes = ['3x2', '4x2'];
        // Optional value unit for axis/tooltip formatting (opt-in). When unset,
        // values render as plain rounded numbers (unchanged legacy behaviour, so
        // consumers that don't pass a unit — e.g. Reporting — are unaffected).
        // Recognised: 'percent' | 'bytes' | 'bytesPerSec' | 'duration' (seconds
        // in) | 'short' | 'perSec'.
        this.unit = config.unit || null;
        this._pendingTimers = [];

        // Create DOM structure once
        this.setupDOM();

        // Only render if initial data provided; otherwise wait for setData()
        if (config.data) {
            this.setData(config.data);
        } else {
            this.showNoData();
        }
    }

    generateRandomData() {
        const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        return months.map((label, i) => ({
            label,
            value: Math.round(50 + Math.sin(i * 0.5) * 30 + Math.random() * 15)
        }));
    }

    setupDOM() {
        this.container.innerHTML = '';
        this.container.className = 'dotdna-chart';
        this.container.style.width = '100%';
        this.container.style.height = '100%';
        this.container.style.display = 'flex';
        this.container.style.alignItems = 'center';
        this.container.style.justifyContent = 'center';
        this.container.style.position = 'relative';

        // Wrapper
        this.wrapper = document.createElement('div');
        this.wrapper.style.width = '100%';
        this.wrapper.style.height = '100%';
        this.wrapper.style.display = 'flex';
        this.wrapper.style.flexDirection = 'column';
        this.wrapper.style.padding = '10px 20px';
        this.wrapper.style.boxSizing = 'border-box';

        // Chart container
        this.chartContainer = document.createElement('div');
        this.chartContainer.style.flex = '1';
        this.chartContainer.style.position = 'relative';
        this.chartContainer.style.minHeight = '0';
        this.chartContainer.style.marginTop = '-40px';

        // SVG with fixed viewBox
        this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        this.svg.setAttribute('viewBox', '0 0 1000 400');
        this.svg.setAttribute('preserveAspectRatio', 'none');  // Stretch to fill
        this.svg.style.width = '100%';
        this.svg.style.height = '100%';
        this.svg.style.display = 'block';

        // Points overlay container (HTML elements to avoid squeezing)
        this.pointsContainer = document.createElement('div');
        this.pointsContainer.style.position = 'absolute';
        this.pointsContainer.style.top = '0';
        this.pointsContainer.style.left = '0';
        this.pointsContainer.style.width = '100%';
        this.pointsContainer.style.height = '100%';
        this.pointsContainer.style.pointerEvents = 'none';

        // Y-axis labels container (HTML elements to avoid squeezing)
        this.yLabelsContainer = document.createElement('div');
        this.yLabelsContainer.style.position = 'absolute';
        this.yLabelsContainer.style.top = '0';
        this.yLabelsContainer.style.left = '0';
        this.yLabelsContainer.style.width = '100%';
        this.yLabelsContainer.style.height = '100%';
        this.yLabelsContainer.style.pointerEvents = 'none';

        // Defs for gradients
        this.defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
        this.svg.appendChild(this.defs);

        // Create gradient
        this.createGradient();

        // Main group for line and points
        this.lineGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        this.svg.appendChild(this.lineGroup);

        this.chartContainer.appendChild(this.svg);
        this.chartContainer.appendChild(this.yLabelsContainer);
        this.chartContainer.appendChild(this.pointsContainer);
        this.wrapper.appendChild(this.chartContainer);

        // X-axis labels container
        this.xLabelsContainer = document.createElement('div');
        this.xLabelsContainer.style.position = 'relative';
        this.xLabelsContainer.style.height = '20px';
        this.xLabelsContainer.style.marginTop = '-5px';
        this.xLabelsContainer.style.width = '100%';
        this.xLabelsContainer.style.paddingRight = '40px';
        this.xLabelsContainer.style.boxSizing = 'border-box';
        this.xLabelsContainer.style.overflow = 'hidden';
        this.wrapper.appendChild(this.xLabelsContainer);

        // Tooltip — colours come from a CSS class (`.line-chart-tooltip`)
        // so theme switches restyle the existing tooltip immediately
        // without needing the chart to re-render. Inline styles only
        // cover layout/positioning bits.
        this.tooltip = document.createElement('div');
        this.tooltip.className = 'line-chart-tooltip';
        this.tooltip.style.cssText = `
            position: absolute;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            padding: 8px 12px;
            border-radius: 8px;
            font-size: 12px;
            line-height: 1.5;
            opacity: 0;
            pointer-events: none;
            z-index: 1000;
            transition: opacity 0.2s ease-out;
        `;
        this.wrapper.appendChild(this.tooltip);

        // Crosshair hover layer. Dense series skip per-point dots, so there is
        // nothing to hover for a tooltip. This transparent overlay tracks the
        // cursor, finds the nearest x-index and shows every series' value there
        // with a vertical guide + dots. Enabled per-render only for dense series
        // (sparse charts keep their per-point dot tooltips), so Reporting/chat
        // are unaffected.
        this.hoverOverlay = document.createElement('div');
        this.hoverOverlay.style.position = 'absolute';
        this.hoverOverlay.style.inset = '0';
        this.hoverOverlay.style.zIndex = '5';
        this.hoverOverlay.style.pointerEvents = 'none';
        this.chartContainer.appendChild(this.hoverOverlay);

        this._crosshairGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        this._crosshairGroup.style.pointerEvents = 'none';
        this.svg.appendChild(this._crosshairGroup);

        this._hover = null;
        this._hoverIndex = -1;
        this.hoverOverlay.addEventListener('mousemove', (e) => this._onHoverMove(e));
        this.hoverOverlay.addEventListener('mouseleave', () => this._onHoverLeave());

        this.container.appendChild(this.wrapper);
    }

    createGradient() {
        const gradient = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
        gradient.setAttribute('id', `lineGradient-${this.container.id}`);
        gradient.setAttribute('x1', '0%');
        gradient.setAttribute('y1', '0%');
        gradient.setAttribute('x2', '100%');
        gradient.setAttribute('y2', '0%');

        const stop1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        stop1.setAttribute('offset', '0%');
        stop1.setAttribute('stop-color', this.config.lineColor[0]);

        const stop2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        stop2.setAttribute('offset', '100%');
        stop2.setAttribute('stop-color', this.config.lineColor[1]);

        gradient.appendChild(stop1);
        gradient.appendChild(stop2);
        this.defs.appendChild(gradient);

        // Area gradient (semi-transparent)
        const areaGradient = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
        areaGradient.setAttribute('id', `areaGradient-${this.container.id}`);
        areaGradient.setAttribute('x1', '0%');
        areaGradient.setAttribute('y1', '0%');
        areaGradient.setAttribute('x2', '0%');
        areaGradient.setAttribute('y2', '100%');

        const areaStop1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        areaStop1.setAttribute('offset', '0%');
        areaStop1.setAttribute('stop-color', this.config.lineColor[0]);
        areaStop1.setAttribute('stop-opacity', '0.3');

        const areaStop2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        areaStop2.setAttribute('offset', '100%');
        areaStop2.setAttribute('stop-color', this.config.lineColor[0]);
        areaStop2.setAttribute('stop-opacity', '0.05');

        areaGradient.appendChild(areaStop1);
        areaGradient.appendChild(areaStop2);
        this.defs.appendChild(areaGradient);
    }

    getSeriesColors(count) {
        if (window.ChartPalette) {
            const colors = [];
            for (let i = 0; i < count; i++) {
                colors.push(window.ChartPalette.colors(i)[0]);
            }
            return colors;
        }
        const fallback = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6',
            '#EC4899', '#06B6D4', '#84CC16', '#F97316', '#6366F1'];
        return fallback.slice(0, Math.min(count, fallback.length));
    }

    setData(data) {
        if (!data || !Array.isArray(data)) {
            console.error('LineChart: setData() requires an array');
            return;
        }

        // Detect multi-series
        const hasSeries = data.some(d => d.series);
        if (hasSeries) {
            const groups = {};
            const seriesOrder = [];
            data.forEach(d => {
                const key = d.series || 'Total';
                if (!groups[key]) {
                    groups[key] = [];
                    seriesOrder.push(key);
                }
                groups[key].push({ label: d.label, value: d.value });
            });
            this.seriesMap = groups;
            this.seriesOrder = seriesOrder;
            this.multiSeries = true;
        } else {
            this.seriesMap = null;
            this.seriesOrder = null;
            this.multiSeries = false;
        }

        this.data = data;
        this.render();
    }

    _clearPendingTimers() {
        for (const id of this._pendingTimers) {
            clearTimeout(id);
        }
        this._pendingTimers = [];
    }

    _scheduleTimer(fn, delay) {
        // animate:false (monitoring auto-refresh) → run the reveal synchronously
        // so each element paints at its final state in the same tick it was
        // created. With no intermediate paint the CSS transitions never fire, so
        // the chart redraws instantly on every refetch instead of replaying its
        // multi-second entry animation. Reporting/chat keep animate=true.
        if (!this.config.animate) {
            fn();
            return -1;
        }
        const id = setTimeout(fn, delay);
        this._pendingTimers.push(id);
        return id;
    }

    // Round n up to the nearest "nice" number (1/2/5 × 10ⁿ).
    _niceNum(n) {
        if (!(n > 0)) return 1;
        const exp = Math.floor(Math.log10(n));
        const frac = n / Math.pow(10, exp);
        const nice = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10;
        return nice * Math.pow(10, exp);
    }

    // Pick a y-axis domain that reads cleanly: non-negative data anchors at 0
    // (so metrics never dip below the axis), the step is a nice number, and the
    // domain covers the data across exactly `gridLines` intervals. Replaces the
    // old `Math.floor(min/10)*10` snapping that produced negative axes and
    // collapsed 0–1 ratios.
    _niceScale(min, max, gridLines = 5) {
        const lo = min < 0 ? min : 0;
        let hi = max;
        if (hi <= lo) {
            if (lo === 0) return { yMin: 0, yMax: 1 };
            hi = lo + Math.abs(lo) * 0.5;
        }
        let step = this._niceNum((hi - lo) / gridLines);
        let yMin = Math.floor(lo / step) * step;
        // floor(lo/step) can drop yMin up to a full step below lo, which for
        // zero-straddling data can leave yMax below max and clip the top point.
        // Grow the step (kept a nice number) until the domain covers max. No-op
        // for non-negative data, where yMin=0 and step*gridLines already >= max.
        while (yMin + step * gridLines < hi) {
            step = this._niceNum(step * 1.5);
            yMin = Math.floor(lo / step) * step;
        }
        return { yMin, yMax: yMin + step * gridLines };
    }

    // Plain number: locale-grouped integers, small fractions trimmed of float
    // noise from step arithmetic (e.g. 0.30000000000000004 → 0.3).
    _num(v) {
        if (!isFinite(v)) return '0';
        if (Number.isInteger(v)) return v.toLocaleString();
        const r = Math.round(v * 1000) / 1000;
        return Number.isInteger(r) ? r.toLocaleString() : String(r);
    }

    _formatBytes(v) {
        const abs = Math.abs(v);
        if (abs < 1024) return Math.round(v) + ' B';
        const units = ['KB', 'MB', 'GB', 'TB', 'PB'];
        let n = v / 1024, i = 0;
        while (Math.abs(n) >= 1024 && i < units.length - 1) { n /= 1024; i++; }
        return (Math.abs(n) >= 100 ? Math.round(n) : Math.round(n * 10) / 10) + ' ' + units[i];
    }

    _formatShort(v) {
        const abs = Math.abs(v);
        if (abs < 1000) return this._num(v);
        for (const [suf, div] of [['B', 1e9], ['M', 1e6], ['K', 1e3]]) {
            if (abs >= div) {
                const n = v / div;
                return (Math.abs(n) >= 100 ? Math.round(n) : Math.round(n * 10) / 10) + suf;
            }
        }
        return this._num(v);
    }

    // Duration formatter — input is seconds.
    _formatDuration(v) {
        const abs = Math.abs(v);
        if (abs === 0) return '0';
        if (abs < 1e-3) return Math.round(v * 1e6) + ' µs';
        if (abs < 1) return Math.round(v * 1e3) + ' ms';
        if (abs < 60) return (Math.round(v * 100) / 100) + ' s';
        if (abs < 3600) return Math.round(v / 60) + ' min';
        return (Math.round(v / 360) / 10) + ' h';
    }

    // Format a value for axis labels + tooltips per the configured unit.
    _formatValue(v) {
        switch (this.unit) {
            case 'percent': return this._num(v) + '%';
            case 'bytes': return this._formatBytes(v);
            case 'bytesPerSec': return this._formatBytes(v) + '/s';
            case 'duration': return this._formatDuration(v);
            case 'perSec': return this._formatShort(v) + '/s';
            case 'short': return this._formatShort(v);
            // No unit (e.g. Reporting): show the exact value trimmed of float
            // noise. Do NOT round — rounding loses tooltip precision and can
            // collapse fractional nice-scale ticks into duplicate labels.
            default: return this._num(v);
        }
    }

    _drawGrid(yMin, yMax, padding, svgWidth, svgHeight, chartWidth, chartHeight) {
        const gridLines = 5;

        for (let i = 0; i <= gridLines; i++) {
            const y = padding.top + (chartHeight / gridLines) * i;
            const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line.setAttribute('x1', padding.left);
            line.setAttribute('y1', y);
            line.setAttribute('x2', padding.left + chartWidth);
            line.setAttribute('y2', y);
            line.setAttribute('stroke-width', '1');
            line.setAttribute('stroke-dasharray', '4,4');
            // Stroke colour + opacity come from `.line-chart-grid` so
            // theme switches restyle existing grid lines via CSS without
            // needing the chart to re-render.
            line.setAttribute('class', 'line-chart-grid');
            this.lineGroup.appendChild(line);

            // Y-axis label (HTML to avoid squeezing)
            const value = yMax - ((yMax - yMin) / gridLines) * i;
            const yPercent = (y / svgHeight) * 100;
            const label = document.createElement('div');
            label.style.position = 'absolute';
            label.style.top = `${yPercent}%`;
            label.style.left = '0';
            label.style.transform = 'translateY(-50%)';
            label.style.fontSize = '12px';
            label.style.textAlign = 'right';
            label.style.paddingRight = '10px';
            label.style.width = `${(padding.left / svgWidth) * 100}%`;
            label.className = 'line-chart-axis-label';
            label.textContent = this._formatValue(value);
            this.yLabelsContainer.appendChild(label);
        }
    }

    _drawXLabels(items, numPoints, padding, svgWidth, chartWidth) {
        // Show start, end, and 3 evenly-spaced middle labels (5 total).
        // For shorter datasets we fall back to whatever points exist.
        const maxLabels = Math.min(5, numPoints);
        const visible = new Set([0, numPoints - 1]);
        if (maxLabels > 2 && numPoints > 2) {
            const step = (numPoints - 1) / (maxLabels - 1);
            for (let i = 1; i < maxLabels - 1; i++) {
                visible.add(Math.round(step * i));
            }
        }

        // Shorten ISO date labels (e.g. "2026-03-15" → "Mar 15")
        const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        const formatLabel = (text) => {
            if (!text) return text;
            const m = String(text).match(/^\d{4}-(\d{2})-(\d{2})/);
            if (m) return monthNames[parseInt(m[1], 10) - 1] + ' ' + parseInt(m[2], 10);
            return text;
        };

        items.forEach((item, index) => {
            if (!visible.has(index)) return;

            const xPercent = ((padding.left + (chartWidth / Math.max(numPoints - 1, 1)) * index) / svgWidth) * 100;
            const label = document.createElement('div');
            label.style.position = 'absolute';
            label.style.left = xPercent + '%';

            if (index === numPoints - 1) {
                label.style.transform = 'translateX(-100%)';
                label.style.textAlign = 'right';
            } else if (index === 0) {
                label.style.transform = 'translateX(0)';
                label.style.textAlign = 'left';
            } else {
                label.style.transform = 'translateX(-50%)';
                label.style.textAlign = 'center';
            }

            label.style.fontSize = '11px';
            label.style.fontWeight = '500';
            label.className = 'line-chart-axis-label';
            label.textContent = formatLabel(item.label);
            this.xLabelsContainer.appendChild(label);
        });
    }

    render() {
        // Cancel any pending animation timers from previous render
        this._clearPendingTimers();

        this.lineGroup.innerHTML = '';
        this.xLabelsContainer.innerHTML = '';
        this.pointsContainer.innerHTML = '';
        this.yLabelsContainer.innerHTML = '';

        // Remove legend if exists
        if (this.legendContainer) {
            this.legendContainer.remove();
            this.legendContainer = null;
        }

        if (!this.data || this.data.length === 0) return;

        if (this.multiSeries) {
            this.renderMultiSeries();
        } else {
            this.renderSingleSeries();
        }
    }

    renderSingleSeries() {
        // SVG dimensions
        const svgWidth = 1000;
        const svgHeight = 400;
        // Unit-suffixed tick labels ("500 ms", "1.5 MB/s") need a wider left
        // gutter than bare numbers or they collide in narrow panels. Reporting/
        // chat charts set no unit and keep the original 90.
        const padding = { top: 80, right: 40, bottom: 40, left: this.unit ? 120 : 90 };
        const chartWidth = svgWidth - padding.left - padding.right;
        const chartHeight = svgHeight - padding.top - padding.bottom;

        // Calculate scale
        const maxValue = Math.max(...this.data.map(d => d.value));
        const minValue = Math.min(...this.data.map(d => d.value));
        const { yMin, yMax } = this._niceScale(minValue, maxValue);

        // Draw grid lines and Y-axis labels
        this._drawGrid(yMin, yMax, padding, svgWidth, svgHeight, chartWidth, chartHeight);

        // Calculate points
        const points = this.data.map((item, index) => {
            const x = padding.left + (chartWidth / Math.max(this.data.length - 1, 1)) * index;
            const normalizedValue = (item.value - yMin) / (yMax - yMin);
            const y = padding.top + chartHeight - (normalizedValue * chartHeight);
            return { x, y, label: item.label, value: item.value };
        });

        // Draw area fill
        const areaPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        let areaD = `M ${points[0].x},${svgHeight - padding.bottom}`;
        areaD += ` L ${points[0].x},${points[0].y}`;
        points.forEach(p => {
            areaD += ` L ${p.x},${p.y}`;
        });
        areaD += ` L ${points[points.length - 1].x},${svgHeight - padding.bottom} Z`;
        areaPath.setAttribute('d', areaD);
        areaPath.setAttribute('fill', `url(#areaGradient-${this.container.id})`);
        areaPath.style.opacity = '0';
        this.lineGroup.appendChild(areaPath);

        // Animate area fade in
        this._scheduleTimer(() => {
            areaPath.style.transition = `opacity ${this.config.animationDuration * 0.8}ms ease-out`;
            areaPath.style.opacity = '1';
        }, 400);

        // Draw line
        const linePath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        let lineD = `M ${points[0].x},${points[0].y}`;
        points.forEach((p, i) => {
            if (i > 0) {
                lineD += ` L ${p.x},${p.y}`;
            }
        });
        linePath.setAttribute('d', lineD);
        linePath.setAttribute('stroke', `url(#lineGradient-${this.container.id})`);
        linePath.setAttribute('stroke-width', this.config.lineWidth);
        linePath.setAttribute('fill', 'none');
        linePath.setAttribute('stroke-linecap', 'round');
        linePath.setAttribute('stroke-linejoin', 'round');

        // Animate line drawing
        const pathLength = linePath.getTotalLength();
        linePath.style.strokeDasharray = pathLength;
        linePath.style.strokeDashoffset = pathLength;
        this.lineGroup.appendChild(linePath);

        // Trigger animation
        this._scheduleTimer(() => {
            linePath.style.transition = `stroke-dashoffset ${this.config.animationDuration}ms cubic-bezier(0.4, 0, 0.2, 1)`;
            linePath.style.strokeDashoffset = '0';
        }, 50);

        // X-axis labels: only show start, end, and 3 evenly-spaced middle
        // dates (5 total). Dots stay on every data point — only the labels
        // are thinned. Date strings get the same "Mar 15" formatting as the
        // multi-series path.
        const numPoints = points.length;
        const maxLabels = Math.min(5, numPoints);
        const visibleLabelIdx = new Set([0, numPoints - 1]);
        if (maxLabels > 2 && numPoints > 2) {
            const step = (numPoints - 1) / (maxLabels - 1);
            for (let i = 1; i < maxLabels - 1; i++) {
                visibleLabelIdx.add(Math.round(step * i));
            }
        }
        const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        const formatLabel = (text) => {
            if (!text) return text;
            const m = String(text).match(/^\d{4}-(\d{2})-(\d{2})/);
            if (m) return monthNames[parseInt(m[1], 10) - 1] + ' ' + parseInt(m[2], 10);
            return text;
        };

        // Draw points as HTML elements (to avoid SVG stretching issues). Dense
        // series skip per-point dots: they read as a clean line and avoid a
        // multi-second staggered-dot animation. Sparse series keep hover dots.
        const isDense = points.length > 30;
        // Dots render only for sparse charts that opt into them (Reporting). Dense
        // charts and monitoring (showDots:false) render as clean smooth lines and
        // rely on the crosshair for hover values.
        const drawDots = !isDense && this.config.showDots;
        points.forEach((point, index) => {
            const xPercent = (point.x / svgWidth) * 100;
            const yPercent = (point.y / svgHeight) * 100;
            const delay = isDense ? 400 : 800 + (index * 80);

            if (drawDots) {
                const circle = document.createElement('div');
                const size = this.config.pointRadius * 2;
                circle.style.position = 'absolute';
                circle.style.left = `${xPercent}%`;
                circle.style.top = `${yPercent}%`;
                circle.style.width = `${size}px`;
                circle.style.height = `${size}px`;
                circle.style.borderRadius = '50%';
                circle.style.backgroundColor = this.config.lineColor[0];
                circle.style.border = 'none';
                circle.style.transform = 'translate(-50%, -50%) scale(0)';
                circle.style.cursor = 'pointer';
                circle.style.opacity = '0';
                circle.style.pointerEvents = 'auto';
                circle.style.boxSizing = 'border-box';

                circle.addEventListener('mouseenter', () => {
                    const hoverSize = (this.config.pointRadius + 2) * 2;
                    circle.style.width = `${hoverSize}px`;
                    circle.style.height = `${hoverSize}px`;
                    this.showTooltip(point);
                });
                circle.addEventListener('mouseleave', () => {
                    circle.style.width = `${size}px`;
                    circle.style.height = `${size}px`;
                    this.hideTooltip();
                });

                this.pointsContainer.appendChild(circle);

                this._scheduleTimer(() => {
                    circle.style.transition = `opacity 400ms ease-out, transform 400ms cubic-bezier(0.34, 1.56, 0.64, 1), width 200ms ease-out, height 200ms ease-out`;
                    circle.style.opacity = '1';
                    circle.style.transform = 'translate(-50%, -50%) scale(1)';
                }, delay);
            }

            // X-axis labels — skip points that aren't in the visible set so
            // long datasets don't paint a wall of overlapping dates.
            if (visibleLabelIdx.has(index)) {
                const label = document.createElement('div');
                label.style.position = 'absolute';
                label.style.left = xPercent + '%';

                if (index === numPoints - 1) {
                    label.style.transform = 'translateX(-100%)';
                    label.style.textAlign = 'right';
                } else if (index === 0) {
                    label.style.transform = 'translateX(0)';
                    label.style.textAlign = 'left';
                } else {
                    label.style.transform = 'translateX(-50%)';
                    label.style.textAlign = 'center';
                }

                label.style.fontSize = '11px';
                label.style.fontWeight = '500';
                label.style.opacity = '0';
                label.className = 'line-chart-axis-label';
                label.textContent = formatLabel(point.label);
                this.xLabelsContainer.appendChild(label);

                // Animate label fade in
                this._scheduleTimer(() => {
                    label.style.transition = 'opacity 300ms ease-out';
                    label.style.opacity = '1';
                }, delay);
            }
        });

        this._setHoverModel(!drawDots, [{ name: '', color: this.config.lineColor[0], points }], this.data.map((d) => d.label), padding, chartWidth, chartHeight, points.length);
    }

    renderMultiSeries() {
        const svgWidth = 1000;
        const svgHeight = 400;
        // Wider gutter for unit-suffixed labels; see renderSingleSeries.
        const padding = { top: 80, right: 40, bottom: 40, left: this.unit ? 120 : 90 };
        const chartWidth = svgWidth - padding.left - padding.right;
        const chartHeight = svgHeight - padding.top - padding.bottom;

        // Global min/max across all series
        let allValues = [];
        for (const key of this.seriesOrder) {
            allValues = allValues.concat(this.seriesMap[key].map(d => d.value));
        }
        const maxValue = Math.max(...allValues);
        const minValue = Math.min(...allValues);
        const { yMin, yMax } = this._niceScale(minValue, maxValue);

        // Draw grid lines and Y-axis labels
        this._drawGrid(yMin, yMax, padding, svgWidth, svgHeight, chartWidth, chartHeight);

        // Colors for series
        const colors = this.getSeriesColors(this.seriesOrder.length);

        // Use first series for X-axis labels (all should share timestamps)
        const refData = this.seriesMap[this.seriesOrder[0]];
        const numPoints = refData.length;
        // Dense series drop per-point dots (kept for single-point series, whose
        // dot is the only mark). See renderSingleSeries for rationale.
        const isDense = numPoints > 30;
        // See renderSingleSeries: dots only for sparse opt-in charts; monitoring
        // and dense charts stay smooth and use the crosshair.
        const drawDots = !isDense && this.config.showDots;

        // X-axis labels
        this._drawXLabels(refData, numPoints, padding, svgWidth, chartWidth);

        // Draw each series
        const hoverSeries = [];
        this.seriesOrder.forEach((seriesName, seriesIdx) => {
            const seriesData = this.seriesMap[seriesName];
            const color = colors[seriesIdx % colors.length];

            // Calculate points
            const points = seriesData.map((item, index) => {
                const x = padding.left + (chartWidth / Math.max(numPoints - 1, 1)) * index;
                const normalizedValue = (item.value - yMin) / (yMax - yMin);
                const y = padding.top + chartHeight - (normalizedValue * chartHeight);
                return { x, y, label: item.label, value: item.value, series: seriesName, seriesColor: color };
            });
            hoverSeries.push({ name: seriesName, color, points });

            // Draw line (skip for single-point series — no line to draw)
            if (points.length > 1) {
                const linePath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                let lineD = `M ${points[0].x},${points[0].y}`;
                points.forEach((p, i) => {
                    if (i > 0) lineD += ` L ${p.x},${p.y}`;
                });
                linePath.setAttribute('d', lineD);
                linePath.setAttribute('stroke', color);
                linePath.setAttribute('stroke-width', this.config.lineWidth);
                linePath.setAttribute('fill', 'none');
                linePath.setAttribute('stroke-linecap', 'round');
                linePath.setAttribute('stroke-linejoin', 'round');

                // Animate
                const pathLength = linePath.getTotalLength();
                linePath.style.strokeDasharray = pathLength;
                linePath.style.strokeDashoffset = pathLength;
                this.lineGroup.appendChild(linePath);

                this._scheduleTimer(() => {
                    linePath.style.transition = `stroke-dashoffset ${this.config.animationDuration}ms cubic-bezier(0.4, 0, 0.2, 1)`;
                    linePath.style.strokeDashoffset = '0';
                }, 50 + seriesIdx * 200);
            }

            // Draw points (larger dots for single-point series)
            const isSinglePoint = points.length === 1;
            points.forEach((point, index) => {
                if (!drawDots && !isSinglePoint) return;
                const circle = document.createElement('div');
                const size = isSinglePoint ? (this.config.pointRadius + 2) * 2 : this.config.pointRadius * 2;
                const xPercent = (point.x / svgWidth) * 100;
                const yPercent = (point.y / svgHeight) * 100;

                circle.style.position = 'absolute';
                circle.style.left = `${xPercent}%`;
                circle.style.top = `${yPercent}%`;
                circle.style.width = `${size}px`;
                circle.style.height = `${size}px`;
                circle.style.borderRadius = '50%';
                circle.style.backgroundColor = color;
                circle.style.border = 'none';
                circle.style.transform = 'translate(-50%, -50%) scale(0)';
                circle.style.cursor = 'pointer';
                circle.style.opacity = '0';
                circle.style.pointerEvents = 'auto';
                circle.style.boxSizing = 'border-box';

                circle.addEventListener('mouseenter', () => {
                    const hoverSize = (this.config.pointRadius + 2) * 2;
                    circle.style.width = `${hoverSize}px`;
                    circle.style.height = `${hoverSize}px`;
                    this.showTooltip(point);
                });
                circle.addEventListener('mouseleave', () => {
                    circle.style.width = `${size}px`;
                    circle.style.height = `${size}px`;
                    this.hideTooltip();
                });

                this.pointsContainer.appendChild(circle);

                const delay = 800 + seriesIdx * 200 + (index * 40);
                this._scheduleTimer(() => {
                    circle.style.transition = 'opacity 400ms ease-out, transform 400ms cubic-bezier(0.34, 1.56, 0.64, 1), width 200ms ease-out, height 200ms ease-out';
                    circle.style.opacity = '1';
                    circle.style.transform = 'translate(-50%, -50%) scale(1)';
                }, delay);
            });
        });

        this._setHoverModel(!drawDots, hoverSeries, refData.map((d) => d.label), padding, chartWidth, chartHeight, numPoints);

        // Legend
        this.legendContainer = document.createElement('div');
        this.legendContainer.style.display = 'flex';
        this.legendContainer.style.justifyContent = 'center';
        this.legendContainer.style.gap = '16px';
        this.legendContainer.style.flexWrap = 'wrap';
        this.legendContainer.style.padding = '4px 0';
        this.legendContainer.style.fontSize = '11px';
        this.legendContainer.style.color = '#9CA3AF';
        // compactLegend (monitoring): keep the legend to a single row so it never
        // starves the flex:1 plot. Don't wrap; scroll horizontally so every series
        // stays reachable. Opt-in; Reporting/chat unchanged.
        if (this.config.compactLegend) {
            this.legendContainer.style.flexWrap = 'nowrap';
            this.legendContainer.style.justifyContent = 'flex-start';
            this.legendContainer.style.overflowX = 'auto';
            this.legendContainer.style.overflowY = 'hidden';
            this.legendContainer.style.flexShrink = '0';
        }

        this.seriesOrder.forEach((name, i) => {
            const item = document.createElement('div');
            item.style.display = 'flex';
            item.style.alignItems = 'center';
            item.style.gap = '4px';
            // Single-row legend: keep each item at full width so the row scrolls
            // horizontally rather than squeezing items.
            if (this.config.compactLegend) item.style.flexShrink = '0';

            const dot = document.createElement('div');
            dot.style.width = '8px';
            dot.style.height = '8px';
            dot.style.borderRadius = '50%';
            dot.style.backgroundColor = colors[i % colors.length];

            const text = document.createElement('span');
            if (this.config.compactLegend) text.style.whiteSpace = 'nowrap';
            text.textContent = name;

            item.appendChild(dot);
            item.appendChild(text);
            this.legendContainer.appendChild(item);
        });

        this.wrapper.appendChild(this.legendContainer);
    }

    _formatTooltipLabel(label) {
        if (!label) return label;
        const s = String(label);
        // ISO date or datetime → "Mar 15, 2026". The time portion is
        // always dropped — only the day-level date is shown.
        const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
        if (m) {
            const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            return `${months[parseInt(m[2],10)-1]} ${parseInt(m[3],10)}, ${m[1]}`;
        }
        return s;
    }

    showTooltip(point) {
        const displayLabel = this._formatTooltipLabel(point.label);
        // Match the value to the line/series colour so the tooltip reads
        // visually anchored to the chart it belongs to. Date stays neutral.
        const valueColor = point.seriesColor || this.config.lineColor[0];
        // Always render on two lines: series+date on top, value beneath. The
        // previous layout stacked series, date and value on separate rows
        // for multi-series charts which made the tooltip taller than needed.
        const topLine = point.series
            ? `<div style="display: flex; align-items: center; gap: 6px; margin-bottom: 2px;">
                    <span style="width: 8px; height: 8px; border-radius: 50%; background-color: ${point.seriesColor || '#9CA3AF'}; flex-shrink: 0;"></span>
                    <span style="color: ${point.seriesColor || '#9CA3AF'}; font-size: 11px;">${point.series}</span>
                    <span style="margin-left: auto; font-weight: 600;">${displayLabel}</span>
                </div>`
            : `<div style="font-weight: 600; margin-bottom: 2px;">${displayLabel}</div>`;
        this.tooltip.innerHTML = `
            ${topLine}
            <div style="font-size: 14px; font-weight: 600; color: ${valueColor};">${this._formatValue(point.value)}</div>
        `;

        const svgRect = this.svg.getBoundingClientRect();
        const wrapperRect = this.wrapper.getBoundingClientRect();
        const svgWidth = 1000;
        const svgHeight = 400;

        const relativeX = svgRect.left - wrapperRect.left + (point.x / svgWidth) * svgRect.width;
        const relativeY = svgRect.top - wrapperRect.top + (point.y / svgHeight) * svgRect.height;

        const tooltipRect = this.tooltip.getBoundingClientRect();

        // Clamp tooltip within the wrapper bounds
        let left = relativeX - tooltipRect.width / 2;
        let top = relativeY - tooltipRect.height - 10;
        const maxLeft = wrapperRect.width - tooltipRect.width - 4;
        if (left < 4) left = 4;
        if (left > maxLeft) left = maxLeft;
        if (top < 4) top = relativeY + 16; // flip below point if clipped at top

        this.tooltip.style.left = left + 'px';
        this.tooltip.style.top = top + 'px';
        this.tooltip.style.opacity = '1';
    }

    hideTooltip() {
        this.tooltip.style.opacity = '0';
    }

    // Escape HTML-special chars before interpolating label/series text into the
    // tooltip's innerHTML — series names come from Prometheus label values.
    _esc(s) {
        return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    }

    // Store what the crosshair needs (per-series y/value by index, x labels, plot
    // geometry) and arm the hover overlay whenever the chart has no per-point dots
    // to hover (dense series, or showDots:false). Sparse dotted charts keep their
    // per-point dot tooltips. Reset the cached index so the next move rebuilds
    // against the fresh data instead of reusing a stale bucket after a refresh.
    _setHoverModel(armCrosshair, seriesList, xLabels, padding, chartWidth, chartHeight, numPoints) {
        this._hoverIndex = -1;
        if (armCrosshair && seriesList.length && numPoints > 1) {
            this._hover = { seriesList, xLabels, padding, chartWidth, chartHeight, numPoints };
            this.hoverOverlay.style.pointerEvents = 'auto';
        } else {
            this._hover = null;
            this.hoverOverlay.style.pointerEvents = 'none';
            if (this._crosshairGroup) this._crosshairGroup.innerHTML = '';
        }
    }

    _onHoverMove(e) {
        const h = this._hover;
        if (!h) return;
        const svgRect = this.svg.getBoundingClientRect();
        if (!svgRect.width) return;

        // Cursor x → SVG x (viewBox 0..1000) → nearest data index.
        const svgX = ((e.clientX - svgRect.left) / svgRect.width) * 1000;
        const stepX = h.chartWidth / Math.max(h.numPoints - 1, 1);
        let i = Math.round((svgX - h.padding.left) / stepX);
        i = Math.max(0, Math.min(h.numPoints - 1, i));

        // The guide + tooltip content depend only on the index, so rebuild them
        // (the costly part — DOM markup for every series + a sort) only when the
        // cursor crosses into a new x-bucket. The repositioning below still runs
        // every move so the tooltip keeps following the cursor.
        if (i !== this._hoverIndex) {
            this._hoverIndex = i;
            const gx = h.padding.left + stepX * i;
            let guide = `<line x1="${gx}" y1="${h.padding.top}" x2="${gx}" y2="${h.padding.top + h.chartHeight}" stroke="#9CA3AF" stroke-width="1" stroke-opacity="0.45"/>`;
            const rows = [];
            for (const s of h.seriesList) {
                const p = s.points[i];
                if (!p || !Number.isFinite(p.value)) continue;
                guide += `<circle cx="${gx}" cy="${p.y}" r="3.5" fill="${s.color}"/>`;
                rows.push({ name: s.name, color: s.color, value: p.value });
            }
            this._crosshairGroup.innerHTML = guide;

            // Tooltip: timestamp header + up to 10 series, largest value first.
            rows.sort((a, b) => b.value - a.value);
            const MAX_ROWS = 10;
            const shown = rows.slice(0, MAX_ROWS);
            let html = `<div style="font-weight:600; margin-bottom:4px;">${this._esc(this._formatTooltipLabel(h.xLabels[i]))}</div>`;
            for (const r of shown) {
                const name = r.name ? `<span style="color:${r.color};">${this._esc(r.name)}</span>` : '';
                html += `<div style="display:flex; align-items:center; gap:6px; white-space:nowrap;">`
                    + `<span style="width:8px;height:8px;border-radius:50%;background:${r.color};flex-shrink:0;"></span>`
                    + `${name}<span style="margin-left:auto; padding-left:12px; font-weight:600;">${this._formatValue(r.value)}</span></div>`;
            }
            if (rows.length > MAX_ROWS) {
                html += `<div style="opacity:0.6; margin-top:2px;">+${rows.length - MAX_ROWS} more</div>`;
            }
            this.tooltip.innerHTML = html;
        }

        // Position near the cursor, clamped to the wrapper.
        const wrapperRect = this.wrapper.getBoundingClientRect();
        const curX = e.clientX - wrapperRect.left;
        const curY = e.clientY - wrapperRect.top;
        const tr = this.tooltip.getBoundingClientRect();
        let left = curX + 14;
        if (left + tr.width > wrapperRect.width - 4) left = curX - tr.width - 14;
        if (left < 4) left = 4;
        let top = curY - tr.height - 10;
        if (top < 4) top = curY + 16;
        this.tooltip.style.left = left + 'px';
        this.tooltip.style.top = top + 'px';
        this.tooltip.style.opacity = '1';
    }

    _onHoverLeave() {
        this._hoverIndex = -1;
        if (this._crosshairGroup) this._crosshairGroup.innerHTML = '';
        this.hideTooltip();
    }

    resize(gridSize) {
        if (this.config.gridSize === gridSize) return;
        this.config.gridSize = gridSize;
        const currentData = this.data;
        this.setupDOM();
        if (currentData) {
            this.setData(currentData);
        }
    }
}

// Export for module systems or global scope
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNALineChart;
}

// Make available globally for browser usage
if (typeof window !== 'undefined') {
    window.DotDNALineChart = DotDNALineChart;
}
