/**
 * DotDNA Charts Library - Vanilla JavaScript Edition
 *
 * A collection of modern, animated SVG charts for data visualization
 * No dependencies, no frameworks - pure vanilla JavaScript
 *
 * Usage:
 *   1. Include dotdna.css in your HTML
 *   2. Include dotdna.js and chart files
 *   3. Create chart instances:
 *      const chart = new DotDNANumberCard('container-id', { value: 1234, label: 'Revenue' });
 *
 * Available Charts:
 *   - DotDNANumberCard: Single KPI metric with trend indicator
 *   - DotDNAAnimatedDonutModern: Modern donut chart with severity levels
 *   - DotDNAAnimatedCircleChart: Circular progress with animated segments
 *   - DotDNASegmentedHorizontalBar: Horizontal bar with segmented progress
 *   - DotDNASegmentedVerticalBar: Vertical bar with segmented stacks
 *
 * The list above tracks charts/ — every file there is loaded by ui/prod/index.html.
 */

// Library version
const DOTDNA_VERSION = '1.0.0';

// Global registry for chart instances
const DotDNA = {
    version: DOTDNA_VERSION,
    charts: new Map(),

    /**
     * Register a chart instance
     */
    register(containerId, chart) {
        this.charts.set(containerId, chart);
    },

    /**
     * Get a chart instance by container ID
     */
    getChart(containerId) {
        return this.charts.get(containerId);
    },

    /**
     * Destroy a chart instance
     */
    destroyChart(containerId) {
        const chart = this.charts.get(containerId);
        if (chart && typeof chart.destroy === 'function') {
            chart.destroy();
        }
        this.charts.delete(containerId);
    },

    /**
     * Destroy all chart instances
     */
    destroyAll() {
        this.charts.forEach(chart => {
            if (typeof chart.destroy === 'function') {
                chart.destroy();
            }
        });
        this.charts.clear();
    },

    /**
     * Default color palettes
     */
    colors: {
        severity: {
            critical: ['#EF4444', '#DC2626'],
            high: ['#F59E0B', '#D97706'],
            medium: ['#3B82F6', '#2563EB'],
            low: ['#10B981', '#059669'],
            info: ['#8B5CF6', '#7C3AED']
        },
        primary: ['#3B82F6', '#10B981', '#F59E0B', '#EC4899', '#8B5CF6'],
        sequential: ['#475569', '#5b8aa0', '#7db4b5', '#9eccc6', '#c3e0db'],
        diverging: ['#DC2626', '#F59E0B', '#FCD34D', '#86EFAC', '#059669']
    },

    /**
     * Utility functions
     */
    utils: {
        /**
         * Format number with commas
         */
        formatNumber(num) {
            return num.toLocaleString();
        },

        /**
         * Generate random data for testing
         */
        generateRandomData(count = 5, maxValue = 100) {
            const data = [];
            let remaining = maxValue;

            for (let i = 0; i < count - 1; i++) {
                const value = Math.floor(Math.random() * (remaining / 2)) + 1;
                data.push(value);
                remaining -= value;
            }
            data.push(remaining);

            return data;
        },

        /**
         * Convert hex color to RGBA
         */
        hexToRgba(hex, alpha = 1) {
            const r = parseInt(hex.slice(1, 3), 16);
            const g = parseInt(hex.slice(3, 5), 16);
            const b = parseInt(hex.slice(5, 7), 16);
            return `rgba(${r}, ${g}, ${b}, ${alpha})`;
        }
    }
};

// Make DotDNA available globally
if (typeof window !== 'undefined') {
    window.DotDNA = DotDNA;
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNA;
}
