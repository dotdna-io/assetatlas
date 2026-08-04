// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useState } from 'react';
import { resolveSourceLogo } from '@/lib/logoUrl';
import { assetUrl } from '@/basePath';
import { CategoryIcon, CATEGORY_COLORS } from '@/components/ui/CategoryIcon';
import styles from './dashboard.module.css';

interface SourceItem {
  name: string;
  integration_id?: string;
  id?: string;
  logo?: string | null;
  integration_type?: string;
  asset_count?: number;
  assetCount?: number;
  last_sync_at?: string;
  lastSync?: string;
  // Average confidence score for the source's assets (0-100).
  confidence?: number;
  // Per-source asset counts by canonical category — drives the mini bars.
  categoryDistribution?: Record<string, number>;
  category?: string;
}

interface DataSourcesProps {
  sources: SourceItem[];
  // Hide the section title (used when nested inside an expanded group panel —
  // the group badge already serves as the heading).
  hideHeading?: boolean;
  // Override the section title — defaults to "Data Sources".
  heading?: string;
}

// Fixed canonical order so the bars line up column-for-column across every
// row — you can scan "which sources have servers" down the table.
const CAT_ORDER = ['server', 'workstation', 'mobile', 'network', 'iot', 'ot', 'miscellaneous'] as const;

/** Compact "which categories does this source carry" glance — a coloured
 *  icon per category present, dominant first. No magnitude (that's on the
 *  source detail page); keeps the row to a single line so the list scales. */
export function CategoryIcons({ distribution }: { distribution?: Record<string, number> }) {
  const dist = distribution ?? {};
  const present = CAT_ORDER
    .map((c) => ({ cat: c, count: dist[c] || 0 }))
    .filter((r) => r.count > 0)
    .sort((a, b) => b.count - a.count);

  if (present.length === 0) return <span className={styles.srcIconsEmpty}>—</span>;

  return (
    <div className={styles.srcIcons} aria-label="Categories present">
      {present.map((r) => (
        <span key={r.cat} className={styles.srcIcon} style={{ color: CATEGORY_COLORS[r.cat] || '#64748b' }}>
          <CategoryIcon category={r.cat} size={18} />
        </span>
      ))}
    </div>
  );
}

// Map a source's average confidence score (0-100) to a level + colour, using
// the dashboard's canonical thresholds (high >= 70, medium >= 45, low < 45).
function getConfidenceLevel(score?: number): { label: string; cls: string } {
  const c = score ?? 0;
  if (c >= 70) return { label: 'High confidence', cls: styles.srcConfHigh };
  if (c >= 45) return { label: 'Med confidence', cls: styles.srcConfMed };
  return { label: 'Low confidence', cls: styles.srcConfLow };
}

function getSyncInfo(src: SourceItem): { cls: string; text: string } {
  const syncTime = src.last_sync_at || src.lastSync;
  if (!syncTime) return { cls: styles.syncDotOk, text: 'Just now' };

  const hrs = Math.floor((Date.now() - new Date(syncTime).getTime()) / 3600000);
  const days = Math.floor(hrs / 24);

  if (days > 7) return { cls: styles.syncDotStale, text: `${days}d ago` };
  if (days > 1) return { cls: styles.syncDotAging, text: `${days}d ago` };
  if (hrs > 0) return { cls: styles.syncDotOk, text: `${hrs}h ago` };
  return { cls: styles.syncDotOk, text: 'Just now' };
}

// One data source as a table row: logo · name · total · category mix · sync.
// Shared between the standalone Data Sources table and an expanded group panel.
export function DataSourceRow({ src }: { src: SourceItem }) {
  const count = src.asset_count || src.assetCount || 0;
  const logoSrc = resolveSourceLogo(src.logo, src.name);
  const syncInfo = getSyncInfo(src);
  const conf = getConfidenceLevel(src.confidence);
  const srcId = src.integration_id || src.id;

  return (
    <div
      className={styles.srcRow}
      onClick={() => window.open(assetUrl(`/datasource-details?id=${srcId}&source=${encodeURIComponent(src.name)}`), '_blank')}
    >
      <div className={styles.srcRowLogo}>
        {logoSrc ? (
          <img src={logoSrc} alt={src.name} />
        ) : (
          <svg width="26" height="26" viewBox="0 0 28 28" fill="none">
            <rect width="28" height="28" rx="6" fill="var(--card-border)" />
            <text x="14" y="14" textAnchor="middle" dominantBaseline="central" fontSize="11" fontWeight="700" fill="var(--text-tertiary)">
              {src.name.charAt(0).toUpperCase()}
            </text>
          </svg>
        )}
      </div>
      <div className={styles.srcRowName}>{src.name}</div>
      <span className={styles.srcSep} aria-hidden />
      <CategoryIcons distribution={src.categoryDistribution} />
      <span className={styles.srcSep} aria-hidden />
      <div className={styles.srcRowCount}>{count.toLocaleString()}</div>
      <span className={styles.srcSep} aria-hidden />
      <span className={`${styles.srcRowConf} ${conf.cls}`}>{conf.label}</span>
      <span className={styles.srcSep} aria-hidden />
      <div className={styles.srcRowSync}>
        <span className={`${styles.syncDot} ${syncInfo.cls}`} />
        {syncInfo.text}
      </div>
    </div>
  );
}

// Two-up table of source rows, busiest first.
export function DataSourceTable({ sources }: { sources: SourceItem[] }) {
  const sorted = [...sources].sort(
    (a, b) => (b.asset_count || b.assetCount || 0) - (a.asset_count || a.assetCount || 0),
  );
  return (
    <div className={styles.srcTable}>
      {sorted.map((src) => (
        <DataSourceRow key={src.name} src={src} />
      ))}
    </div>
  );
}

export function DataSources({ sources, hideHeading, heading }: DataSourcesProps) {
  if (sources.length === 0) return null;

  return (
    <div>
      {!hideHeading && <div className={styles.label}>{heading ?? 'Data Sources'}</div>}
      <DataSourceTable sources={sources} />
    </div>
  );
}

export function DataSourcesSkeleton() {
  return (
    <div aria-busy="true">
      <div className={styles.label}>Data Sources</div>
      <div className={styles.srcTable}>
        {Array.from({ length: 9 }).map((_, i) => (
          <div key={i} className={styles.srcRow}>
            <span className={`${styles.skeleton} ${styles.skelSrcLogo} ${styles.skelBlock}`} />
            <span className={`${styles.skeleton} ${styles.skelSrcName}`} />
            <span className={`${styles.skeleton} ${styles.skelSrcCount}`} />
            <span className={`${styles.skeleton} ${styles.skelSrcIcons}`} />
            <span className={`${styles.skeleton} ${styles.skelSrcSync}`} />
          </div>
        ))}
      </div>
    </div>
  );
}
