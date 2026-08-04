// SPDX-License-Identifier: AGPL-3.0-or-later
import { Fragment } from 'react';
import {
  usePipelineRunDetail,
  usePipelineRunPhases,
  type PipelinePhase,
  type PipelineSubStage,
} from '@/api/pipeline';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { Badge } from '@/components/ui/Badge';
import styles from './RunDetailPanel.module.css';

interface RunDetailPanelProps {
  runId: string;
}

function formatStageDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '--';
  if (seconds < 0.01) return '<0.01s';
  if (seconds < 1) return seconds.toFixed(2) + 's';
  if (seconds < 60) return seconds.toFixed(1) + 's';
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return mins + 'm ' + secs + 's';
}

// Phase status — rendered as plain colored text (no badge chip) so the
// row reads as "name → status → duration" without competing visual
// weight from chip backgrounds. Color tracks the same semantic tokens
// the Badge variants used.
const STATUS_COLOR_CLASS: Record<string, string> = {
  success: styles.statusSuccess,
  completed: styles.statusSuccess,
  failed: styles.statusError,
  running: styles.statusInfo,
  pending: styles.statusInfo,
};

function StageBars({ subStages }: { subStages: PipelineSubStage[] }) {
  if (subStages.length === 0) {
    return <div className={styles.emptyDetail}>No stage data</div>;
  }
  const maxDur = Math.max(...subStages.map((s) => s.duration_seconds)) || 1;
  return (
    <ul className={styles.stageList}>
      {subStages.map((s) => {
        const pct = Math.max(1, (s.duration_seconds / maxDur) * 100);
        const fillClass = [styles.stageFill, s.is_slow ? styles.stageFillSlow : '']
          .filter(Boolean).join(' ');
        const p95Hint = s.p95_seconds != null
          ? ` (p95 ${formatStageDuration(s.p95_seconds)})`
          : '';
        return (
          <li key={s.stage_key} className={styles.stageItem} title={s.stage_key + p95Hint}>
            <span className={styles.stageName}>{s.stage_key.replace(/_/g, ' ')}</span>
            <div className={styles.stageTrack}>
              <div className={fillClass} style={{ width: pct.toFixed(1) + '%' }} />
            </div>
            <span className={styles.stageDur}>{formatStageDuration(s.duration_seconds)}</span>
            {s.is_slow && (
              <Badge variant="warning" className={styles.slowBadge}>slow</Badge>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function PhaseRow({ phase }: { phase: PipelinePhase }) {
  return (
    <details className={styles.phaseRow} open={phase.has_slow_stages}>
      <summary className={styles.phaseSummary}>
        <span className={styles.phaseName}>{phase.label}</span>
        <span className={`${styles.phaseStatus} ${STATUS_COLOR_CLASS[phase.status] ?? ''}`}>
          {phase.status}
        </span>
        {phase.has_slow_stages && (
          <Badge variant="warning">slow stages</Badge>
        )}
        <span className={styles.phaseDuration}>
          {formatStageDuration(phase.duration_seconds)}
        </span>
      </summary>
      <div className={styles.phaseBody}>
        <StageBars subStages={phase.sub_stages} />
      </div>
    </details>
  );
}

export function RunDetailPanel({ runId }: RunDetailPanelProps) {
  const { data, isLoading, isError } = usePipelineRunDetail(runId);
  const { data: phasesData, isLoading: phasesLoading } = usePipelineRunPhases(runId);

  if (isLoading) return <LoadingSpinner />;
  if (isError || !data) return <div className={styles.error}>Failed to load details</div>;

  const stats = data.stats || {};
  const batches = stats.batch_results || [];

  const input = stats.input_records ||
    batches.reduce((s, b) => s + (b.input_records || 0), 0);
  const unique = stats.unique_assets != null ? stats.unique_assets : data.total_assets;
  const created = data.assets_created != null ? data.assets_created : (stats.total_assets_created || 0);
  const returned = data.assets_readded != null
    ? data.assets_readded
    : ((stats.chronicle_events && stats.chronicle_events.asset_readded) || 0);
  const updated = data.assets_updated != null ? data.assets_updated : (stats.total_assets_updated || 0);
  const removed = stats.removed_assets || 0;
  const merged = stats.merged_assets || 0;
  const excluded = stats.excluded_records || 0;
  // Grace carry-forward (#795) re-injects absent-but-in-grace assets AFTER the
  // aggregation stage counted `unique`, so `total` — the persisted inventory,
  // the same figure the run-history Total column and Explorer show — equals
  // unique + carried. `total_assets` lives only inside stats on this endpoint.
  const carried = stats.grace_carried_total || 0;
  const total = stats.total_assets;

  // Combined data-flow row: each step is a card and the chain reads
  // left-to-right through the pipeline shape — sources fan in, records
  // get excluded/updated/removed/created, unique assets fall out the
  // other side. Colors apply when the value is non-zero so a clean
  // run reads as quiet neutral cards.
  const metrics: Array<{ label: string; value: string; cls?: string }> = [
    { label: 'Data Sources', value: batches.length ? String(batches.length) : '--' },
    { label: 'Input Records', value: input ? Number(input).toLocaleString() : '--' },
    { label: 'Excluded', value: Number(excluded).toLocaleString(), cls: excluded > 0 ? styles.metricError : undefined },
    { label: 'Updated', value: Number(updated).toLocaleString(), cls: updated > 0 ? styles.metricInfo : undefined },
    { label: 'Removed', value: Number(removed).toLocaleString(), cls: removed > 0 ? styles.metricWarn : undefined },
    // Merges are identity consolidations, not departures — kept neutral (no
    // color), mirroring the neutral "Merged" column in the run-history table.
    { label: 'Merged', value: Number(merged).toLocaleString() },
    { label: 'Created', value: Number(created).toLocaleString(), cls: created > 0 ? styles.metricGood : undefined },
    { label: 'Returned', value: Number(returned).toLocaleString(), cls: returned > 0 ? styles.metricWarn : undefined },
    { label: 'Unique Assets', value: unique != null ? Number(unique).toLocaleString() : '--' },
  ];

  // When grace carried assets forward, the aggregation-time "Unique Assets"
  // terminal understates the persisted inventory. Append the carried count and
  // the reconciled Total so `unique + carried = total` reads left-to-right and
  // matches the run-history Total / Explorer. Omitted on clean runs (carried 0)
  // where Unique Assets already is the total, keeping quiet runs uncluttered.
  if (carried > 0) {
    metrics.push(
      { label: 'Carried Forward', value: Number(carried).toLocaleString(), cls: styles.metricWarn },
      { label: 'Total', value: total != null ? Number(total).toLocaleString() : '--' },
    );
  }

  // Data sources table
  const sortedBatches = [...batches].sort((a, b) => (b.input_records || 0) - (a.input_records || 0));
  const totalInput = sortedBatches.reduce((s, b) => s + (b.input_records || 0), 0);
  const hasAssets = sortedBatches.some((b) => b.unique_assets != null);
  const hasExcluded = sortedBatches.some((b) => (b.excluded ?? 0) > 0);

  return (
    <div className={styles.panel}>
      {/* Pipeline Summary \u2014 single chained row of value cards. The
          Chronicle line that used to sit beneath this duplicated the
          same Updated/Created/Removed counts, so it's gone. */}
      <div className={styles.metrics}>
        {metrics.map((m, i) => (
          <Fragment key={m.label}>
            <div className={[styles.metric, m.cls].filter(Boolean).join(' ')}>
              <span className={styles.metricValue}>{m.value}</span>
              <span className={styles.metricLabel}>{m.label}</span>
            </div>
            {i < metrics.length - 1 && (
              <span className={styles.metricArrow}>{'\u2192'}</span>
            )}
          </Fragment>
        ))}
      </div>

      {/* Error */}
      {data.error_message && (
        <div>
          <div className={styles.sectionHeading}>Error</div>
          <div className={styles.errorCallout}>{data.error_message}</div>
        </div>
      )}

      {/* Per-source breakdown. The "Data Sources" heading that used to
          sit above this table was redundant with the Data Sources card
          in the metric row, so the table now stands on its own. */}
      <div>
        {sortedBatches.length === 0 ? (
          <div className={styles.emptyDetail}>No data source information available</div>
        ) : (
          <table className={styles.batchTable}>
            <thead>
              <tr>
                <th>Integration</th>
                <th className={styles.textRight}>Records</th>
                <th className={styles.textRight}>Share</th>
                {hasAssets && <th className={styles.textRight}>Assets</th>}
                {hasExcluded && <th className={styles.textRight}>Excluded</th>}
                <th className={styles.textCenter}>Status</th>
              </tr>
            </thead>
            <tbody>
              {sortedBatches.map((b, idx) => {
                const name = b.integration_name || (b.batch_id ? String(b.batch_id).substring(0, 8) : '--');
                const records = b.input_records != null ? b.input_records.toLocaleString() : '--';
                const pct = totalInput > 0 && b.input_records
                  ? Math.round((b.input_records / totalInput) * 100)
                  : 0;
                const isSuccess = b.success !== false;

                return (
                  <tr key={idx}>
                    <td>
                      <span className={styles.sourceName}>{name}</span>
                      {b.integration_type && (
                        <span className={styles.sourceType}>{b.integration_type}</span>
                      )}
                      {b.error && <div className={styles.sourceError}>{b.error}</div>}
                    </td>
                    <td className={styles.textRight}>{records}</td>
                    <td className={styles.textRight}>{pct > 0 ? pct + '%' : '--'}</td>
                    {hasAssets && (
                      <td className={styles.textRight}>
                        {b.unique_assets != null ? b.unique_assets.toLocaleString() : '--'}
                      </td>
                    )}
                    {hasExcluded && (
                      <td className={[styles.textRight, (b.excluded ?? 0) > 0 ? styles.textWarn : ''].filter(Boolean).join(' ')}>
                        {(b.excluded || 0).toLocaleString()}
                      </td>
                    )}
                    <td className={styles.textCenter} style={{ color: isSuccess ? 'var(--success)' : 'var(--error)' }}>
                      {isSuccess ? '\u2713' : '\u2717'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div>
        <div className={styles.sectionHeading}>Stage timings</div>
        {phasesLoading ? (
          <div className={styles.emptyDetail}>Loading…</div>
        ) : !phasesData || phasesData.phases.length === 0 ? (
          <div className={styles.emptyDetail}>Not available</div>
        ) : (
          <div className={styles.phases}>
            {phasesData.phases.map((p) => (
              <PhaseRow key={p.key} phase={p} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
