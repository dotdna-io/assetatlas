/**
 * DotDNAElegantGauge - Adaptive gauge with three layout modes
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 *
 * Layouts:
 * - 1x1: Horizontal line gauge with sphere indicator, large % text
 * - 1x2: Vertical/tall line gauge, thicker bar, large % text
 * - 2x1: Classic circular arc gauge with sliding sphere
 */

class DotDNAElegantGauge extends DotDNASVGChartBase {
    static metadata = {
        id: 'c5d6e7f8-a9b0-41c2-d3e4-f5a6b7c8d9e0',
        name: 'Gauge',
        oldId: 'elegant-gauge'
    };

    constructor(containerId, options = {}) {
        super(containerId, options);

        this.value = options.value !== undefined ? options.value : 75;
        this.maxValue = options.maxValue || 100;
        this.showPercentage = options.showPercentage !== undefined ? options.showPercentage : true;
        this.shouldAnimate = options.animate !== undefined ? options.animate : true;
        this.thresholdLow = options.thresholdLow ?? 33;
        this.thresholdHigh = options.thresholdHigh ?? 66;
        this.invertColors = options.invertColors ?? false;

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
        const w = rect.width;
        const h = rect.height;
        if (h > w * 1.3) return '1x2';  // tall → circle
        return '1x1';                    // square → compact line
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

        if (this.layoutSize === '1x2') {
            this.renderCircular();
        } else {
            this.renderLineCompact();
        }
    }

    // ==========================================
    // 1x1 — Smile arc gauge, compact
    // ==========================================
    renderLineCompact() {
        this.renderSmileArc({
            fontSize: 72,
            strokeWidth: 6,
            sphereRadius: 7,
            arcPaddingX: 30,
            arcBottomMargin: 50,
            arcDepth: 0.08
        });
    }

    /**
     * Shared smile-arc renderer for 1x1 and 2x1 layouts
     * Draws a shallow downward arc (smile) at the bottom of the card
     * with percentage text centered above it
     */
    renderSmileArc(cfg) {
        const rect = this.container.getBoundingClientRect();
        const w = rect.width;
        const h = rect.height;

        const isDark = document.body.classList.contains('dark-mode');
        const pct = Math.round((this.value / this.maxValue) * 100);
        const percentage = this.value / this.maxValue;
        const colors = this.getGradientColors(pct);
        const textColor = isDark ? '#FFFFFF' : '#111827';
        const trackColor = isDark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.08)';

        // Create SVG filling the container
        this.createSVG();
        this.svg.setAttribute('viewBox', `0 0 ${w} ${h}`);

        const uid = `smile-${Date.now()}`;
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');

        // Ball gradient
        const ballGrad = document.createElementNS('http://www.w3.org/2000/svg', 'radialGradient');
        ballGrad.setAttribute('id', `${uid}-ball`);
        ballGrad.setAttribute('cx', '30%');
        ballGrad.setAttribute('cy', '30%');
        const bs1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        bs1.setAttribute('offset', '0%');
        bs1.setAttribute('stop-color', isDark ? '#a8a8a8' : '#9ca3af');
        const bs2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        bs2.setAttribute('offset', '100%');
        bs2.setAttribute('stop-color', isDark ? '#6b6b6b' : '#6b7280');
        ballGrad.appendChild(bs1);
        ballGrad.appendChild(bs2);
        defs.appendChild(ballGrad);

        // Value arc gradient
        const valGrad = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
        valGrad.setAttribute('id', `${uid}-val`);
        valGrad.setAttribute('x1', '0%');
        valGrad.setAttribute('y1', '0%');
        valGrad.setAttribute('x2', '100%');
        valGrad.setAttribute('y2', '0%');
        const vs1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        vs1.setAttribute('offset', '0%');
        vs1.setAttribute('stop-color', colors[0]);
        const vs2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        vs2.setAttribute('offset', '100%');
        vs2.setAttribute('stop-color', colors[1]);
        valGrad.appendChild(vs1);
        valGrad.appendChild(vs2);
        defs.appendChild(valGrad);

        this.svg.appendChild(defs);

        // Smile arc geometry
        // Arc endpoints: left and right, raised above the bottom edge
        const arcX1 = cfg.arcPaddingX;
        const arcX2 = w - cfg.arcPaddingX;
        const arcWidth = arcX2 - arcX1;
        const arcY = h - cfg.arcBottomMargin;
        const arcSag = arcWidth * cfg.arcDepth; // how deep the smile dips

        // Use a large radius circle to create a shallow arc
        // For a chord of length arcWidth with sagitta arcSag:
        // radius = (arcWidth^2) / (8 * arcSag) + arcSag / 2
        const radius = (arcWidth * arcWidth) / (8 * arcSag) + arcSag / 2;

        // Center of the arc circle is above the arc
        const arcCenterX = (arcX1 + arcX2) / 2;
        const arcCenterY = arcY - (radius - arcSag);

        // Calculate start/end angles for the smile arc
        const startAngle = Math.atan2(arcY - arcCenterY, arcX1 - arcCenterX);
        const endAngle = Math.atan2(arcY - arcCenterY, arcX2 - arcCenterX);

        // Helper to get point on arc at a fraction (0=left, 1=right)
        const pointOnArc = (fraction) => {
            const angle = startAngle + (endAngle - startAngle) * fraction;
            return {
                x: arcCenterX + radius * Math.cos(angle),
                y: arcCenterY + radius * Math.sin(angle)
            };
        };

        // Track arc path (full smile)
        const trackStart = pointOnArc(0);
        const trackEnd = pointOnArc(1);
        const trackPath = `M ${trackStart.x} ${trackStart.y} A ${radius} ${radius} 0 0 0 ${trackEnd.x} ${trackEnd.y}`;

        const track = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        track.setAttribute('d', trackPath);
        track.setAttribute('fill', 'none');
        track.setAttribute('stroke', trackColor);
        track.setAttribute('stroke-width', cfg.strokeWidth);
        track.setAttribute('stroke-linecap', 'round');
        this.svg.appendChild(track);

        // Value arc path (partial smile, from left to value position)
        const valEnd = pointOnArc(percentage);
        const valuePath = `M ${trackStart.x} ${trackStart.y} A ${radius} ${radius} 0 0 0 ${valEnd.x} ${valEnd.y}`;

        const valueArc = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        valueArc.setAttribute('d', valuePath);
        valueArc.setAttribute('fill', 'none');
        valueArc.setAttribute('stroke', `url(#${uid}-val)`);
        valueArc.setAttribute('stroke-width', cfg.strokeWidth);
        valueArc.setAttribute('stroke-linecap', 'round');
        valueArc.setAttribute('opacity', '0');
        this.svg.appendChild(valueArc);

        // Sphere indicator (starts at left)
        const sphereStart = pointOnArc(0);
        const indicator = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        indicator.setAttribute('cx', sphereStart.x);
        indicator.setAttribute('cy', sphereStart.y);
        indicator.setAttribute('r', cfg.sphereRadius);
        indicator.setAttribute('fill', `url(#${uid}-ball)`);
        indicator.setAttribute('filter', `drop-shadow(0 2px 4px rgba(${isDark ? '168,168,168' : '107,114,128'},0.4))`);
        this.svg.appendChild(indicator);

        // Percentage text — positioned close above the arc
        const arcTopY = arcY - arcSag;
        const textY = arcTopY - cfg.fontSize * 0.15;
        const pctText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        pctText.setAttribute('x', w / 2);
        pctText.setAttribute('y', textY);
        pctText.setAttribute('text-anchor', 'middle');
        pctText.setAttribute('dominant-baseline', 'middle');
        pctText.setAttribute('font-size', cfg.fontSize);
        pctText.setAttribute('font-weight', '300');
        pctText.setAttribute('letter-spacing', '-0.02em');
        pctText.setAttribute('fill', textColor);
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

                // Move sphere along arc
                const curFraction = percentage * eased;
                const pos = pointOnArc(curFraction);
                indicator.setAttribute('cx', pos.x);
                indicator.setAttribute('cy', pos.y);

                // Grow value arc
                const curEnd = pointOnArc(curFraction);
                const curPath = `M ${trackStart.x} ${trackStart.y} A ${radius} ${radius} 0 0 0 ${curEnd.x} ${curEnd.y}`;
                valueArc.setAttribute('d', curPath);
                valueArc.setAttribute('opacity', '0.5');

                // Animate counter
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
            const endPos = pointOnArc(percentage);
            indicator.setAttribute('cx', endPos.x);
            indicator.setAttribute('cy', endPos.y);
            valueArc.setAttribute('opacity', '0.5');
            pctText.setAttribute('opacity', '1');
            pctText.textContent = pct + '%';
        }
    }

    // ==========================================
    // 2x1 — Classic circular arc gauge
    // ==========================================
    renderCircular() {
        const rect = this.container.getBoundingClientRect();
        const w = rect.width;
        const h = rect.height;
        const centerX = w / 2;
        const centerY = h / 2 + 8;

        const radius = Math.min(w, h) * 0.42;
        const strokeWidth = 7;
        const indicatorRadius = 10;
        const fontSize = 60;

        const startAngle = 140;
        const endAngle = 400;
        const totalAngle = endAngle - startAngle;

        const isDark = document.body.classList.contains('dark-mode');
        const textColor = isDark ? '#FFFFFF' : '#1e293b';
        const trackColor = isDark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.08)';

        const pct = Math.round((this.value / this.maxValue) * 100);
        const percentage = this.value / this.maxValue;
        const colors = this.getGradientColors(pct);
        const valueAngle = startAngle + (totalAngle * percentage);

        // Create SVG
        this.createSVG();
        this.svg.setAttribute('viewBox', `0 0 ${w} ${h}`);

        const uid = `gauge-${Date.now()}`;
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');

        // Ball gradient
        const ballGrad = document.createElementNS('http://www.w3.org/2000/svg', 'radialGradient');
        ballGrad.setAttribute('id', `${uid}-ball`);
        ballGrad.setAttribute('cx', '30%');
        ballGrad.setAttribute('cy', '30%');
        const bs1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        bs1.setAttribute('offset', '0%');
        bs1.setAttribute('stop-color', isDark ? '#a8a8a8' : '#9ca3af');
        const bs2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        bs2.setAttribute('offset', '100%');
        bs2.setAttribute('stop-color', isDark ? '#6b6b6b' : '#6b7280');
        ballGrad.appendChild(bs1);
        ballGrad.appendChild(bs2);
        defs.appendChild(ballGrad);

        // Value gradient
        const valGrad = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
        valGrad.setAttribute('id', `${uid}-val`);
        valGrad.setAttribute('x1', '0%');
        valGrad.setAttribute('y1', '0%');
        valGrad.setAttribute('x2', '100%');
        valGrad.setAttribute('y2', '100%');
        const vs1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        vs1.setAttribute('offset', '0%');
        vs1.setAttribute('stop-color', colors[0]);
        const vs2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        vs2.setAttribute('offset', '100%');
        vs2.setAttribute('stop-color', colors[1]);
        valGrad.appendChild(vs1);
        valGrad.appendChild(vs2);
        defs.appendChild(valGrad);

        this.svg.appendChild(defs);

        // Track arc
        const track = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        track.setAttribute('d', this.createArcPathForGauge(centerX, centerY, radius, startAngle, endAngle));
        track.setAttribute('fill', 'none');
        track.setAttribute('stroke', trackColor);
        track.setAttribute('stroke-width', strokeWidth);
        track.setAttribute('stroke-linecap', 'round');
        this.svg.appendChild(track);

        // Value arc
        const valueArc = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        valueArc.setAttribute('d', this.createArcPathForGauge(centerX, centerY, radius, startAngle, valueAngle));
        valueArc.setAttribute('fill', 'none');
        valueArc.setAttribute('stroke', `url(#${uid}-val)`);
        valueArc.setAttribute('stroke-width', strokeWidth);
        valueArc.setAttribute('stroke-linecap', 'round');
        valueArc.setAttribute('opacity', '0.3');
        this.svg.appendChild(valueArc);

        // Sphere indicator
        const startPos = this.polarToCartesian(centerX, centerY, radius, startAngle);
        const indicator = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        indicator.setAttribute('cx', startPos.x);
        indicator.setAttribute('cy', startPos.y);
        indicator.setAttribute('r', indicatorRadius);
        indicator.setAttribute('fill', `url(#${uid}-ball)`);
        indicator.setAttribute('filter', `drop-shadow(0 2px 6px rgba(${isDark ? '168,168,168' : '107,114,128'},0.4))`);
        this.svg.appendChild(indicator);

        // Center text
        const centerText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        centerText.setAttribute('x', centerX);
        centerText.setAttribute('y', centerY);
        centerText.setAttribute('text-anchor', 'middle');
        centerText.setAttribute('dominant-baseline', 'middle');
        centerText.setAttribute('font-size', fontSize);
        centerText.setAttribute('font-weight', '300');
        centerText.setAttribute('letter-spacing', '-0.02em');
        centerText.setAttribute('fill', textColor);
        centerText.setAttribute('opacity', '0');
        centerText.textContent = this.showPercentage ? pct + '%' : this.value;
        this.svg.appendChild(centerText);

        // Animate
        if (this.shouldAnimate) {
            const duration = 2000;
            const startTime = Date.now();
            const easeOut = (t) => 1 - Math.pow(1 - t, 3);

            const animate = () => {
                const elapsed = Date.now() - startTime;
                const progress = Math.min(elapsed / duration, 1);
                const eased = easeOut(progress);

                // Move sphere along arc
                const curAngle = startAngle + (totalAngle * percentage * eased);
                const pos = this.polarToCartesian(centerX, centerY, radius, curAngle);
                indicator.setAttribute('cx', pos.x);
                indicator.setAttribute('cy', pos.y);

                // Grow value arc
                valueArc.setAttribute('d', this.createArcPathForGauge(centerX, centerY, radius, startAngle, curAngle));
                valueArc.setAttribute('opacity', '0.5');

                // Animate counter
                const curPct = Math.round(pct * eased);
                centerText.textContent = this.showPercentage ? curPct + '%' : Math.round(this.value * eased);

                // Fade in text
                if (progress > 0.1) {
                    const tp = Math.min(1, (progress - 0.1) / 0.3);
                    centerText.setAttribute('opacity', tp);
                }

                if (progress < 1) requestAnimationFrame(animate);
                else centerText.textContent = this.showPercentage ? pct + '%' : this.value;
            };
            requestAnimationFrame(animate);
        } else {
            const endPos = this.polarToCartesian(centerX, centerY, radius, valueAngle);
            indicator.setAttribute('cx', endPos.x);
            indicator.setAttribute('cy', endPos.y);
            centerText.setAttribute('opacity', '1');
        }
    }

    // ==========================================
    // Shared helpers
    // ==========================================

    getGradientColors(pct) {
        const low = this.thresholdLow;
        const high = this.thresholdHigh;
        const invert = this.invertColors;

        if (window.ChartPalette) {
            const red = window.ChartPalette.colors(0);
            const amber = window.ChartPalette.colors(1);
            const green = window.ChartPalette.colors(3);
            if (pct < low) return invert ? green : red;
            if (pct < high) return amber;
            return invert ? red : green;
        }
        const red = ['#EF4444', '#DC2626'];
        const amber = ['#F59E0B', '#D97706'];
        const green = ['#10B981', '#059669'];
        if (pct < low) return invert ? green : red;
        if (pct < high) return amber;
        return invert ? red : green;
    }

    createArcPathForGauge(x, y, radius, startAngle, endAngle) {
        const start = this.polarToCartesian(x, y, radius, endAngle);
        const end = this.polarToCartesian(x, y, radius, startAngle);
        const largeArcFlag = endAngle - startAngle <= 180 ? '0' : '1';
        return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArcFlag} 0 ${end.x} ${end.y}`;
    }

    polarToCartesian(centerX, centerY, radius, angleInDegrees) {
        const rad = (angleInDegrees - 90) * Math.PI / 180;
        return {
            x: centerX + radius * Math.cos(rad),
            y: centerY + radius * Math.sin(rad)
        };
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

if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNAElegantGauge;
}

if (typeof window !== 'undefined') {
    window.DotDNAElegantGauge = DotDNAElegantGauge;
}
