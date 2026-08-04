// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * Category palette — single source of truth for asset category colors.
 * Used wherever a category needs a color (icons, badges, charts).
 */
export const CATEGORY_COLORS: Record<string, string> = {
  server: '#3b82f6',
  workstation: '#8b5cf6',
  mobile: '#ea580c',
  network: '#0891b2',
  iot: '#059669',
  ot: '#d97706',
  miscellaneous: '#64748b',
};

/** Normalize a category string (lowercase, strip whitespace) for use as a
 *  CATEGORY_COLORS key. Idempotent — pre-normalized keys pass through. */
export function categoryKey(value: string): string {
  return value.toLowerCase().replace(/\s+/g, '');
}

export function getCategoryColor(value: string): string {
  return CATEGORY_COLORS[categoryKey(value)] || CATEGORY_COLORS.miscellaneous;
}

export function isKnownCategory(value: string | null | undefined): boolean {
  if (!value) return false;
  return categoryKey(value) in CATEGORY_COLORS;
}

interface CategoryIconProps {
  category: string;
  size?: number;
  className?: string;
}

/**
 * Outline SVG icon for an asset category.
 * Categories: server, workstation, mobile, network, iot, ot, miscellaneous (default).
 * Used in dashboard data-intelligence and asset-details header.
 */
export function CategoryIcon({ category, size = 16, className }: CategoryIconProps) {
  const common = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.5,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    className,
  };

  switch (category) {
    case 'server':
      return (
        <svg {...common}>
          <rect x="2" y="2" width="20" height="8" rx="2" />
          <line x1="6" y1="6" x2="6" y2="6" />
          <line x1="10" y1="6" x2="18" y2="6" />
          <rect x="2" y="14" width="20" height="8" rx="2" />
          <line x1="6" y1="18" x2="6" y2="18" />
          <line x1="10" y1="18" x2="18" y2="18" />
        </svg>
      );
    case 'workstation':
      return (
        <svg {...common}>
          <rect x="2" y="3" width="20" height="14" rx="2" />
          <line x1="8" y1="21" x2="16" y2="21" />
          <line x1="12" y1="17" x2="12" y2="21" />
        </svg>
      );
    case 'mobile':
      return (
        <svg {...common}>
          <rect x="5" y="2" width="14" height="20" rx="2" />
          <line x1="12" y1="18" x2="12" y2="18" strokeLinecap="round" strokeWidth="2" />
        </svg>
      );
    case 'network':
      return (
        <svg {...common}>
          <rect x="2" y="13" width="20" height="7" rx="1.5" />
          <line x1="7" y1="13" x2="7" y2="5" />
          <line x1="17" y1="13" x2="17" y2="5" />
          <line x1="6" y1="16.5" x2="8" y2="16.5" />
          <line x1="11" y1="16.5" x2="13" y2="16.5" />
        </svg>
      );
    case 'iot':
      return (
        <svg {...common}>
          <path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5" />
          <line x1="9" y1="18" x2="15" y2="18" />
          <line x1="10" y1="22" x2="14" y2="22" />
        </svg>
      );
    case 'ot':
      return (
        <svg {...common}>
          <rect x="6" y="6" width="12" height="12" rx="1.5" />
          <line x1="9" y1="2" x2="9" y2="6" />
          <line x1="15" y1="2" x2="15" y2="6" />
          <line x1="9" y1="18" x2="9" y2="22" />
          <line x1="15" y1="18" x2="15" y2="22" />
          <line x1="2" y1="9" x2="6" y2="9" />
          <line x1="2" y1="15" x2="6" y2="15" />
          <line x1="18" y1="9" x2="22" y2="9" />
          <line x1="18" y1="15" x2="22" y2="15" />
        </svg>
      );
    default:
      return (
        <svg {...common}>
          <rect x="3" y="3" width="7" height="7" rx="1" />
          <rect x="14" y="3" width="7" height="7" rx="1" />
          <rect x="3" y="14" width="7" height="7" rx="1" />
          <rect x="14" y="14" width="7" height="7" rx="1" />
        </svg>
      );
  }
}
