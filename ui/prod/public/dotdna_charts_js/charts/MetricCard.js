/**
 * MetricCard - Metric display with sparkline trend and key stats
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 *
 * Layout: Big number (top-right) over full-width sparkline + bottom stats
 * Sizes: 2x2, 3x1
 */

class DotDNAMetricCard extends DotDNASVGChartBase {
    constructor(containerId, config = {}) {
        super(containerId, config);

        this.config = {
            animationDuration: config.animationDuration || 2000,
            gridSize: config.gridSize || '2x1',
            color: config.color || (window.ChartPalette ? window.ChartPalette.colors(2) : ['#3B82F6', '#2563EB']),
            ...config
        };

        this.data = null;

        this.setupDOM();

        // Only render if initial data provided; otherwise wait for setData()
        if (config.data) {
            this.setData(config.data);
        } else {
            this.showNoData();
        }
    }

    generateRandomData() {
        const months = ['Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan'];
        const baseValue = 8000 + Math.floor(Math.random() * 4000);
        const trend = months.map((label, i) => {
            const variation = Math.sin(i * 0.8) * 1500 + Math.random() * 800;
            return {
                label,
                value: Math.round(baseValue + variation + i * 200)
            };
        });

        const currentVal = trend[trend.length - 1].value;
        const startVal = trend[0].value;

        return {
            trend,
            currentValue: currentVal,
            unit: '',
            stats: [
                { label: trend[0].label, value: this.formatNum(startVal) },
                { label: trend[trend.length - 1].label, value: this.formatNum(currentVal), highlight: true }
            ]
        };
    }

    formatNum(num) {
        if (Math.abs(num) >= 1000000) {
            return (num / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
        }
        if (Math.abs(num) >= 1000) {
            return (num / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
        }
        return num.toLocaleString();
    }

    setupDOM() {
        this.container.innerHTML = '';
        this.container.className = 'dotdna-chart';
        this.container.style.width = '100%';
        this.container.style.height = '100%';
        this.container.style.display = 'flex';
        this.container.style.alignItems = 'stretch';
        this.container.style.justifyContent = 'center';
        this.container.style.position = 'relative';
        this.container.style.overflow = 'hidden';
    }

    setData(data) {
        if (!data || typeof data !== 'object') return;
        const item = Array.isArray(data) ? data[0] : data;
        if (!item) return;
        // Normalize: pipeline sends 'value', render expects 'currentValue'
        if (item.value !== undefined && item.currentValue === undefined) {
            item.currentValue = item.value;
        }
        this.data = item;
        this.render();
    }

    render() {
        this.container.innerHTML = '';

        const isDark = document.body.classList.contains('dark-mode');
        const textColor = isDark ? '#FFFFFF' : '#111827';
        const subtextColor = isDark ? '#9CA3AF' : '#6B7280';
        const lineColor = this.config.color[0];

        // 1-row grid cards give the body ~135px; the default 54px number and
        // 26px top padding overflow that and clip the stats row (the card
        // body hides overflow) — switch to compact spacing when short.
        const compact = this.container.clientHeight > 0 && this.container.clientHeight < 160;

        const wrapper = document.createElement('div');
        wrapper.style.width = '100%';
        wrapper.style.height = '100%';
        wrapper.style.display = 'flex';
        wrapper.style.flexDirection = 'column';
        wrapper.style.padding = compact ? '10px 28px 8px 20px' : '26px 28px 10px 20px';
        wrapper.style.boxSizing = 'border-box';
        wrapper.style.position = 'relative';

        // Big number — top right, above the sparkline
        const numberRow = document.createElement('div');
        numberRow.style.display = 'flex';
        numberRow.style.alignItems = 'baseline';
        numberRow.style.justifyContent = 'flex-end';
        numberRow.style.gap = '4px';
        numberRow.style.marginBottom = '4px';
        numberRow.style.flexShrink = '0';

        const bigNumber = document.createElement('div');
        bigNumber.className = 'mc-value';
        const gridW = parseInt((this.config.gridSize || '2x1').split('x')[0]);
        bigNumber.style.fontSize = compact
            ? (gridW >= 2 ? '38px' : '30px')
            : (gridW >= 2 ? '54px' : '38px');
        bigNumber.style.fontWeight = '300';
        bigNumber.style.letterSpacing = '-0.02em';
        bigNumber.style.color = textColor;
        bigNumber.style.lineHeight = '1';
        if (gridW < 2) bigNumber.style.marginTop = '6px';
        bigNumber.style.opacity = '0';
        bigNumber.style.transform = 'translateY(8px)';
        bigNumber.style.transition = 'opacity 0.8s ease-out, transform 0.8s ease-out';
        bigNumber.textContent = '0';

        numberRow.appendChild(bigNumber);

        if (this.data.unit) {
            const unitLabel = document.createElement('div');
            unitLabel.style.fontSize = '18px';
            unitLabel.style.fontWeight = '400';
            unitLabel.style.color = subtextColor;
            unitLabel.style.lineHeight = '1';
            unitLabel.textContent = this.data.unit;
            numberRow.appendChild(unitLabel);
        }

        wrapper.appendChild(numberRow);

        // Sparkline — full width, takes remaining space
        const sparkContainer = document.createElement('div');
        sparkContainer.style.flex = '1';
        sparkContainer.style.position = 'relative';
        sparkContainer.style.minHeight = '30px';
        sparkContainer.style.width = '100%';

        const sparkSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        sparkSvg.style.width = '100%';
        sparkSvg.style.height = '100%';
        sparkSvg.style.display = 'block';
        sparkSvg.style.overflow = 'visible';

        this.renderSparkline(sparkSvg, lineColor, isDark);
        sparkContainer.appendChild(sparkSvg);

        // Start-of-trend value — sits inside the chart at the line's origin.
        // An HTML overlay rather than SVG text: the sparkline SVG stretches
        // non-uniformly (preserveAspectRatio "none"), which would distort
        // glyphs. renderSparkline stores the first point's height ratio.
        const startStat = this.data.stats && this.data.stats[0];
        if (startStat && startStat.value !== undefined && this._startPointRatio !== undefined) {
            const startValue = document.createElement('div');
            startValue.className = 'mc-start-value';
            startValue.style.position = 'absolute';
            startValue.style.left = '0';
            startValue.style.fontSize = '14px';
            startValue.style.fontWeight = '600';
            startValue.style.color = textColor;
            startValue.style.lineHeight = '1';
            startValue.style.pointerEvents = 'none';
            // Above the line's first point; below it when the line starts
            // high, so the label never collides with the big number.
            if (this._startPointRatio < 0.3) {
                startValue.style.top = `calc(${this._startPointRatio * 100}% + 6px)`;
            } else {
                startValue.style.top = `calc(${this._startPointRatio * 100}% - 6px)`;
                startValue.style.transform = 'translateY(-100%)';
            }
            startValue.style.opacity = '0';
            startValue.style.transition = 'opacity 0.5s ease-out';
            startValue.textContent = startStat.value;
            sparkContainer.appendChild(startValue);
            setTimeout(() => {
                startValue.style.opacity = '1';
            }, 800);
        }

        wrapper.appendChild(sparkContainer);

        // Bottom stats row — date labels only ("2026-06-22" left, "2026-07-20"
        // right). Values are shown elsewhere: the start value sits inside the
        // chart at the line's origin, the end value IS the headline number.
        if (this.data.stats && this.data.stats.length > 0) {
            const statsRow = document.createElement('div');
            statsRow.className = 'mc-stats';
            statsRow.style.display = 'flex';
            statsRow.style.justifyContent = 'space-between';
            statsRow.style.marginTop = compact ? '4px' : '8px';
            statsRow.style.paddingTop = compact ? '6px' : '8px';
            statsRow.style.borderTop = `1px solid ${isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.06)'}`;
            statsRow.style.flexShrink = '0';

            this.data.stats.forEach((stat, i) => {
                const statEl = document.createElement('div');
                statEl.style.display = 'flex';
                statEl.style.alignItems = 'baseline';
                statEl.style.gap = '6px';
                statEl.style.opacity = '0';
                statEl.style.transform = 'translateY(6px)';
                statEl.style.transition = 'opacity 0.5s ease-out, transform 0.5s ease-out';

                const statLabel = document.createElement('span');
                statLabel.style.fontSize = '11px';
                statLabel.style.color = subtextColor;
                statLabel.textContent = stat.label;

                statEl.appendChild(statLabel);
                statsRow.appendChild(statEl);

                setTimeout(() => {
                    statEl.style.opacity = '1';
                    statEl.style.transform = 'translateY(0)';
                }, 800 + i * 150);
            });

            wrapper.appendChild(statsRow);
        }

        this.container.appendChild(wrapper);

        // Animate big number
        setTimeout(() => {
            bigNumber.style.opacity = '1';
            bigNumber.style.transform = 'translateY(0)';
        }, 200);

        this.animateCounter(bigNumber, this.data.currentValue);
    }

    renderSparkline(svg, lineColor, isDark) {
        // Height ratio (0..1) of the line's first point — consumed by render()
        // to anchor the in-chart start-value label.
        this._startPointRatio = undefined;
        const trend = this.data.trend;
        if (!trend || trend.length < 2) return;

        const vw = 400;
        const vh = 100;
        const padX = 0;
        const padY = 8;
        svg.setAttribute('viewBox', `0 0 ${vw} ${vh}`);
        svg.setAttribute('preserveAspectRatio', 'none');

        const values = trend.map(t => t.value);
        const minVal = Math.min(...values);
        const maxVal = Math.max(...values);
        const range = maxVal - minVal || 1;

        const points = trend.map((t, i) => {
            const x = padX + (i / (trend.length - 1)) * (vw - padX * 2);
            const y = padY + (1 - (t.value - minVal) / range) * (vh - padY * 2);
            return { x, y };
        });

        this._startPointRatio = points[0].y / vh;

        // Area fill gradient
        const uid = `mc-${this.container.id || Date.now()}`;
        const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');

        const areaGrad = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
        areaGrad.setAttribute('id', `${uid}-area`);
        areaGrad.setAttribute('x1', '0%');
        areaGrad.setAttribute('y1', '0%');
        areaGrad.setAttribute('x2', '0%');
        areaGrad.setAttribute('y2', '100%');

        const as1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        as1.setAttribute('offset', '0%');
        as1.setAttribute('stop-color', lineColor);
        as1.setAttribute('stop-opacity', '0.15');
        const as2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
        as2.setAttribute('offset', '100%');
        as2.setAttribute('stop-color', lineColor);
        as2.setAttribute('stop-opacity', '0.02');
        areaGrad.appendChild(as1);
        areaGrad.appendChild(as2);
        defs.appendChild(areaGrad);
        svg.appendChild(defs);

        // Area path — extends to full bottom
        let areaD = `M ${points[0].x},${vh}`;
        points.forEach(p => { areaD += ` L ${p.x},${p.y}`; });
        areaD += ` L ${points[points.length - 1].x},${vh} Z`;

        const areaPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        areaPath.setAttribute('d', areaD);
        areaPath.setAttribute('fill', `url(#${uid}-area)`);
        areaPath.style.opacity = '0';
        svg.appendChild(areaPath);

        setTimeout(() => {
            areaPath.style.transition = 'opacity 1s ease-out';
            areaPath.style.opacity = '1';
        }, 600);

        // Line path — edge to edge
        let lineD = `M ${points[0].x},${points[0].y}`;
        points.slice(1).forEach(p => { lineD += ` L ${p.x},${p.y}`; });

        const linePath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        linePath.setAttribute('d', lineD);
        linePath.setAttribute('stroke', lineColor);
        linePath.setAttribute('stroke-width', '2');
        linePath.setAttribute('fill', 'none');
        linePath.setAttribute('stroke-linecap', 'round');
        linePath.setAttribute('stroke-linejoin', 'round');
        linePath.setAttribute('vector-effect', 'non-scaling-stroke');

        const pathLength = linePath.getTotalLength();
        linePath.style.strokeDasharray = pathLength;
        linePath.style.strokeDashoffset = pathLength;
        svg.appendChild(linePath);

        setTimeout(() => {
            linePath.style.transition = `stroke-dashoffset ${this.config.animationDuration}ms cubic-bezier(0.4, 0, 0.2, 1)`;
            linePath.style.strokeDashoffset = '0';
        }, 100);
    }

    animateCounter(element, targetValue) {
        const formatted = this.formatNum(targetValue);
        const duration = this.config.animationDuration;
        const startTime = Date.now();
        const isFormatted = /[KMB]$/.test(formatted);

        const easeOut = (t) => 1 - Math.pow(1 - t, 3);

        const animate = () => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = easeOut(progress);

            if (isFormatted) {
                const match = formatted.match(/^([\d.]+)([KMB])$/);
                if (match) {
                    const num = parseFloat(match[1]);
                    const suffix = match[2];
                    element.textContent = (num * eased).toFixed(1) + suffix;
                }
            } else {
                element.textContent = Math.floor(targetValue * eased).toLocaleString();
            }

            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                element.textContent = formatted;
            }
        };

        setTimeout(animate, 400);
    }

    resize(gridSize) {
        this.config.gridSize = gridSize;
        this.setupDOM();
        if (this.data) this.setData(this.data);
    }

    refresh() {
        this.setupDOM();
        if (this.data) this.setData(this.data);
    }

    destroy() {
        this.container.innerHTML = '';
    }
}

// Export to window for wizard access
if (typeof window !== 'undefined') {
    window.DotDNAMetricCard = DotDNAMetricCard;
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNAMetricCard;
}
