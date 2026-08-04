// SPDX-License-Identifier: AGPL-3.0-or-later
import { useEffect, useRef } from 'react';
import { assetUrl } from '@/basePath';
import styles from './dashboard.module.css';
import { useDashboardConfidence } from '@/api/dashboard';

// EXPERIMENT: repurposes the posture chevrons to show the confidence-level
// distribution — Confirmed / Under-covered / Shadows — with the same
// green/amber/red chevron shapes and per-category gauges.

type Band = 'confirmed' | 'under_covered' | 'shadows';
type ConfidenceData = Record<string, Record<Band, number>>;

const CAT_LABELS: Record<string, string> = {
  server: 'Server', workstation: 'Workstation', mobile: 'Mobile',
  network: 'Network', iot: 'IoT', ot: 'OT', miscellaneous: 'Misc',
};

const CAT_ORDER = ['server', 'workstation', 'mobile', 'network', 'ot', 'iot', 'miscellaneous'];
const CAT_RANK = new Map(CAT_ORDER.map((k, i) => [k, i]));
const catRank = (k: string) => CAT_RANK.get(k) ?? Number.MAX_SAFE_INTEGER;

const BANDS: { id: Band; label: string; subtitle: string; cssColor: string }[] = [
  { id: 'confirmed', label: 'Confirmed', subtitle: 'Corroborated across your sources.', cssColor: 'var(--success)' },
  { id: 'under_covered', label: 'Under-covered', subtitle: 'Missing from sources that should have them.', cssColor: 'var(--warning)' },
  { id: 'shadows', label: 'Shadows', subtitle: 'Seen by one source, proven by none.', cssColor: 'var(--error)' },
];

function getSegPositionClass(index: number): string {
  if (index === 0) return styles.flowSegFirst;
  if (index === 1) return styles.flowSegMiddle;
  return styles.flowSegLast;
}

function getBandBgClass(id: Band): string {
  if (id === 'confirmed') return styles.secured;
  if (id === 'under_covered') return styles.monitored;
  return styles.discovered;
}

function getBandColorClass(id: Band): string {
  if (id === 'confirmed') return styles.flowTierSecured;
  if (id === 'under_covered') return styles.flowTierMonitored;
  return styles.flowTierDiscovered;
}

export function ConfidenceFlow() {
  const containerRef = useRef<HTMLDivElement>(null);
  const { data } = useDashboardConfidence();
  const breakdown: ConfidenceData = data?.breakdown ?? {};

  const totals: Record<Band, number> = { confirmed: 0, under_covered: 0, shadows: 0 };
  Object.values(breakdown).forEach((v) => {
    totals.confirmed += v.confirmed;
    totals.under_covered += v.under_covered;
    totals.shadows += v.shadows;
  });

  // Animate the per-category mini bars after mount.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const timer = setTimeout(() => {
      el.querySelectorAll<HTMLElement>('[data-pct]').forEach((bar) => {
        bar.style.width = bar.dataset.pct + '%';
      });
    }, 200);
    return () => clearTimeout(timer);
  }, [breakdown]);

  const cats = Object.entries(breakdown).sort(([a], [b]) => catRank(a) - catRank(b));

  return (
    <div className={`${styles.postureFlow} ${styles.confFlow}`} ref={containerRef}>
      {BANDS.map((band, idx) => {
        return (
          <div
            key={band.id}
            className={`${styles.flowSeg} ${getSegPositionClass(idx)} ${getBandBgClass(band.id)}`}
          >
            {/* Horizontal header: title + subtitle left, big number right. */}
            <div className={styles.confHeader}>
              <div className={styles.confHeaderText}>
                <div className={`${styles.flowTier} ${getBandColorClass(band.id)}`}>{band.label}</div>
                <div className={styles.confSub}>{band.subtitle}</div>
              </div>
              <div className={styles.flowNum}>{totals[band.id].toLocaleString()}</div>
            </div>

            <div className={styles.flowCats}>
              {cats.map(([catKey, catVal]) => {
                const val = catVal[band.id];
                // Bar = this band's share of the category's total (e.g. a
                // server that's 32% confirmed fills ~32% of the line).
                const catTotal = catVal.confirmed + catVal.under_covered + catVal.shadows;
                const pct = catTotal > 0 ? Math.round((val / catTotal) * 100) : 0;
                return (
                  <div
                    className={styles.flowCatRow}
                    key={catKey}
                    onClick={() => window.open(
                      assetUrl(`/explorer?filter_category=${encodeURIComponent(catKey)}&confidence=${band.id}`),
                      '_blank',
                    )}
                    role="link"
                    tabIndex={0}
                  >
                    <span className={styles.flowCatName}>{CAT_LABELS[catKey] || catKey}</span>
                    <div className={styles.flowCatBarWrap}>
                      <div
                        className={styles.flowCatBar}
                        style={{ width: '0%', background: band.cssColor }}
                        data-pct={pct}
                      />
                    </div>
                    <span className={styles.flowCatCount}>{val.toLocaleString()}</span>
                    <span className={styles.flowCatPct}>{pct}%</span>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
