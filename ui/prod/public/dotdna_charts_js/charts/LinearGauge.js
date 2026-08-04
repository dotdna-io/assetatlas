/**
 * LinearGauge - Horizontal bar gauge with tri-color segments
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 *
 * The bar fills progressively with color transitions:
 * - 0-33%: Red
 * - 33-66%: Orange (only the 33-66 segment)
 * - 66-100%: Green (only the 66-100 segment)
 *
 * Sizes: 1x1, 2x1
 */

class DotDNALinearGauge extends DotDNASVGChartBase {
    constructor(containerId, options = {}) {
        super(containerId, options);

        this.value = options.value !== undefined ? options.value : 75;
        this.maxValue = options.maxValue || 100;
        this.label = options.label || '';
        this.showPercentage = options.showPercentage !== undefined ? options.showPercentage : true;
        this.shouldAnimate = options.animate !== undefined ? options.animate : true;
        this.thresholdLow = options.thresholdLow ?? 33;
        this.thresholdHigh = options.thresholdHigh ?? 66;
        this.invertColors = options.invertColors ?? false;
        this.dashedSegments = options.dashedSegments ?? false;

        // When the React wrapper remounts the chart (e.g. on a config-toggle
        // key change), the constructor receives the current preview data —
        // prefer the real value/label over the placeholder from defaultOptions
        // so the very first animation lands on the correct number.
        if (Array.isArray(options.data) && options.data[0]) {
            const item = options.data[0];
            if (item.value !== undefined) this.value = item.value;
            if (item.label !== undefined) this.label = item.label;
        }

        this.layoutSize = options.gridSize || this.detectLayoutSize();

        // Only render initially if explicit data/value was provided. When
        // wrapped by DotDNAChart in a React flow, the constructor receives
        // `data: []` while the real data loads — rendering with the default
        // value of 75 causes a redundant entry animation before setData()
        // is called with the actual value.
        if (this._hasInitialData(options)) {
            this.render();
        }
    }

    _hasInitialData(options) {
        // Array data (even empty) means the React wrapper is driving — wait
        // for setData() so the default `value` doesn't trigger a stub render.
        if (Array.isArray(options.data)) return options.data.length > 0;
        if (options.data !== undefined) return true;
        if (options.value !== undefined) return true;
        return false;
    }

    detectLayoutSize() {
        const rect = this.container.getBoundingClientRect();
        if (rect.width > rect.height * 1.3) return '2x1';
        return '1x1';
    }

    getSegmentColors() {
        const palette = window.ChartPalette;
        const red = palette ? palette.colors(0) : ['#EF4444', '#DC2626'];
        const amber = palette ? palette.colors(1) : ['#F59E0B', '#D97706'];
        const green = palette ? palette.colors(3) : ['#10B981', '#059669'];
        return this.invertColors
            ? { red: green, amber, green: red }
            : { red, amber, green };
    }

    render() {
        this._hasRendered = true;
        this.container.innerHTML = '';
        this.container.className = 'dotdna-chart';
        this.container.style.width = '100%';
        this.container.style.height = '100%';
        this.container.style.display = 'flex';
        this.container.style.alignItems = 'center';
        this.container.style.justifyContent = 'center';
        this.container.style.overflow = 'hidden';

        const rect = this.container.getBoundingClientRect();
        const w = rect.width;
        const h = rect.height;
        const centerX = w / 2;
        const centerY = h / 2;

        // Theme-dependent colours applied via CSS classes (`.gauge-pct`,
        // `.gauge-track-fill`, `.gauge-track-stroke`) so theme switches
        // update immediately without re-rendering the SVG.

        const pct = Math.round((this.value / this.maxValue) * 100);
        const percentage = this.value / this.maxValue;
        const colors = this.getSegmentColors();

        // Bar dimensions
        const is2x1 = this.layoutSize === '2x1';
        const barHeight = is2x1 ? Math.min(h * 0.10, 14) : Math.min(h * 0.08, 12);
        const barPadX = is2x1 ? w * 0.08 : w * 0.12;
        const barWidth = w - barPadX * 2;
        const barY = centerY + (is2x1 ? h * 0.22 : h * 0.18);
        const barRadius = barHeight / 2;

        // Font size (+10% from base)
        let fontSize = is2x1 ? 70 : 62;
        if (pct === 100 && this.layoutSize === '1x1') fontSize = 50;
        if (pct === 100 && is2x1) fontSize = 57;
        const textY = centerY - (is2x1 ? h * 0.02 : h * 0.02);

        // Create SVG
        this.createSVG();
        this.svg.setAttribute('viewBox', `0 0 ${w} ${h}`);

        const uid = `lg-${Date.now()}`;
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');

        // Clip path for rounded bar
        const clipPath = document.createElementNS('http://www.w3.org/2000/svg', 'clipPath');
        clipPath.setAttribute('id', `${uid}-clip`);
        const clipRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        clipRect.setAttribute('x', barPadX);
        clipRect.setAttribute('y', barY);
        clipRect.setAttribute('width', barWidth);
        clipRect.setAttribute('height', barHeight);
        clipRect.setAttribute('rx', barRadius);
        clipRect.setAttribute('ry', barRadius);
        clipPath.appendChild(clipRect);
        defs.appendChild(clipPath);

        // The bar represents a single percentage (count / total), so the
        // entire fill takes one colour determined by where pct lands relative
        // to the thresholds. `getSegmentColors()` already honours invertColors.
        const fillColors = pct < this.thresholdLow ? colors.red
            : pct < this.thresholdHigh ? colors.amber
            : colors.green;

        const grad = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
        grad.setAttribute('id', `${uid}-fill`);
        // userSpaceOnUse: in dashed mode the fill is a horizontal `<line>`
        // whose objectBoundingBox has zero height — percentage gradient
        // stops collapse and the stroke renders as no-paint. Fixed
        // user-space coordinates avoid that and also make the rect-fill
        // gradient stable as the bar grows.
        grad.setAttribute('gradientUnits', 'userSpaceOnUse');
        grad.setAttribute('x1', barPadX);
        grad.setAttribute('y1', barY + barHeight / 2);
        grad.setAttribute('x2', barPadX + barWidth);
        grad.setAttribute('y2', barY + barHeight / 2);
        const fs1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        fs1.setAttribute('offset', '0%');
        fs1.setAttribute('stop-color', fillColors[0]);
        const fs2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        fs2.setAttribute('offset', '100%');
        fs2.setAttribute('stop-color', fillColors[1]);
        grad.appendChild(fs1);
        grad.appendChild(fs2);
        defs.appendChild(grad);

        this.svg.appendChild(defs);

        // Dashed mode renders both track and fill as horizontal `<line>`s
        // with stroke-dasharray to create the brick pattern. Continuous mode
        // keeps the existing rounded rect approach.
        const dashed = this.dashedSegments;
        const targetWidth = percentage * barWidth;
        const barCenterY = barY + barHeight / 2;

        let dashArray = null;
        if (dashed) {
            // Bricks scale with bar height so they read consistently across
            // 1x1 / 2x1 layouts. Aim for slot ≈ 1.4x bar height.
            const numBricks = Math.max(8, Math.round(barWidth / (barHeight * 1.4)));
            const slotLen = barWidth / numBricks;
            const brickLen = slotLen * 0.7;
            const gapLen = slotLen - brickLen;
            dashArray = `${brickLen} ${gapLen}`;
        }

        // Track bar (background)
        let track;
        if (dashed) {
            track = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            track.setAttribute('x1', barPadX);
            track.setAttribute('y1', barCenterY);
            track.setAttribute('x2', barPadX + barWidth);
            track.setAttribute('y2', barCenterY);
            track.setAttribute('class', 'gauge-track-stroke');
            track.setAttribute('stroke-width', barHeight);
            track.setAttribute('stroke-linecap', 'butt');
            track.setAttribute('stroke-dasharray', dashArray);
        } else {
            track = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            track.setAttribute('x', barPadX);
            track.setAttribute('y', barY);
            track.setAttribute('width', barWidth);
            track.setAttribute('height', barHeight);
            track.setAttribute('rx', barRadius);
            track.setAttribute('ry', barRadius);
            track.setAttribute('class', 'gauge-track-fill');
        }
        this.svg.appendChild(track);

        // Fill: in continuous mode it's a rect inside the rounded clipPath;
        // in dashed mode it's a horizontal line with the same dasharray.
        const clipGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        if (!dashed) clipGroup.setAttribute('clip-path', `url(#${uid}-clip)`);

        let fillRect = null;
        let fillLine = null;
        if (dashed) {
            fillLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            fillLine.setAttribute('x1', barPadX);
            fillLine.setAttribute('y1', barCenterY);
            fillLine.setAttribute('x2', barPadX);
            fillLine.setAttribute('y2', barCenterY);
            fillLine.setAttribute('stroke', `url(#${uid}-fill)`);
            fillLine.setAttribute('stroke-width', barHeight);
            fillLine.setAttribute('stroke-linecap', 'butt');
            fillLine.setAttribute('stroke-dasharray', dashArray);
            clipGroup.appendChild(fillLine);
        } else {
            fillRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            fillRect.setAttribute('x', barPadX);
            fillRect.setAttribute('y', barY);
            fillRect.setAttribute('width', 0);
            fillRect.setAttribute('height', barHeight);
            fillRect.setAttribute('fill', `url(#${uid}-fill)`);
            clipGroup.appendChild(fillRect);
        }

        this.svg.appendChild(clipGroup);

        // Percentage text
        const maxTextX = barPadX + barWidth - w * 0.12;
        const finalTextX = is2x1 ? Math.min(barPadX + percentage * barWidth, maxTextX) : centerX;
        const pctText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        pctText.setAttribute('x', is2x1 ? barPadX : centerX);
        pctText.setAttribute('y', textY);
        pctText.setAttribute('text-anchor', is2x1 ? 'middle' : 'middle');
        pctText.setAttribute('dominant-baseline', 'middle');
        pctText.setAttribute('font-size', fontSize);
        pctText.setAttribute('font-weight', '300');
        pctText.setAttribute('letter-spacing', '-0.02em');
        pctText.setAttribute('class', 'gauge-pct');
        pctText.setAttribute('opacity', '0');
        pctText.textContent = '0%';
        this.svg.appendChild(pctText);

        // Animate
        if (this.shouldAnimate) {
            const duration = 2000;
            const startTime = Date.now();
            const easeOut = (t) => 1 - Math.pow(1 - t, 3);

            const animate = () => {
                const elapsed = Date.now() - startTime;
                const progress = Math.min(elapsed / duration, 1);
                const eased = easeOut(progress);
                const curFraction = percentage * eased;

                if (fillLine) {
                    fillLine.setAttribute('x2', barPadX + curFraction * barWidth);
                } else {
                    fillRect.setAttribute('width', curFraction * barWidth);
                }

                // Counter
                const curPct = Math.round(pct * eased);
                pctText.textContent = curPct + '%';

                // In 2x1, move text X with the bar edge
                if (is2x1) {
                    const curX = Math.min(barPadX + curFraction * barWidth, maxTextX);
                    pctText.setAttribute('x', curX);
                }

                // Fade in text
                if (progress > 0.1) {
                    const tp = Math.min(1, (progress - 0.1) / 0.3);
                    pctText.setAttribute('opacity', tp);
                }

                if (progress < 1) requestAnimationFrame(animate);
                else {
                    pctText.textContent = pct + '%';
                    if (is2x1) pctText.setAttribute('x', finalTextX);
                }
            };
            requestAnimationFrame(animate);
        } else {
            if (fillLine) {
                fillLine.setAttribute('x2', barPadX + targetWidth);
            } else {
                fillRect.setAttribute('width', targetWidth);
            }
            pctText.setAttribute('opacity', '1');
            pctText.textContent = pct + '%';
            if (is2x1) pctText.setAttribute('x', finalTextX);
        }
    }

    setData(data) {
        if (!data) return;
        const item = Array.isArray(data) ? data[0] : data;
        if (!item) return;
        if (item.value !== undefined) this.value = item.value;
        if (item.label !== undefined) this.label = item.label;
        this.render();
    }

    resize(gridSize) {
        this.layoutSize = gridSize;
        // Only re-render if we've already done an initial render. While
        // waiting for data, resize() must not trigger a stub render with
        // the default value — that's what causes the double-animation bug.
        if (this._hasRendered) this.render();
    }

    refresh() {
        this.container.innerHTML = '';
        this.svg = null;
        this.render();
    }

    destroy() {
        if (this.container) this.container.innerHTML = '';
    }
}

if (typeof window !== 'undefined') {
    window.DotDNALinearGauge = DotDNALinearGauge;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNALinearGauge;
}
