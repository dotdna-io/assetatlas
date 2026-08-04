/**
 * CircleGauge - Full circle gauge with tri-color segments
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 *
 * The circle fills progressively with color transitions:
 * - 0-33%: Red
 * - 33-66%: Orange (only the 33-66 segment)
 * - 66-100%: Green (only the 66-100 segment)
 *
 * Sizes: 1x1, 1x2
 */

class DotDNACircleGauge extends DotDNASVGChartBase {
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
        if (rect.height > rect.width * 1.3) return '1x2';
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

        // Theme-dependent colours (text fill, track stroke) are applied via
        // CSS classes (see dotdna.css `.gauge-pct`, `.gauge-track-stroke`)
        // so theme switches update immediately without needing to re-render
        // the SVG.

        const pct = Math.round((this.value / this.maxValue) * 100);
        const percentage = this.value / this.maxValue;
        const colors = this.getSegmentColors();

        const dim = Math.min(w, h);
        const strokeWidth = dim * 0.085;
        const radius = ((dim / 2) - strokeWidth - 2) * 0.95;
        const fontSize = Math.round(radius * 0.55);

        // Default rendering is a continuous arc. When `dashedSegments` is on,
        // the stroke is broken into 36 small "domino" bricks evenly distributed
        // around the full ring. Paths are clamped to 0.9999 so SVG doesn't see
        // a degenerate start = end arc — well below pixel resolution.
        const numBricks = 36;
        const fullRing = 0.9999;
        const slotLen = (2 * Math.PI * radius) / numBricks;
        const brickLen = slotLen * 0.7;
        const gapLen = slotLen - brickLen;
        const dashArray = this.dashedSegments ? `${brickLen} ${gapLen}` : null;

        // Create SVG
        this.createSVG();
        this.svg.setAttribute('viewBox', `0 0 ${w} ${h}`);

        const uid = `cg-${Date.now()}`;
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');

        // The gauge represents a single percentage (count / total), so the
        // entire fill takes one colour determined by where pct lands relative
        // to the thresholds. `getSegmentColors()` already honours invertColors,
        // so picking by key here gives the right behaviour in both modes.
        const fillColors = pct < this.thresholdLow ? colors.red
            : pct < this.thresholdHigh ? colors.amber
            : colors.green;

        const grad = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
        grad.setAttribute('id', `${uid}-fill`);
        grad.setAttribute('x1', '0%');
        grad.setAttribute('y1', '0%');
        grad.setAttribute('x2', '100%');
        grad.setAttribute('y2', '100%');
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

        // Helper: arc path string between two circumference-fraction
        // positions. Both paths start from the top going clockwise so the
        // dasharray pattern aligns between track and fill.
        const startAngleDeg = -90; // top of circle
        const arcPathBetween = (startFrac, endFrac) => {
            const sa = startAngleDeg + startFrac * 360;
            const ea = startAngleDeg + endFrac * 360;
            const sr = (sa * Math.PI) / 180;
            const er = (ea * Math.PI) / 180;
            const x1 = centerX + radius * Math.cos(sr);
            const y1 = centerY + radius * Math.sin(sr);
            const x2 = centerX + radius * Math.cos(er);
            const y2 = centerY + radius * Math.sin(er);
            const largeArc = (endFrac - startFrac) > 0.5 ? 1 : 0;
            return `M ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2}`;
        };

        // Track: full ring, clamped to 0.9999 of the circumference
        const track = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        track.setAttribute('d', arcPathBetween(0, fullRing));
        track.setAttribute('fill', 'none');
        track.setAttribute('class', 'gauge-track-stroke');
        track.setAttribute('stroke-width', strokeWidth);
        if (dashArray) track.setAttribute('stroke-dasharray', dashArray);
        track.setAttribute('stroke-linecap', 'butt');
        this.svg.appendChild(track);

        // Single fill arc covering [0, percentage] of the value range.
        const fillArc = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        fillArc.setAttribute('d', arcPathBetween(0, Math.min(percentage, fullRing)));
        fillArc.setAttribute('fill', 'none');
        fillArc.setAttribute('stroke', `url(#${uid}-fill)`);
        fillArc.setAttribute('stroke-width', strokeWidth);
        fillArc.setAttribute('stroke-linecap', 'butt');
        if (dashArray) fillArc.setAttribute('stroke-dasharray', dashArray);
        fillArc.setAttribute('opacity', '0');
        this.svg.appendChild(fillArc);

        // Center percentage text
        const pctText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        pctText.setAttribute('x', centerX);
        pctText.setAttribute('y', centerY);
        pctText.setAttribute('text-anchor', 'middle');
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
                const curPercentage = percentage * eased;

                fillArc.setAttribute(
                    'd',
                    arcPathBetween(0, Math.min(curPercentage, fullRing)),
                );
                fillArc.setAttribute('opacity', '1');

                // Counter
                const curPct = Math.round(pct * eased);
                pctText.textContent = curPct + '%';

                // Fade in text
                if (progress > 0.1) {
                    const tp = Math.min(1, (progress - 0.1) / 0.3);
                    pctText.setAttribute('opacity', tp);
                }

                if (progress < 1) requestAnimationFrame(animate);
                else pctText.textContent = pct + '%';
            };
            requestAnimationFrame(animate);
        } else {
            fillArc.setAttribute('opacity', '1');
            pctText.setAttribute('opacity', '1');
            pctText.textContent = pct + '%';
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
    window.DotDNACircleGauge = DotDNACircleGauge;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNACircleGauge;
}
