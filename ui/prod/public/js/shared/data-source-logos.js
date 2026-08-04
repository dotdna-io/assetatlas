/**
 * AssetAtlas - Data Source Logo Mappings
 *
 * Centralized mapping of data source names to logo filenames.
 * Used across multiple pages: alerts, asset-history, homepage, etc.
 *
 * Logos are stored in /logos/ directory.
 *
 * Usage:
 *   import { getDataSourceLogo, DATA_SOURCE_LOGOS } from './shared/data-source-logos.js';
 *
 *   const logo = getDataSourceLogo('CrowdStrike');
 *   // Returns: { type: 'image', src: '/logos/crowdstrike.png' }
 *
 *   const logo = getDataSourceLogo('Unknown Source');
 *   // Returns: { type: 'svg', svg: '<svg>...</svg>' }
 */

// =============================================================================
// Logo Mappings with Brand Colors
// =============================================================================

const DATA_SOURCE_LOGOS = {
    // EDR / Endpoint Security
    'crowdstrike': { logo: 'crowdstrike.png', color: '#E11029' },
    'crowdstrike falcon': { logo: 'crowdstrike.png', color: '#E11029' },
    'defender': { logo: 'MS-defender-endpoint.png', color: '#0078D4' },
    'microsoft defender': { logo: 'MS-defender-endpoint.png', color: '#0078D4' },
    'microsoft defender api': { logo: 'MS-defender-endpoint.png', color: '#0078D4' },
    'microsoft defender telemetry': { logo: 'MS-defender-endpoint.png', color: '#0078D4' },
    'defender for endpoint': { logo: 'MS-defender-endpoint.png', color: '#0078D4' },
    'microsoft defender for endpoint': { logo: 'MS-defender-endpoint.png', color: '#0078D4' },
    'sentinel one': { logo: 'sentinel one.webp', color: '#6C2EB9' },
    'sentinelone': { logo: 'sentinel one.webp', color: '#6C2EB9' },
    'sentinelone ranger': { logo: 'sentinel one.webp', color: '#6C2EB9' },
    'cortex': { logo: 'cortex.png', color: '#F04E23' },
    'cylance': { logo: 'cylance.png', color: '#000000' },
    'cybereason': { logo: 'cybereason.png', color: '#6B4FBB' },
    'malwarebytes': { logo: 'malwarebytes.png', color: '#0052CC' },
    'sophos': { logo: 'sophos.png', color: '#2A5CAA' },
    'mcafee': { logo: 'mcafee.webp', color: '#C8102E' },
    'symantec': { logo: 'symantec.png', color: '#FDB913' },
    'kaspersky': { logo: 'kaspersky.png', color: '#006D5C' },
    'eset': { logo: 'eset.png', color: '#00A4E4' },
    'trend micro': { logo: 'trend_micro.png', color: '#D71920' },
    'trellix': { logo: 'trellix.png', color: '#FF6B00' },
    'carbon black': { logo: 'vmware.png', color: '#717074' },

    // SIEM / Log Management
    'splunk': { logo: 'splunk.png', color: '#65A637' },
    'microsoft sentinel': { logo: 'microsoft sentinel.png', color: '#0078D4' },
    'sentinel': { logo: 'microsoft sentinel.png', color: '#0078D4' },
    'log analytics': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
    'log analytics workspace': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
    'log analytics workspaces': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
    'azure log analytics': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
    'microsoft log analytics': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
    'microsoft log analytics agent': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
    'log analytics agent': { logo: 'MS-Log-Analytics-Workspaces.svg', color: '#0078D4' },
    'sumo logic': { logo: 'sumo_logic.png', color: '#000099' },
    'datadog': { logo: 'datadog.png', color: '#632CA6' },
    'dynatrace': { logo: 'dynatrace.png', color: '#1496FF' },
    'new relic': { logo: 'new_relic.png', color: '#008C99' },

    // Endpoint Management
    'intune': { logo: 'intune.png', color: '#0078D4' },
    'intune full': { logo: 'intune.png', color: '#0078D4' },
    'microsoft intune': { logo: 'intune.png', color: '#0078D4' },
    'sccm': { logo: 'sccm.png', color: '#0078D4' },
    'lansweeper': { logo: 'lansweeper.png', color: '#F37021' },
    'tanium': { logo: 'tanium.png', color: '#C8102E' },
    'ivanti': { logo: 'ivanti.png', color: '#E31837' },
    'snow software': { logo: 'snow software.png', color: '#00A1E0' },
    'manage engine': { logo: 'manage_engine.png', color: '#D32F2F' },
    'manageengine': { logo: 'manage_engine.png', color: '#D32F2F' },
    'airwatch': { logo: 'airwatch.png', color: '#F37021' },
    'jamf': { logo: 'apple.png', color: '#000000' },

    // ITSM / CMDB
    'servicenow': { logo: 'servicenow.png', color: '#81B5A1' },
    'servicenow cmdb': { logo: 'servicenow.png', color: '#81B5A1' },

    // Vulnerability Management
    'qualys': { logo: 'qualys.png', color: '#ED1C24' },
    'tenable': { logo: 'tenable.png', color: '#0A1F44' },
    'nessus': { logo: 'tenable.png', color: '#0A1F44' },
    'nessus scanner': { logo: 'tenable.png', color: '#0A1F44' },
    'rapid7': { logo: 'rapid7.png', color: '#E85D04' },

    // Network Security
    'forescout': { logo: 'forescout.png', color: '#00A3E0' },
    'armis': { logo: 'armis.png', color: '#6C5CE7' },
    'zscaler': { logo: 'zscaler.svg', color: '#049FD9' },
    'palo alto': { logo: 'paloalto.png', color: '#F04E23' },
    'paloalto': { logo: 'paloalto.png', color: '#F04E23' },
    'fortinet': { logo: 'fortinet.png', color: '#DA291C' },
    'cisco': { logo: 'cisco.png', color: '#049FD9' },
    'checkpoint': { logo: 'checkpoint.png', color: '#E21836' },
    'juniper': { logo: 'juniper.png', color: '#00A0E3' },
    'juniper mist': { logo: 'juniper.png', color: '#00A0E3' },
    'aruba': { logo: 'aruba.png', color: '#F37021' },

    // Cloud Platforms
    'azure': { logo: 'azure.png', color: '#0078D4' },
    'azure resource graph': { logo: 'Azure-Resource-Graph.svg', color: '#0078D4' },
    'azure container instances': { logo: 'Azure-Container-Instances.svg', color: '#0078D4' },
    'azure data explorer': { logo: 'Azure-Data-Explorer.svg', color: '#0078D4' },
    'azure data lake': { logo: 'Azure-Data-Lake-Store.svg', color: '#0078D4' },
    'azure data lake store': { logo: 'Azure-Data-Lake-Store.svg', color: '#0078D4' },
    'azure event hubs': { logo: 'Azure-Event-Hubs.svg', color: '#0078D4' },
    'azure virtual machines': { logo: 'Azure-Virtual-Machines.svg', color: '#0078D4' },
    'azure vm': { logo: 'Azure-Virtual-Machines.svg', color: '#0078D4' },
    'aws': { logo: 'aws.png', color: '#FF9900' },
    'google cloud': { logo: 'google_cloud.svg', color: '#4285F4' },
    'gcp': { logo: 'google_cloud.svg', color: '#4285F4' },
    'databricks': { logo: 'Databricks.svg', color: '#FF3621' },

    // OT / ICS Security
    'claroty': { logo: 'claroty.png', color: '#00B4D8' },
    'nozomi': { logo: 'nozomi.webp', color: '#00A3A1' },
    'rockwell': { logo: 'rockwell automation.png', color: '#C8102E' },
    'rockwell automation': { logo: 'rockwell automation.png', color: '#C8102E' },
    'schneider': { logo: 'schneider.png', color: '#3DCD58' },

    // Identity / Directory
    'ldap': { logo: 'MS-active-directory.png', color: '#0078D4' },
    'ldap computers': { logo: 'MS-active-directory.png', color: '#0078D4' },
    'active directory': { logo: 'MS-active-directory.png', color: '#0078D4' },
    'active directory authentications': { logo: 'MS-active-directory.png', color: '#0078D4' },
    'ad': { logo: 'MS-active-directory.png', color: '#0078D4' },
    'microsoft active directory': { logo: 'MS-active-directory.png', color: '#0078D4' },
    'azure ad': { logo: 'microsoft-entra-id.png', color: '#0078D4' },
    'azure active directory': { logo: 'microsoft-entra-id.png', color: '#0078D4' },
    'entra': { logo: 'microsoft-entra-id.png', color: '#0078D4' },
    'entra id': { logo: 'microsoft-entra-id.png', color: '#0078D4' },
    'entra devices': { logo: 'microsoft-entra-id.png', color: '#0078D4' },
    'microsoft entra': { logo: 'microsoft-entra-id.png', color: '#0078D4' },
    'microsoft entra id': { logo: 'microsoft-entra-id.png', color: '#0078D4' },

    // Monitoring
    'zabbix': { logo: 'zabbix.png', color: '#D40000' },
    'prtg': { logo: 'prtg.png', color: '#00B4CC' },
    'solarwinds': { logo: 'solarwinds.png', color: '#F37021' },

    // Virtualization
    'vmware': { logo: 'vmware.png', color: '#717074' },
    'citrix': { logo: 'citrix.png', color: '#452170' },

    // Hardware Vendors (for asset context)
    'dell': { logo: 'dell.png', color: '#007DB8' },
    'hp': { logo: 'hp.png', color: '#0096D6' },
    'lenovo': { logo: 'lenovo.png', color: '#E2231A' },
    'apple': { logo: 'apple.png', color: '#000000' },
    'samsung': { logo: 'samsung.png', color: '#1428A0' },
    'huawei': { logo: 'huawei.png', color: '#E31837' },
    'ricoh': { logo: 'ricoh.png', color: '#C8102E' },
    'avaya': { logo: 'avaya.png', color: '#DA291C' },
    'ibm': { logo: 'ibm.png', color: '#0530AD' },
    'oracle': { logo: 'oracle.png', color: '#C74634' },
    'sap': { logo: 'sap.png', color: '#0FAAFF' },

    // Backup
    'commvault': { logo: 'commvault.png', color: '#F37021' },

    // Other
    'aternity': { logo: 'aternity.png', color: '#00A4E4' },
    'snowflake': { logo: 'snowflake.png', color: '#29B5E8' }
};

// Default color for unknown sources
const DEFAULT_BRAND_COLOR = '#64748b';

// =============================================================================
// Fallback Icon
// =============================================================================

const FALLBACK_ICON_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
    <path d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7"></path>
    <ellipse cx="12" cy="7" rx="8" ry="4"></ellipse>
    <path d="M4 12c0 2.21 3.582 4 8 4s8-1.79 8-4"></path>
</svg>`;

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Get logo information for a data source
 * @param {string} sourceName - The data source name (case-insensitive)
 * @returns {Object} Logo info: { type: 'image'|'svg', src?: string, svg?: string, color: string }
 */
function getDataSourceLogo(sourceName) {
    if (!sourceName) {
        return { type: 'svg', svg: FALLBACK_ICON_SVG, color: DEFAULT_BRAND_COLOR };
    }

    // First, check IntegrationLogos (logos from database, user-selected in wizard)
    // This takes precedence over hardcoded mappings
    if (typeof IntegrationLogos !== 'undefined') {
        const apiLogo = IntegrationLogos.getLogoForSourceSync(sourceName);
        // apiLogo is null if not in cache, or an object with type/src if found
        if (apiLogo && apiLogo.type === 'image' && apiLogo.src) {
            return {
                type: 'image',
                src: apiLogo.src,
                color: apiLogo.color || DEFAULT_BRAND_COLOR
            };
        }
        // If apiLogo is null, fall through to hardcoded mappings below
    }

    // Fallback to hardcoded mappings
    const normalizedName = sourceName.toLowerCase().trim();
    const config = DATA_SOURCE_LOGOS[normalizedName];

    if (config) {
        return {
            type: 'image',
            src: `/logos/${config.logo}`,
            filename: config.logo,
            color: config.color || DEFAULT_BRAND_COLOR
        };
    }

    return { type: 'svg', svg: FALLBACK_ICON_SVG, color: DEFAULT_BRAND_COLOR };
}

/**
 * Get the default (hardcoded) logo for a source name, bypassing IntegrationLogos (DB).
 * Used for integration groups which should always show the type default, not customized logos.
 * @param {string} sourceName - The data source or integration type name (case-insensitive)
 * @returns {Object} Logo info: { type: 'image'|'svg', src?: string, svg?: string, color: string }
 */
function getDefaultLogo(sourceName) {
    if (!sourceName) {
        return { type: 'svg', svg: FALLBACK_ICON_SVG, color: DEFAULT_BRAND_COLOR };
    }

    const normalizedName = sourceName.toLowerCase().trim().replace(/_/g, ' ');
    const config = DATA_SOURCE_LOGOS[normalizedName];

    if (config) {
        return {
            type: 'image',
            src: `/logos/${config.logo}`,
            filename: config.logo,
            color: config.color || DEFAULT_BRAND_COLOR
        };
    }

    return { type: 'svg', svg: FALLBACK_ICON_SVG, color: DEFAULT_BRAND_COLOR };
}

/**
 * Get brand color for a data source
 * @param {string} sourceName - The data source name (case-insensitive)
 * @returns {string} Hex color code
 */
function getDataSourceColor(sourceName) {
    if (!sourceName) return DEFAULT_BRAND_COLOR;

    const normalizedName = sourceName.toLowerCase().trim();
    const config = DATA_SOURCE_LOGOS[normalizedName];

    return config?.color || DEFAULT_BRAND_COLOR;
}

/**
 * Generate HTML for a data source logo
 * @param {string} sourceName - The data source name
 * @param {Object} options - Optional settings { className, size, alt }
 * @returns {string} HTML string for the logo
 */
function getDataSourceLogoHTML(sourceName, options = {}) {
    const { className = '', size = null, alt = sourceName } = options;
    const logo = getDataSourceLogo(sourceName);

    const sizeStyle = size ? `width: ${size}px; height: ${size}px;` : '';

    if (logo.type === 'image') {
        return `<img src="${logo.src}" alt="${alt || 'Logo'}" class="${className}" style="${sizeStyle} object-fit: contain;">`;
    }

    return `<span class="${className}" style="${sizeStyle}">${logo.svg}</span>`;
}

/**
 * Convert hex color to rgba with opacity
 * @param {string} hex - Hex color code
 * @param {number} opacity - Opacity value (0-1)
 * @returns {string} RGBA color string
 */
function hexToRgba(hex, opacity) {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    if (!result) return `rgba(100, 116, 139, ${opacity})`; // fallback gray

    const r = parseInt(result[1], 16);
    const g = parseInt(result[2], 16);
    const b = parseInt(result[3], 16);

    return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}

/**
 * Generate HTML for a data source badge (icon + name with brand color background)
 * @param {string} sourceName - The data source name
 * @param {Object} options - Optional settings { maxLength, showTooltip }
 * @returns {string} HTML string for the badge
 */
function getDataSourceBadgeHTML(sourceName, options = {}) {
    const { maxLength = 12, showTooltip = true } = options;
    const logo = getDataSourceLogo(sourceName);
    const color = logo.color;

    // Truncate name if too long
    let displayName = sourceName || 'Unknown';
    const needsTruncation = displayName.length > maxLength;
    if (needsTruncation) {
        displayName = displayName.substring(0, maxLength - 1) + '…';
    }

    // Escape HTML in names
    const escapedName = displayName.replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));
    const escapedFullName = (sourceName || 'Unknown').replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));

    // Generate logo HTML (small icon)
    const logoHTML = logo.type === 'image'
        ? `<img src="${logo.src}" alt="" class="ds-badge-icon">`
        : `<span class="ds-badge-icon ds-badge-icon-svg">${logo.svg}</span>`;

    // Build badge with inline styles for color (CSS custom property)
    // Use data-tooltip for instant CSS tooltip instead of slow native title
    const tooltipAttr = showTooltip ? `data-tooltip="${escapedFullName}"` : '';

    return `<span class="ds-badge" style="--ds-color: ${color}; --ds-bg: ${hexToRgba(color, 0.12)}; --ds-border: ${hexToRgba(color, 0.25)};" ${tooltipAttr}>${logoHTML}<span class="ds-badge-name">${escapedName}</span></span>`;
}

/**
 * Generate HTML for multiple data source badges (with overflow handling)
 * @param {Array} sources - Array of data source names
 * @param {Object} options - Optional settings { maxVisible, maxLength }
 * @returns {string} HTML string for all badges
 */
function getDataSourceBadgesHTML(sources, options = {}) {
    const { maxVisible = 3, maxLength = 12 } = options;

    if (!sources || !Array.isArray(sources) || sources.length === 0) {
        return '<span class="ds-badge-empty">-</span>';
    }

    const visibleSources = sources.slice(0, maxVisible);
    const hiddenSources = sources.slice(maxVisible);
    const hiddenCount = hiddenSources.length;

    let html = '<div class="ds-badges-container">';

    // Render visible badges
    html += visibleSources.map(src => getDataSourceBadgeHTML(src, { maxLength })).join('');

    // Add hidden badges container and overflow toggle if needed
    if (hiddenCount > 0) {
        // Hidden badges (collapsed by default)
        html += `<div class="ds-badges-overflow-container" style="display: none;">`;
        html += hiddenSources.map(src => getDataSourceBadgeHTML(src, { maxLength })).join('');
        html += `</div>`;

        // Toggle button
        html += `<button class="ds-badge ds-badge-overflow" onclick="DataSourceLogos.toggleBadgeOverflow(this)" data-count="${hiddenCount}">+${hiddenCount}</button>`;
    }

    html += '</div>';
    return html;
}

/**
 * Toggle visibility of overflow badges
 * @param {HTMLElement} btn - The toggle button element
 */
function toggleBadgeOverflow(btn) {
    const container = btn.closest('.ds-badges-container');
    const overflowContainer = container.querySelector('.ds-badges-overflow-container');
    const count = btn.dataset.count;

    if (overflowContainer.style.display === 'none') {
        overflowContainer.style.display = 'contents';
        btn.textContent = '−'; // minus sign
        btn.classList.add('expanded');
    } else {
        overflowContainer.style.display = 'none';
        btn.textContent = `+${count}`;
        btn.classList.remove('expanded');
    }
}

// =============================================================================
// Exports (for module usage)
// =============================================================================

// Support both ES modules and global scope
if (typeof window !== 'undefined') {
    window.DataSourceLogos = {
        DATA_SOURCE_LOGOS,
        FALLBACK_ICON_SVG,
        DEFAULT_BRAND_COLOR,
        getDataSourceLogo,
        getDefaultLogo,
        getDataSourceLogoHTML,
        getDataSourceColor,
        getDataSourceBadgeHTML,
        getDataSourceBadgesHTML,
        toggleBadgeOverflow,
        hexToRgba
    };
}

// For ES module imports
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        DATA_SOURCE_LOGOS,
        FALLBACK_ICON_SVG,
        DEFAULT_BRAND_COLOR,
        getDataSourceLogo,
        getDefaultLogo,
        getDataSourceLogoHTML,
        getDataSourceColor,
        getDataSourceBadgeHTML,
        getDataSourceBadgesHTML,
        toggleBadgeOverflow,
        hexToRgba
    };
}
