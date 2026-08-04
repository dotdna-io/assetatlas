// SPDX-License-Identifier: AGPL-3.0-or-later
import { useMemo } from 'react';
import { hexToRgba, DEFAULT_BRAND_COLOR, resolveDataSourceLogo, type DataSourceLogo } from '@/pages/explorer/search/data-source-logos';
import { resolveLogoUrl } from '@/lib/logoUrl';
import { useDarkMode } from '@/hooks';
import { useIntegrationGroups, useLogoMap } from '@/api/integrations';
import styles from './DataSourceBadge.module.css';

interface DataSourceBadgeProps {
  name: string;
  tinted?: boolean;
  className?: string;
}

/** WCAG relative luminance (0 = black, 1 = white) of a #rrggbb colour. */
function relLuminance(hex: string): number {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!m) return 0.5;
  const [r, g, b] = [m[1], m[2], m[3]].map((h) => {
    const c = parseInt(h, 16) / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Brand colour for the name, falling back to neutral text when the brand
 *  colour would be too low-contrast against the current theme background
 *  (near-black brands on dark, very light/yellow brands on light). */
function brandTextColor(hex: string, isDark: boolean): string {
  const L = relLuminance(hex);
  if (isDark) return L < 0.05 ? 'var(--text-primary)' : hex;
  return L > 0.5 ? 'var(--text-primary)' : hex;
}

/** Resolve a data-source name to its logo/colour. Sources appear in asset data
 *  by their operator-chosen name (e.g. "Acme Intune"), which need not match any
 *  brand, so we resolve in priority order: the integration's own logo from the
 *  backend logo-map → the static brand map → the integration_type of the group
 *  the name belongs to. See resolveDataSourceLogo. */
function useResolveSourceLogo(): (name: string) => DataSourceLogo | null {
  const { data: groupsData } = useIntegrationGroups();
  const { data: logoMapData } = useLogoMap();
  return useMemo(() => {
    const typeByName = new Map<string, string>();
    for (const g of groupsData?.groups ?? []) {
      typeByName.set(g.name.toLowerCase().trim(), g.integration_type);
    }
    const logoByName = new Map<string, string>();
    for (const [n, url] of Object.entries(logoMapData?.logoMap ?? {})) {
      if (url) logoByName.set(n.toLowerCase().trim(), url);
    }
    return (name: string): DataSourceLogo | null =>
      resolveDataSourceLogo(name, logoByName, typeByName);
  }, [groupsData, logoMapData]);
}

/**
 * Data-source tag. With `tinted`, renders a glossy brand-tinted icon chip
 * (logo inside) followed by the source name in the brand colour — matching
 * the category tag's chip language. Without `tinted`, a bare logo + neutral
 * name for dense, full-name lists.
 */
export function DataSourceBadge({ name, tinted, className }: DataSourceBadgeProps) {
  const { isDarkMode } = useDarkMode();
  const resolveLogo = useResolveSourceLogo();
  const logoInfo = resolveLogo(name);
  const color = logoInfo?.color ?? DEFAULT_BRAND_COLOR;
  const logoSrc = logoInfo ? resolveLogoUrl(logoInfo.logo) : null;

  const logoEl = logoSrc ? (
    <img src={logoSrc} alt="" className={styles.logo} />
  ) : (
    <span className={styles.fallbackIcon}>{name.charAt(0).toUpperCase()}</span>
  );

  if (tinted) {
    return (
      <span className={`${styles.badge} ${className ?? ''}`}>
        <span className={styles.chip} style={{ backgroundColor: hexToRgba(color, isDarkMode ? 0.28 : 0.15) }}>
          {logoEl}
        </span>
        <span className={styles.tagName} style={{ color: brandTextColor(color, isDarkMode) }}>{name}</span>
      </span>
    );
  }

  return (
    <span className={`${styles.badge} ${className ?? ''}`}>
      {logoEl}
      <span className={styles.name}>{name}</span>
    </span>
  );
}
