/**
 * MetricBar - Metric display with mini vertical bar chart and key stats
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 *
 * Layout: Big number (top-right) over full-width mini bars + bottom stats
 * Sizes: 2x1, 1x1
 */

class DotDNAMetricBar extends DotDNASVGChartBase {
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
        const barColor = this.config.color;

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

        // Big number — top right
        const numberRow = document.createElement('div');
        numberRow.style.display = 'flex';
        numberRow.style.alignItems = 'baseline';
        numberRow.style.justifyContent = 'flex-end';
        numberRow.style.gap = '4px';
        numberRow.style.marginBottom = '4px';
        numberRow.style.flexShrink = '0';

        const bigNumber = document.createElement('div');
        bigNumber.className = 'mb-value';
        const gridW = parseInt((this.config.gridSize || '2x1').split('x')[0]);
        bigNumber.style.fontSize = compact
            ? (gridW >= 2 ? '38px' : '32px')
            : (gridW >= 2 ? '54px' : '44px');
        bigNumber.style.fontWeight = '300';
        bigNumber.style.letterSpacing = '-0.02em';
        bigNumber.style.color = textColor;
        bigNumber.style.lineHeight = '1';
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

        // Mini bar chart — full width, takes remaining space
        const barsContainer = document.createElement('div');
        barsContainer.style.flex = '1';
        barsContainer.style.display = 'flex';
        barsContainer.style.flexDirection = 'row';
        barsContainer.style.alignItems = 'flex-end';
        barsContainer.style.gap = '3px';
        barsContainer.style.minHeight = '30px';
        barsContainer.style.width = '100%';
        barsContainer.style.position = 'relative';

        const trend = this.data.trend;
        if (trend && trend.length > 0) {
            const maxVal = Math.max(...trend.map(t => t.value));

            trend.forEach((item, i) => {
                const barWrapper = document.createElement('div');
                barWrapper.style.flex = '1';
                barWrapper.style.height = '100%';
                barWrapper.style.display = 'flex';
                barWrapper.style.alignItems = 'flex-end';

                const bar = document.createElement('div');
                const heightPct = (item.value / maxVal) * 100;
                bar.style.width = '100%';
                bar.style.height = '0%';
                bar.style.borderRadius = '2px 2px 0 0';
                bar.style.background = `linear-gradient(to top, ${barColor[1]}, ${barColor[0]})`;
                bar.style.opacity = '0.8';
                bar.style.transition = `height 600ms cubic-bezier(0.34, 1.56, 0.64, 1)`;

                // Highlight last bar
                if (i === trend.length - 1) {
                    bar.style.opacity = '1';
                }

                barWrapper.appendChild(bar);
                barsContainer.appendChild(barWrapper);

                // Stagger animation
                setTimeout(() => {
                    bar.style.height = heightPct + '%';
                }, i * 80);
            });

            // Start-of-trend value — sits inside the chart above the first
            // bar (mirrors MetricCard). The ending value is omitted from the
            // stats row below: it's already the big headline number.
            const startStat = this.data.stats && this.data.stats[0];
            if (startStat && startStat.value !== undefined && trend.length >= 2) {
                const topRatio = 1 - (trend[0].value / maxVal);
                const startValue = document.createElement('div');
                startValue.className = 'mb-start-value';
                startValue.style.position = 'absolute';
                startValue.style.left = '0';
                startValue.style.fontSize = '14px';
                startValue.style.fontWeight = '600';
                startValue.style.color = textColor;
                startValue.style.lineHeight = '1';
                startValue.style.pointerEvents = 'none';
                // Above the first bar's top; below it when the bar is nearly
                // full height, so the label never collides with the big number.
                if (topRatio < 0.3) {
                    startValue.style.top = `calc(${topRatio * 100}% + 6px)`;
                } else {
                    startValue.style.top = `calc(${topRatio * 100}% - 6px)`;
                    startValue.style.transform = 'translateY(-100%)';
                }
                startValue.style.opacity = '0';
                startValue.style.transition = 'opacity 0.5s ease-out';
                startValue.textContent = startStat.value;
                barsContainer.appendChild(startValue);
                setTimeout(() => {
                    startValue.style.opacity = '1';
                }, 800);
            }
        }

        wrapper.appendChild(barsContainer);

        // Bottom stats row — date labels only ("2026-06-22" left, "2026-07-20"
        // right). Values are shown elsewhere: the start value sits inside the
        // chart above the first bar, the end value IS the headline number.
        if (this.data.stats && this.data.stats.length > 0) {
            const statsRow = document.createElement('div');
            statsRow.className = 'mb-stats';
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

if (typeof window !== 'undefined') {
    window.DotDNAMetricBar = DotDNAMetricBar;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNAMetricBar;
}
