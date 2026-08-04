// SPDX-License-Identifier: AGPL-3.0-or-later
import { Link } from 'react-router-dom';
import { Badge } from '@/components/ui/Badge';
import { DataSourceBadge } from '@/components/ui/DataSourceBadge';
import { DeleteIconButton, IconButton } from '@/components/ui/IconButton';
import { InlineStatus, useInlineStatus } from '@/components/ui/InlineStatus';
import { ToggleSwitch } from '@/components/ui/ToggleSwitch';
import { resolveLogoUrl } from '@/lib/logoUrl';
import { useRunIntegration } from '@/api/integrations';
import type { Integration } from '@/api/integrations';
import { formatRelativeTime, getConnectorDisplayName, getScheduleDescription, getSourceMethodLabel } from './integration-utils';
import styles from './IntegrationCard.module.css';

interface IntegrationCardProps {
  integration: Integration;
  onRunStarted?: () => void;
  onToggle: (id: string, enable: boolean) => void;
  onEdit: (id: string) => void;
  onClone: (id: string) => void;
  onDelete: (id: string) => void;
}

export function IntegrationCard({ integration, onRunStarted, onToggle, onEdit, onClone, onDelete }: IntegrationCardProps) {
  const isEnabled = integration.status === 'enabled';
  const sourceMethodLabel = getSourceMethodLabel(integration.source_method);
  const [runStatus, setRunStatus] = useInlineStatus();

  const runMutation = useRunIntegration({
    onSuccess: () => {
      onRunStarted?.();
      setRunStatus({ kind: 'success', message: 'Integration pipeline started' });
    },
    onError: (err) => {
      setRunStatus({ kind: 'error', message: err.message || 'Failed to run integration' });
    },
  });

  function handleRun() {
    if (integration.is_running) return;
    runMutation.mutate(integration.id);
  }

  return (
    <div className={`${styles.card} ${!isEnabled ? styles.disabledCard : ''}`}>
      <div className={styles.cardHeader}>
        <div className={styles.headerLeft}>
          {integration.logo && (
            <img src={resolveLogoUrl(integration.logo) ?? ''} alt="" className={styles.logo} />
          )}
          <div>
            <Link to={`/integration-details?id=${integration.id}`} className={styles.name}>
              {integration.name}
            </Link>
            <div className={styles.type}>{getConnectorDisplayName(integration.integration_type)}</div>
          </div>
        </div>
        <div className={styles.headerRight}>
          <ToggleSwitch
            checked={isEnabled}
            onChange={(checked) => onToggle(integration.id, checked)}
          />
          {sourceMethodLabel && (
            <DataSourceBadge name={sourceMethodLabel} tinted />
          )}
        </div>
      </div>

      <div className={styles.meta}>
        {integration.is_running ? (
          <Badge variant="info">Running</Badge>
        ) : integration.status === 'error' ? (
          <Badge variant="danger">Error</Badge>
        ) : (
          <Badge variant={isEnabled ? 'success' : 'default'}>
            {isEnabled ? 'Enabled' : 'Disabled'}
          </Badge>
        )}
        {integration.latest_batch_assets != null && integration.latest_batch_assets > 0 && (
          <span className={styles.assetCount}>{integration.latest_batch_assets} assets</span>
        )}
      </div>

      <div className={styles.details}>
        <div className={styles.detailRow}>
          <span className={styles.detailLabel}>Last sync</span>
          <span className={styles.detailValue}>{formatRelativeTime(integration.last_sync_at)}</span>
        </div>
        <div className={styles.detailRow}>
          <span className={styles.detailLabel}>Schedule</span>
          <span className={styles.detailValue}>{getScheduleDescription(integration.schedule_cron)}</span>
        </div>
        {integration.latest_batch_records != null && (
          <div className={styles.detailRow}>
            <span className={styles.detailLabel}>Records</span>
            <span className={styles.detailValue}>
              {integration.is_running && integration.latest_batch_records === 0
                ? '—'
                : integration.latest_batch_records.toLocaleString()}
            </span>
          </div>
        )}
      </div>

      <div className={styles.actions}>
        <div className={styles.runStatus}>
          <InlineStatus status={runStatus} align="inline" />
        </div>
        <IconButton onClick={handleRun} title="Run Now" disabled={integration.is_running}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        </IconButton>
        <IconButton onClick={() => onEdit(integration.id)} title="Edit">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </IconButton>
        <IconButton onClick={() => onClone(integration.id)} title="Clone">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        </IconButton>
        <DeleteIconButton onClick={() => onDelete(integration.id)} title="Delete" />
      </div>
    </div>
  );
}
