// SPDX-License-Identifier: AGPL-3.0-or-later
import { useDocumentTitle } from '@/hooks';
import { useState, useMemo, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAlerts } from '@/api/alerts';
import type { Alert, AlertsParams } from '@/api/alerts';
import { Badge } from '@/components/ui/Badge';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { TimeRangeGroup } from '@/components/ui/TimeRangeGroup';
import { AlertsOmniSearch } from './alerts/AlertsOmniSearch';
import { AlertsTimeline, bucketKey, type TimelineBarFilter } from './alerts/AlertsTimeline';
import { CategoryPie } from './alerts/CategoryPie';
import { AlertEvidence } from './alerts/AlertEvidence';
import { AlertSubjects } from './alerts/AlertSubjects';
import { AlertHistoryChart } from './alerts/AlertHistoryChart';
import { SEVERITY_COLORS } from '@/constants/severity';
import { ALERT_TYPE_COLORS, ALERT_TYPE_LABELS } from '@/constants/alertTypes';
import { timeAgo } from '@/utils/formatters';
import { EyeIcon } from '@/components/ui/EyeIcon';
import { assetUrl } from '@/basePath';
import styles from './alerts/alerts.module.css';
import { CategoryTag } from '@/components/ui/CategoryTag';

/** Explorer search token addressing one alert.
 *
 * An OPEN alert is addressed by NAME — readable, autocompleting, and saveable
 * into a saved filter. A resolved one cannot be: at most one alert per rule is
 * open at a time and resolved rows accumulate under the same title, so
 * `Alert = "<name>"` selects the OPEN row for that rule and would answer the
 * wrong set — or zero — for a historical card. Those send the id, which the
 * same filter field also accepts.
 */
export function alertSearchToken(
  alert: { id: string; title: string; status: string },
): string {
  if (alert.status !== 'active' && alert.status !== 'acknowledged') {
    return `Alert = ${alert.id}`;
  }
  // The parser strips one wrapping quote pair and has no escape syntax, so use
  // the quote character the title does not contain.
  const q = alert.title.includes('"') ? "'" : '"';
  return `Alert = ${q}${alert.title}${q}`;
}

export default function AlertsPage() {
  useDocumentTitle('Alerts');
  const [searchParams, setSearchParams] = useSearchParams();
  const assetIdFilter = searchParams.get('asset_id') || '';
  const assetLabelFilter = searchParams.get('asset_label') || '';

  const [timeRange, setTimeRange] = useState('30d');
  const [severity, setSeverity] = useState('');
  // Alert origin: '' (all) | 'system' (AssetAtlas out-of-the-box) | 'custom'.
  // Client-side — it narrows the loaded set for the cards, chart and list.
  const [search, setSearch] = useState('');
  const [barFilter, setBarFilter] = useState<TimelineBarFilter | null>(null);
  // Pie-slice category filter — client-side, narrows the list only (the pie
  // and chart keep the full distribution), toggled by clicking a slice.
  const [pieCategory, setPieCategory] = useState<string | null>(null);
  const [customRange, setCustomRange] = useState<{ start: string; end: string } | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const params: AlertsParams = {
    time_range: timeRange,
    ...(severity && { severity }),
    ...(search && { search }),
    // Exact server-side asset filter (#903/#908): scopes the list, count,
    // summary and timeline to this asset — not just the first loaded page.
    ...(assetIdFilter && { asset_id: assetIdFilter }),
  };

  const { data, isLoading, error } = useAlerts(params);

  // #904: the anchor we highlight against — the most recent completed pipeline
  // run, exposed top-level by the alerts API (#906). Null before any run lands.

  const clearAssetFilter = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('asset_id');
      next.delete('asset_label');
      return next;
    });
  }, [setSearchParams]);

  const selectAssetFilter = useCallback((id: string, label: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('asset_id', id);
      if (label) next.set('asset_label', label);
      else next.delete('asset_label');
      return next;
    });
  }, [setSearchParams]);

  const handleBarClick = useCallback((sev: string, date: string) => {
    setBarFilter((prev) =>
      prev?.severity === sev && prev?.date === date ? null : { severity: sev, date },
    );
  }, []);

  // Local YYYY-MM-DD from an ISO timestamp (used by custom-range filtering only;
  // AlertsTimeline's bucketKey() handles the chart-level matching).
  const toLocalDay = useCallback((iso: string) => {
    const d = new Date(iso);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }, []);

  // Stage 1: time-range + origin filter (feeds cards + chart + list)
  const timeFilteredAlerts = useMemo(() => {
    if (!data?.alerts) return [];
    const now = Date.now();
    const presetMs: Record<string, number> = {
      '24h': 24 * 3600000,
      '7d': 7 * 86400000,
      '30d': 30 * 86400000,
    };
    let list = data.alerts;
    if (timeRange === 'custom' && customRange) {
      list = list.filter((a) => {
        const day = toLocalDay(a.created_at);
        return day >= customRange.start && day <= customRange.end;
      });
    } else if (presetMs[timeRange]) {
      const cutoff = now - presetMs[timeRange];
      list = list.filter((a) => new Date(a.created_at).getTime() >= cutoff);
    }
    return list;
  }, [data?.alerts, timeRange, customRange, toLocalDay]);

  // Stage 2: bar click filter (only affects list). Match by bucket key so
  // 24h hourly clicks narrow to that hour, while daily clicks narrow to that day.
  // (The asset filter is server-side now — see the asset_id param — so there is
  // no client-side asset narrowing here; a client filter would silently re-cap
  // the result to the first loaded page, the very bug #908 fixes.)
  const filteredAlerts = useMemo(() => {
    let list = timeFilteredAlerts;
    if (barFilter) {
      list = list.filter(
        (a) =>
          a.severity === barFilter.severity &&
          bucketKey(a.created_at, timeRange) === barFilter.date,
      );
    }
    if (pieCategory) {
      list = list.filter((a) => a.type === pieCategory);
    }
    return list;
  }, [timeFilteredAlerts, barFilter, timeRange, pieCategory]);

  return (
    <div>
      {/* Time picker */}
      <div className={styles.topBar}>
        <div />
        <TimeRangeGroup
          value={timeRange}
          onChange={(v) => {
            setTimeRange(v);
            if (v !== 'custom') setCustomRange(null);
          }}
          customRange={customRange}
          onCustomRange={(start, end) => setCustomRange({ start, end })}
        />
      </div>

      {/* Top section: category pie (30%) + combined timeline (70%) */}
      <div className={styles.topSection}>
        <CategoryPie
          alerts={timeFilteredAlerts}
          activeCategory={pieCategory}
          onCategoryToggle={(cat) => setPieCategory((prev) => (prev === cat ? null : cat))}
        />
        <AlertsTimeline
          alerts={timeFilteredAlerts}
          timeRange={timeRange}
          customRange={customRange}
          activeFilter={barFilter}
          onSegmentClick={handleBarClick}
        />
      </div>


      {/* Toolbar */}
      <div className={styles.toolbarRow}>
        <div className={styles.toolbar}>
          <AlertsOmniSearch
            search={search}
            assetId={assetIdFilter}
            assetLabel={assetLabelFilter}
            onSearch={setSearch}
            onAssetSelect={selectAssetFilter}
            onAssetClear={clearAssetFilter}
            width={420}
          />
          <select
            className={styles.severitySelect}
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
          >
            <option value="">All Severities</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </div>
        <span className={styles.totalAlerts}>
          {(barFilter || pieCategory) ? (
            // A timeline-bar click narrows the loaded page client-side, so
            // show "N of <total>". The asset filter is server-side now, so its
            // total lives in summary.total and reads through the plain branch
            // below.
            <>
              <strong>{filteredAlerts.length}</strong> of {data?.summary?.total} alerts
              <button className={styles.clearFilter} onClick={() => { setBarFilter(null); setPieCategory(null); }}>Clear</button>
            </>
          ) : (
            data?.summary && <><strong>{data.summary.total}</strong> alerts</>
          )}
        </span>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className={styles.loadingWrap}>
          <LoadingSpinner />
        </div>
      )}

      {/* Error */}
      {error && (
        <div className={styles.empty}>Failed to load alerts</div>
      )}

      {/* Alert list */}
      {data?.alerts && data.alerts.length === 0 && !isLoading && (
        <div className={styles.empty}>No alerts found for this time range</div>
      )}

      {filteredAlerts.map((alert: Alert) => {
        const isPrometheus = alert.source === 'prometheus';
        const subjectType = alert.subject_type || 'asset';
        const isAssetSubject = subjectType === 'asset';
        const categories = !isPrometheus && isAssetSubject && alert.assets_by_category
          ? Object.keys(alert.assets_by_category)
          : [];
        const isExpanded = expandedId === alert.id;
        // Only pipeline-evaluated alerts have history to chart (prometheus /
        // non-pipeline system alerts carry no run id), and the chart tracks
        // involved assets over time — so it only makes sense for alerts that
        // actually involve assets.
        const hasHistory =
          !!alert.last_pipeline_run_id && isAssetSubject && alert.asset_count > 0;
        const visibleAssets = !isPrometheus && isAssetSubject ? alert.assets.slice(0, 6) : [];
        const moreCount = !isPrometheus && isAssetSubject ? alert.assets.length - 6 : 0;
        const instance = isPrometheus
          ? (alert.labels?.instance || alert.labels?.service || alert.labels?.job)
          : null;
        return (
          <div
            key={alert.id}
            data-testid="alert-item"
            className={`${styles.alertItem} ${isExpanded ? styles.alertItemExpanded : ''}`}
            onClick={() => setExpandedId(isExpanded ? null : alert.id)}
          >
            <div className={styles.alertItemBody}>
              <div className={styles.alertItemRow}>
                <div className={styles.alertItemLeft}>
                  <div className={styles.alertItemHeader}>
                    <span
                      className={styles.alertSeverity}
                      style={{ color: SEVERITY_COLORS[alert.severity] || '#64748b' }}
                    >
                      {alert.severity}
                    </span>
                    <span className={styles.alertSep} />
                    {/* No marker for AssetAtlas alerts — they're the vast
                        majority, so a per-row logo is pure repetition. Only
                        the exceptions announce themselves. */}
                    {isPrometheus && <Badge color="#e6522c">Prometheus</Badge>}
                    {!isPrometheus && alert.source !== 'system' && (
                      <>
                        <span className={styles.alertSeverity} style={{ color: '#64748b' }}>
                          Custom
                        </span>
                        <span className={styles.alertSep} />
                      </>
                    )}
                    <span className={styles.alertItemTitle}>{alert.title}</span>
                  </div>
                </div>
                <div className={styles.alertItemRight}>
                  {isPrometheus && instance && (
                    <span className={styles.alertItemCount}>Instance: {instance}</span>
                  )}
                  {ALERT_TYPE_LABELS[alert.type] && (
                    <>
                      <span
                        className={styles.alertSeverity}
                        style={{ color: ALERT_TYPE_COLORS[alert.type] }}
                      >
                        {ALERT_TYPE_LABELS[alert.type]}
                      </span>
                      <span className={styles.alertSep} />
                    </>
                  )}
                  {!isPrometheus && categories.length > 0 && (
                    <>
                      <div className={styles.alertCategories}>
                        {categories.map((cat) => (
                          <CategoryTag key={cat} category={cat} iconOnly />
                        ))}
                      </div>
                      <span className={styles.alertSep} />
                    </>
                  )}
                  {!isPrometheus && alert.asset_count > 0 && (
                    <>
                      <span className={styles.alertItemCount}>
                        {alert.asset_count} {subjectType === 'integration' ? 'data sources'
                          : subjectType === 'audit_event' ? 'events' : 'assets'}
                      </span>
                      <span className={styles.alertSep} />
                    </>
                  )}
                  <span className={styles.alertItemTime}>{timeAgo(alert.created_at)}</span>
                </div>
              </div>

              {isExpanded && (
                <div className={styles.expandedWrap} onClick={(e) => e.stopPropagation()}>
                  <div className={styles.expandedContent}>
                  <div className={styles.expandedLeft}>
                    {alert.description && (
                      <div className={styles.expandedSection}>
                        <span className={styles.expandedLabel}>Description</span>
                        <p className={styles.expandedText}>{alert.description}</p>
                      </div>
                    )}
                    {/* Asset alerts show Reason at the top of the right pane;
                        every other kind (prometheus, integration, audit) keeps
                        it here — their right pane is absent or lists subjects. */}
                    {alert.reason && alert.reason !== alert.description
                      && (isPrometheus || !isAssetSubject) && (
                      <div className={styles.expandedSection}>
                        <span className={styles.expandedLabel}>Reason</span>
                        <p className={styles.expandedText}>{alert.reason}</p>
                      </div>
                    )}
                    {hasHistory && <AlertHistoryChart alertId={alert.id} />}
                    {alert.evidence && Object.keys(alert.evidence).length > 0 && (
                      <AlertEvidence evidence={alert.evidence} reason={alert.reason} />
                    )}
                  </div>
                  {!isPrometheus && (
                    isAssetSubject ? (
                      <div className={styles.expandedRight}>
                        {alert.reason && alert.reason !== alert.description && (
                          <div className={styles.expandedSection}>
                            <span className={styles.expandedLabel}>Reason</span>
                            <p className={styles.expandedText}>{alert.reason}</p>
                          </div>
                        )}
                        <span className={styles.expandedLabel}>Involved Assets</span>
                        <div className={styles.assetGrid}>
                          {visibleAssets.map((asset) => (
                            <div key={asset.id} className={styles.assetCard}>
                              <div className={styles.assetCardInfo}>
                                <span className={styles.assetCardName}>{asset.label || asset.hostname || asset.ip || 'Unknown'}</span>
                                <span className={styles.assetCardMeta}>
                                  {[asset.model, asset.ip].filter(Boolean).join(' \u00b7 ')}
                                </span>
                              </div>
                              <div className={styles.assetCardActions}>
                                <a
                                  href={`/asset-details?id=${asset.id}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className={styles.assetCardBtn}
                                >
                                  <EyeIcon size={14} />
                                </a>
                                <a
                                  href={`/asset-history?id=${asset.id}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className={styles.assetCardBtn}
                                >
                                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14">
                                    <circle cx="12" cy="12" r="10" />
                                    <polyline points="12 6 12 12 16 14" />
                                  </svg>
                                </a>
                              </div>
                            </div>
                          ))}
                        </div>
                        {/* The grid above stops at 12, so for anything larger
                            this is the only way to reach the rest. The link
                            carries one Alert search token, never the asset
                            ids: the alert's name when open (readable and
                            saveable into a saved filter), its id when
                            resolved (a resolved row's name would select the
                            OPEN row for that rule instead). Either way the
                            Explorer asks the server to resolve it, so an
                            alert naming 35k assets links like one naming
                            three (#907). Inside the isAssetSubject branch —
                            an integration/audit alert's subjects are not
                            assets, so an Explorer link would be a dead end. */}
                        {(moreCount > 0 || alert.asset_count > 0) && (
                          <div className={styles.assetGridFooter}>
                            {moreCount > 0 && (
                              <span className={styles.assetMore}>+{moreCount} more assets</span>
                            )}
                            {alert.asset_count > 0 && (
                              <a
                                href={assetUrl(
                                  `/explorer?search=${encodeURIComponent(alertSearchToken(alert))}`,
                                )}
                                target="_blank"
                                rel="noopener noreferrer"
                                className={styles.viewInExplorer}
                              >
                                View {alert.asset_count}
                                {alert.asset_count === 1 ? ' asset' : ' assets'} in Explorer
                              </a>
                            )}
                          </div>
                        )}
                      </div>
                    ) : (
                      <AlertSubjects subjectType={subjectType} subjects={alert.subjects} />
                    )
                  )}
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
