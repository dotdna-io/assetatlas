/**
 * UltraNumber - Ultra-minimalist number display with breathing animation
 * Part of DotDNA Charts Library (Vanilla JS Edition)
 *
 * REFACTORED VERSION with proper separation:
 * - config: Static configuration (colors, animation settings)
 * - data: Dynamic data that can be updated without recreation
 * - setupDOM(): One-time DOM structure creation
 * - setData(): Update data dynamically without distortion
 * - resize(): Handle size changes
 */

class DotDNAUltraNumber extends DotDNASVGChartBase {
    constructor(containerId, config = {}) {
        super(containerId, config);

        // SEPARATE: Static configuration (doesn't change after init)
        const paletteColor = window.ChartPalette ? window.ChartPalette.gradients[2].colors[0] : '#3B82F6';
        this.config = {
            animationDuration: config.animationDuration || 2000,
            color: config.color || paletteColor,
            gridSize: config.gridSize || '2x2'  // '2x2' or '3x2'
        };

        // SEPARATE: Dynamic data (can be updated)
        this.data = null;

        // State tracking
        this.animationKey = 0;
        this.isVisible = false;

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
     * Creates random data for initial display
     */
    generateRandomData() {
        return {
            value: Math.floor(Math.random() * 10000) + 1000,
            showPercent: false,
            percentValue: Math.floor(Math.random() * 100),
            label: 'Sample'
        };
    }

    /**
     * ONE-TIME DOM SETUP
     * Creates the wrapper and structure
     * This only runs once or when resizing
     */
    setupDOM() {
        // Clear container
        this.container.innerHTML = '';
        this.container.className = 'dotdna-chart';
        this.container.style.width = '100%';
        this.container.style.height = '100%';
        this.container.style.display = 'flex';
        this.container.style.alignItems = 'center';
        this.container.style.justifyContent = 'center';
        this.container.style.position = 'relative';
        this.container.style.overflow = 'hidden';

        // Create wrapper
        this.wrapper = document.createElement('div');
        this.wrapper.style.width = '100%';
        this.wrapper.style.height = '100%';
        this.wrapper.style.display = 'flex';
        this.wrapper.style.flexDirection = 'column';
        this.wrapper.style.alignItems = 'center';
        this.wrapper.style.justifyContent = 'center';
        this.wrapper.style.padding = '20px';
        this.wrapper.style.boxSizing = 'border-box';
        this.wrapper.style.background = 'transparent';

        // Main number container - centered with slight downward offset
        this.numberContainer = document.createElement('div');
        this.numberContainer.style.position = 'relative';
        this.numberContainer.style.display = 'flex';
        this.numberContainer.style.alignItems = 'center';
        this.numberContainer.style.justifyContent = 'center';
        this.numberContainer.style.marginTop = '8px';

        // The number itself
        this.numberDiv = document.createElement('div');
        this.numberDiv.style.fontSize = '84px';
        this.numberDiv.style.fontWeight = '300';
        this.numberDiv.style.color = this.config.color;
        this.numberDiv.style.letterSpacing = '-0.02em';
        this.numberDiv.style.lineHeight = '1';
        this.numberDiv.style.fontVariantNumeric = 'tabular-nums';
        this.numberDiv.style.opacity = '0';
        this.numberDiv.style.transform = 'scale(0.8)';
        this.numberDiv.style.textAlign = 'center';

        // Percent sign span (optional, added inline with number)
        this.percentSign = document.createElement('span');
        this.percentSign.style.fontSize = '48px';
        this.percentSign.style.fontWeight = '300';
        this.percentSign.style.color = this.config.color;
        this.percentSign.style.opacity = '0.7';
        this.percentSign.style.marginLeft = '8px';
        this.percentSign.textContent = '%';

        // Assemble DOM
        this.numberContainer.appendChild(this.numberDiv);
        this.wrapper.appendChild(this.numberContainer);
        this.container.appendChild(this.wrapper);

        // Hover effects
        this.numberContainer.addEventListener('mouseenter', () => {
            this.numberDiv.style.transform = 'scale(1.05)';
        });

        this.numberContainer.addEventListener('mouseleave', () => {
            this.numberDiv.style.transform = 'scale(1)';
        });
    }

    /**
     * SET DATA - Update chart data dynamically
     * @param {Object} data - Chart data: {value, showPercent, percentValue, label}
     */
    setData(data) {
        if (!data || typeof data !== 'object') {
            console.error('UltraNumber: setData() requires an object');
            return;
        }

        const item = Array.isArray(data) ? data[0] : data;
        if (!item) return;

        this.data = {
            value: item.value !== undefined ? item.value : 0,
            showPercent: item.showPercent || false,
            percentValue: item.percentValue,
            label: item.label || ''
        };

        // Update percent sign visibility
        this.updatePercentSign();

        // Animate number entrance with scale
        setTimeout(() => {
            this.numberDiv.style.transition = 'all 1.2s cubic-bezier(0.34, 1.56, 0.64, 1)';
            this.numberDiv.style.opacity = '1';
            this.numberDiv.style.transform = 'scale(1)';
        }, 100);

        // Start counter animation
        setTimeout(() => {
            this.animateNumber();
        }, 400);

        this.isVisible = true;
    }

    /**
     * UPDATE PERCENT SIGN
     * Shows or hides the percent sign based on data
     */
    updatePercentSign() {
        // Remove any existing percent sign
        if (this.numberDiv.contains(this.percentSign)) {
            this.numberDiv.removeChild(this.percentSign);
        }

        // Add percent sign if needed
        if (this.data.showPercent) {
            this.numberDiv.appendChild(this.percentSign);
        }
    }

    /**
     * FORMAT NUMBER
     * Formats large numbers with K, M, B notation
     */
    formatNumber(num) {
        if (Math.abs(num) >= 1000000000) {
            return (num / 1000000000).toFixed(1).replace(/\.0$/, '') + 'B';
        }
        if (Math.abs(num) >= 1000000) {
            return (num / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
        }
        if (Math.abs(num) >= 1000) {
            return (num / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
        }
        return num.toString();
    }

    /**
     * ANIMATE NUMBER
     * Animates the main number counter with smooth counting from 0
     */
    animateNumber() {
        const targetValue = this.data.value;
        const formattedTarget = this.formatNumber(targetValue);
        const duration = this.config.animationDuration;
        const startTime = Date.now();
        const isFormatted = formattedTarget.includes('K') || formattedTarget.includes('M') || formattedTarget.includes('B');

        // Easing function: ease-out expo for smooth deceleration
        const easeOutExpo = (t) => {
            return t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
        };

        const animate = () => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = easeOutExpo(progress);

            if (isFormatted) {
                // For formatted numbers (K, M, B), animate to the numeric part smoothly
                const match = formattedTarget.match(/^([\d.]+)([KMB])$/);
                if (match) {
                    const numericPart = parseFloat(match[1]);
                    const suffix = match[2];
                    const currentValue = (numericPart * eased).toFixed(1);
                    this.numberDiv.childNodes[0].textContent = currentValue + suffix;
                } else {
                    this.numberDiv.childNodes[0].textContent = formattedTarget;
                }
            } else {
                // For regular numbers, animate the count smoothly
                const currentValue = Math.floor(targetValue * eased);
                this.numberDiv.childNodes[0].textContent = currentValue.toLocaleString();
            }

            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                // Ensure final value is exact
                this.numberDiv.childNodes[0].textContent = isFormatted ? formattedTarget : targetValue.toLocaleString();
            }
        };

        // Initialize text node if needed
        if (this.numberDiv.childNodes.length === 0 || this.numberDiv.childNodes[0].nodeType !== Node.TEXT_NODE) {
            this.numberDiv.insertBefore(document.createTextNode('0'), this.numberDiv.firstChild);
        }

        animate();
    }

    /**
     * ANIMATE PERCENTAGE
     * Animates the percentage counter below the main number
     */
    animatePercentage() {
        const targetPercent = this.data.percentValue;
        const startTime = Date.now();
        const duration = this.config.animationDuration;

        const animate = () => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3);

            const currentPercent = Math.floor(targetPercent * eased);
            this.percentText.textContent = currentPercent + '%';

            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                this.percentText.textContent = targetPercent + '%';
            }
        };

        setTimeout(() => {
            animate();
        }, 200);
    }

    /**
     * RESIZE - Handle size changes (2x2 ↔ 3x2)
     * @param {String} gridSize - '2x2' or '3x2'
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
     * UPDATE VALUE - Legacy method for backward compatibility
     * @deprecated Use setData() instead
     */
    updateValue(newValue) {
        console.warn('UltraNumber: updateValue() is deprecated. Use setData() instead.');
        this.setData({ ...this.data, value: newValue });
    }

    /**
     * REFRESH - Legacy method
     * @deprecated Use setData() instead
     */
    refresh() {
        console.warn('UltraNumber: refresh() is deprecated. Use setData() instead.');
        this.animationKey++;
        this.isVisible = false;
        if (this.data) {
            this.setData(this.data);
        }
    }

    /**
     * DESTROY
     */
    destroy() {
        this.container.innerHTML = '';
    }
}

// Export to window for wizard access
if (typeof window !== 'undefined') {
    window.DotDNAUltraNumber = DotDNAUltraNumber;
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = DotDNAUltraNumber;
}
