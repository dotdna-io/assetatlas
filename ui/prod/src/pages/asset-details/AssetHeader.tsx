// SPDX-License-Identifier: AGPL-3.0-or-later
import type { AssetDetail } from '@/api/asset-details';
import { Badge } from '@/components/ui/Badge';
import { CategoryIcon, getCategoryColor } from '@/components/ui/CategoryIcon';
import { assetUrl } from '@/basePath';
import { formatDate } from '../explorer/explorer-utils';
import styles from './asset-details.module.css';

function getLastSeenClass(hours: number | null): string {
  if (hours === null) return '';
  if (hours > 168) return styles.lastSeenStale;
  if (hours > 24) return styles.lastSeenAging;
  return styles.lastSeenRecent;
}

function getAssetTitle(asset: AssetDetail): string {
  return asset.hostname || asset.fqdn || asset.ip?.[0] || asset.mac?.[0] || asset.sn || 'Unknown Asset';
}

function getSubtitle(asset: AssetDetail): string | null {
  const parts: string[] = [];
  if (asset.manufacturer) parts.push(asset.manufacturer);
  if (asset.model) parts.push(asset.model);
  return parts.length > 0 ? parts.join(' - ') : null;
}

interface AssetHeaderProps {
  asset: AssetDetail;
}

export function AssetHeader({ asset }: AssetHeaderProps) {
  const categoryKey = (asset.category || '').toLowerCase();
  const title = getAssetTitle(asset);
  const subtitle = getSubtitle(asset);

  const lsHours = asset.last_seen
    ? (Date.now() - new Date(asset.last_seen).getTime()) / 3_600_000
    : null;
  const lsClass = getLastSeenClass(lsHours);

  const filteredTags = asset.tags.filter((tag) => tag.name.toLowerCase() !== categoryKey);

  return (
    <div className={styles.header}>
        <div className={styles.headerLeft}>
          <div className={styles.categoryIconWrap} style={{ color: getCategoryColor(categoryKey) }}>
            <CategoryIcon category={categoryKey} size={32} className={styles.categoryIcon} />
          </div>
          <div className={styles.headerInfo}>
            <div className={styles.titleRow}>
              <h2 className={styles.assetTitle}>{title}</h2>
              {asset.category && (
                <span className={styles.categoryLabel} style={{ color: getCategoryColor(categoryKey) }}>
                  {asset.category}
                </span>
              )}
              {filteredTags.map((tag) => (
                <Badge key={tag.name} color={tag.color}>{tag.name}</Badge>
              ))}
            </div>
            {subtitle && <span className={styles.assetSubtitle}>{subtitle}</span>}
          </div>
        </div>

        <div className={styles.headerRight}>
          {asset.status.is_compliant !== null && (
            <span
              className={styles.statusText}
              style={{ color: asset.status.is_compliant ? '#10b981' : '#ef4444' }}
            >
              {asset.status.is_compliant ? 'Compliant' : 'Non-Compliant'}
            </span>
          )}

          {asset.status.in_grace && (
            <span className={styles.statusText} style={{ color: '#f59e0b' }}>
              In Grace · {asset.status.days_in_grace ?? 0}d
            </span>
          )}

          {asset.last_seen && (
            <span className={`${styles.lastSeenBadge} ${lsClass}`}>
              {formatDate(asset.last_seen)}
            </span>
          )}

          {(() => {
            const alertCount = asset.status.alert_count ?? 0;
            const hasAlerts = alertCount > 0;
            const alertsHref = hasAlerts
              ? `/alerts?asset_id=${encodeURIComponent(asset.id)}&asset_label=${encodeURIComponent(title)}`
              : '/alerts';
            return (
              <button
                className={`${styles.iconBtn} ${hasAlerts ? styles.iconBtnAlerted : ''}`}
                title={hasAlerts ? `${alertCount} alert${alertCount === 1 ? '' : 's'} for this asset` : 'Alerts'}
                onClick={() => window.open(assetUrl(alertsHref), '_blank')}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="18" height="18">
                  <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
                  <path d="M13.73 21a2 2 0 0 1-3.46 0" />
                </svg>
                {hasAlerts && (
                  <span className={styles.iconBadge}>{alertCount > 99 ? '99+' : alertCount}</span>
                )}
              </button>
            );
          })()}
          <button
            className={styles.iconBtn}
            title="Asset History"
            onClick={() => window.open(assetUrl(`/asset-history?id=${asset.id}`), '_blank')}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="18" height="18">
              <circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" />
            </svg>
          </button>
        </div>
    </div>
  );
}

