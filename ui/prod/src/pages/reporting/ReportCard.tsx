// SPDX-License-Identifier: AGPL-3.0-or-later
import type { Report } from '@/api/reports';
import { assetUrl } from '@/basePath';
import { timeAgo } from '@/utils/formatters';
import { CHART_TYPES, parseSize } from './data/chartConfig';
import styles from './ReportCard.module.css';

/** Build a lookup: chartId UUID → icon filename. */
const ICON_BY_ID = new Map(CHART_TYPES.map((t) => [t.chartId, t.icon]));
const DEFAULT_ICON = 'vertical-bar-chart.svg';

/** Max grid row to show in the preview. */
const MAX_PREVIEW_ROW = 4;

interface ReportCardProps {
  report: Report;
}

export function ReportCard({ report }: ReportCardProps) {
  const charts = (report.charts || []) as Array<Record<string, any>>;
  const isBuiltIn = report.created_by === null;

  // Charts whose top edge is within the first ~3 visual rows, skip DataTables
  const previewCharts = charts.filter(
    (c) => c.gridRow <= MAX_PREVIEW_ROW && c.className !== 'DotDNADataTable',
  );

  const handleClick = () => {
    window.open(assetUrl(`/reporting/${report.id}`), '_blank');
  };

  return (
    <div className={styles.card} onClick={handleClick} role="link" tabIndex={0}>
      {isBuiltIn && (
        <div className={styles.badge}>
          <img src={assetUrl('/logos/asset-atlas-symbol.png')} alt="AssetAtlas" />
        </div>
      )}

      {previewCharts.length > 0 ? (
        <div className={styles.previewWrap}>
          <div className={styles.previewGrid}>
            {previewCharts.map((chart) => {
              const { cols, rows } = parseSize(chart.size);
              const icon = ICON_BY_ID.get(chart.chartId) || DEFAULT_ICON;
              return (
                <div
                  key={chart.id}
                  className={styles.previewCell}
                  style={{
                    gridRow: `${chart.gridRow} / span ${rows}`,
                    gridColumn: `${chart.gridColumn} / span ${cols}`,
                  }}
                >
                  <img
                    src={assetUrl(`/chart-icons/${icon}`)}
                    alt={chart.name || 'chart'}
                    loading="lazy"
                  />
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className={styles.emptyPreview}>No charts</div>
      )}

      <div className={styles.glassOverlay}>
        <h3 className={styles.title}>{report.title}</h3>
        {report.description && (
          <p className={styles.description}>{report.description}</p>
        )}
        <div className={styles.meta}>
          <span>{charts.length} chart{charts.length !== 1 ? 's' : ''}</span>
          <span>Updated {timeAgo(report.updated_at)}</span>
        </div>
      </div>
    </div>
  );
}
