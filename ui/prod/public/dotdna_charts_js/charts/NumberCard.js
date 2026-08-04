/**
 * NumberCard - KPI stat card with an animated counter.
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 *
 * Follows the DotDNAChart data contract: the value arrives via `config.data` at
 * construction and via setData({ value }) on updates. `value` may be a number
 * (count-up animated, locale-grouped) or a pre-formatted string shown verbatim
 * (e.g. "2.34 GB", "75.0%"). No data → an em dash. The label is optional and
 * usually omitted — the surrounding tile already carries the metric title.
 */

class DotDNANumberCard {
    constructor(containerId, options = {}) {
        this.container = document.getElementById(containerId);
        if (!this.container) {
            throw new Error(`Container with id "${containerId}" not found`);
        }

        this.options = {
            label: options.label || '',
            color: options.color || 'var(--accent)',
            suffix: options.suffix || '',
            animate: options.animate !== undefined ? options.animate : true,
            animationDuration: options.animationDuration || 1000,
        };

        this.value = null;          // null → "no data" dash
        this.displayValue = 0;      // last painted number (animation start point)
        this.animationTimer = null;

        this.render();

        // DotDNAChart passes the value under `data` in the constructor options.
        if (options.data !== undefined) {
            this.setData(options.data);
        }
    }

    // Static card chrome, built once. setData only rewrites the value text so
    // refetches don't churn the DOM.
    render() {
        this.container.className = 'dotdna-chart';

        const suffix = this.options.suffix
            ? `<span class="dotdna-number-card-suffix">${this.options.suffix}</span>`
            : '';
        const label = this.options.label
            ? `<div class="dotdna-number-card-label">${this.options.label}</div>`
            : '';

        this.container.innerHTML = `
            <div class="dotdna-number-card">
                <div class="dotdna-number-card-value" style="color: ${this.options.color};">
                    <span class="value-number">—</span>${suffix}
                </div>
                ${label}
            </div>
        `;

        this.valueElement = this.container.querySelector('.value-number');
    }

    // Accepts { value } (DotDNAChart shape) or a raw number/string. A DotDNAChart
    // mount with no data passes `[]` — treated as "no data".
    setData(data) {
        let value;
        if (Array.isArray(data)) {
            value = undefined;
        } else if (data && typeof data === 'object') {
            value = data.value;
        } else {
            value = data;
        }

        const invalid = value === undefined || value === null
            || (typeof value === 'number' && !isFinite(value));
        this.value = invalid ? null : value;
        this._updateValue();
    }

    get isNumeric() {
        return typeof this.value === 'number' && isFinite(this.value);
    }

    formatNumber(num) {
        return typeof num === 'number' ? num.toLocaleString() : String(num);
    }

    _updateValue() {
        if (!this.valueElement) return;
        if (this.animationTimer) {
            clearInterval(this.animationTimer);
            this.animationTimer = null;
        }
        if (this.value === null) {
            this.valueElement.textContent = '—';
            return;
        }
        if (this.isNumeric && this.options.animate) {
            this.animate();
        } else {
            // Pre-formatted string, or animation disabled → show verbatim.
            this.displayValue = this.isNumeric ? this.value : 0;
            this.valueElement.textContent = this.formatNumber(this.value);
        }
    }

    // Count from the previously shown number to the new value (not always from
    // zero) so periodic refetches read as a smooth tick, not a reset.
    animate() {
        const steps = 60;
        const stepDuration = this.options.animationDuration / steps;
        const target = this.value;
        const start = isFinite(this.displayValue) ? this.displayValue : 0;
        const delta = (target - start) / steps;
        const isInt = Number.isInteger(target);
        let currentStep = 0;

        const paint = (v) => {
            if (!this.valueElement) return;
            const shown = isInt ? Math.round(v) : Math.round(v * 100) / 100;
            this.valueElement.textContent = this.formatNumber(shown);
        };

        paint(start);
        this.animationTimer = setInterval(() => {
            currentStep++;
            if (currentStep >= steps) {
                this.displayValue = target;
                paint(target);
                clearInterval(this.animationTimer);
                this.animationTimer = null;
            } else {
                const v = start + delta * currentStep;
                this.displayValue = v;
                paint(v);
            }
        }, stepDuration);
    }

    update(newOptions) {
        // Back-compat with the {...config, data} update shape. Merge option
        // fields, rebuild chrome, then re-apply data through setData.
        if (newOptions && typeof newOptions === 'object' && 'data' in newOptions) {
            const { data, ...rest } = newOptions;
            Object.assign(this.options, rest);
            this.render();
            this.setData(data);
        } else {
            Object.assign(this.options, newOptions || {});
            this.render();
            this._updateValue();
        }
    }

    destroy() {
        if (this.animationTimer) {
            clearInterval(this.animationTimer);
        }
        this.container.innerHTML = '';
    }
}

// Export for module systems or global scope
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNANumberCard;
}

// Make available globally for browser usage
if (typeof window !== 'undefined') {
    window.DotDNANumberCard = DotDNANumberCard;
}
