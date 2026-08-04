// SPDX-License-Identifier: AGPL-3.0-or-later
import type { HistoryAsset } from '@/api/asset-history';
import type { AssetDetail } from '@/api/asset-details';
import { Badge } from '@/components/ui/Badge';
import { TimeRangeGroup } from '@/components/ui/TimeRangeGroup';
import { EyeIcon } from '@/components/ui/EyeIcon';
import { CategoryIcon, getCategoryColor } from '@/components/ui/CategoryIcon';
import { assetUrl } from '@/basePath';
import styles from './asset-history.module.css';

interface HistoryHeaderProps {
  asset: HistoryAsset;
  assetDetail: AssetDetail | null;
  timeRange: string;
  onTimeRangeChange: (range: string) => void;
}

function getAssetTitle(asset: HistoryAsset): string {
  return asset.hostname || asset.ip || asset.mac || 'Unknown Asset';
}

export function HistoryHeader({ asset, assetDetail, timeRange, onTimeRangeChange }: HistoryHeaderProps) {
  const title = getAssetTitle(asset);
  const categoryKey = (asset.category || '').toLowerCase();

  const subtitle = assetDetail
    ? [assetDetail.manufacturer, assetDetail.model].filter(Boolean).join(' - ')
    : null;

  const tags = assetDetail?.tags?.filter((t) => t.name.toLowerCase() !== categoryKey) ?? [];

  return (
    <div className={styles.header}>
      <div className={styles.headerLeft}>
        <div className={styles.categoryIconWrap} style={{ color: getCategoryColor(categoryKey) }}>
          <CategoryIcon category={categoryKey} size={32} className={styles.categoryIcon} />
        </div>
        <div className={styles.headerInfo}>
          <div className={styles.titleRow}>
            <h2 className={styles.title}>{title}</h2>
            {asset.category && (
              <span className={styles.categoryLabel} style={{ color: getCategoryColor(categoryKey) }}>
                {asset.category}
              </span>
            )}
            {tags.map((tag) => (
              <Badge key={tag.name} color={tag.color}>{tag.name}</Badge>
            ))}
          </div>
          {subtitle && <span className={styles.subtitle}>{subtitle}</span>}
        </div>
      </div>

      <div className={styles.headerRight}>
        <TimeRangeGroup value={timeRange} onChange={onTimeRangeChange} />
        <button
          className={styles.iconBtn}
          title="Alerts"
          onClick={() => window.open(assetUrl('/alerts'), '_blank')}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="18" height="18">
            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
            <path d="M13.73 21a2 2 0 0 1-3.46 0" />
          </svg>
        </button>
        <button
          className={styles.iconBtn}
          title="Asset Details"
          onClick={() => window.open(assetUrl(`/asset-details?id=${asset.id}`), '_blank')}
        >
          <EyeIcon size={18} />
        </button>
      </div>
    </div>
  );
}
