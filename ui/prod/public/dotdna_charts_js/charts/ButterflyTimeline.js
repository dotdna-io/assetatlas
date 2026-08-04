/**
 * ButterflyTimeline - Split-scale butterfly bar chart for DotDNA Charts Library
 *
 * Wraps AssetTimelineChart as a DotDNA-compatible chart class.
 * Top half = positive metric (e.g. assets added), bottom half = negative metric (e.g. removed).
 * Each half scales independently for visibility.
 *
 * Data format (from API):
 *   [{ label: 'Jan', added: 1200, removed: 3 }, ...]
 *
 * Options:
 *   data           - Array of { label, added, removed }
 *   addedColor     - [topColor, bottomColor] gradient pair (default green)
 *   removedColor   - [topColor, bottomColor] gradient pair (default red)
 *   addedLabel     - Legend label for top bars (default 'Assets Added')
 *   removedLabel   - Legend label for bottom bars (default 'Assets Removed')
 *   barRadius      - Corner radius in px (default 4)
 *   waveDelay      - ms between column animations (default 80)
 *
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 */

class DotDNAButterflyTimeline {
    constructor(containerId, options = {}) {
        this.containerId = containerId;
        this.container = document.getElementById(containerId);
        if (!this.container) throw new Error(`Container "${containerId}" not found`);

        this.options = options;
        this._inner = null;

        // If data is provided, render immediately
        if (options.data && options.data.length > 0) {
            this._render();
        } else {
            this._showEmpty();
        }
    }

    _render() {
        // Delegate to AssetTimelineChart
        if (typeof AssetTimelineChart === 'undefined') {
            console.error('[DotDNAButterflyTimeline] AssetTimelineChart not loaded');
            this._showEmpty('Chart library not available');
            return;
        }

        this.destroy();
        this._inner = new AssetTimelineChart(this.containerId, this.options);
    }

    _showEmpty(msg) {
        this.container.innerHTML = `
            <div style="display:flex;align-items:center;justify-content:center;height:100%;
                        color:var(--text-tertiary);font-size:13px;">
                ${msg || 'No timeline data available'}
            </div>
        `;
    }

    /**
     * Update chart with new data
     */
    update(newOptions) {
        Object.assign(this.options, newOptions);
        if (this.options.data && this.options.data.length > 0) {
            this._render();
        } else {
            this._showEmpty();
        }
    }

    /**
     * Generate demo data for wizard preview
     */
    static generateDemoData() {
        const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        return months.map(m => ({
            label: m,
            added: Math.floor(Math.random() * 500) + 50,
            removed: Math.floor(Math.random() * 30) + 1
        }));
    }

    /**
     * Cleanup
     */
    destroy() {
        if (this._inner && typeof this._inner.destroy === 'function') {
            this._inner.destroy();
        }
        this._inner = null;
    }
}
