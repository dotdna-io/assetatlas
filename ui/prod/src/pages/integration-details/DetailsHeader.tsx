// SPDX-License-Identifier: AGPL-3.0-or-later
import { Link } from 'react-router-dom';
import { Badge } from '@/components/ui/Badge';
import { DataSourceBadge } from '@/components/ui/DataSourceBadge';
import { DeleteIconButton, IconButton } from '@/components/ui/IconButton';
import type { Integration } from '@/api/integrations';
import { resolveLogoUrl } from '@/lib/logoUrl';
import { formatRelativeTime, getSourceMethodLabel } from '@/pages/integrations/integration-utils';
import styles from './DetailsHeader.module.css';

interface DetailsHeaderProps {
  integration: Integration;
  onRunNow: () => void;
  onEdit: () => void;
  onDelete: () => void;
  isRunning: boolean;
}

export function DetailsHeader({
  integration,
  onRunNow,
  onEdit,
  onDelete,
  isRunning,
}: DetailsHeaderProps) {
  return (
    <div className={styles.header}>
      <Link to="/integrations" className={styles.backLink}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg> Back to Integrations
      </Link>

      <div className={styles.headerMain}>
        {integration.logo ? (
          <img
            src={resolveLogoUrl(integration.logo) ?? ''}
            alt={`${integration.name} logo`}
            className={styles.logo}
          />
        ) : (
          <div className={styles.logoPlaceholder}>
            {integration.name.charAt(0).toUpperCase()}
          </div>
        )}

        <div className={styles.info}>
          <div className={styles.nameRow}>
            <div className={styles.name}>{integration.name}</div>
            <Badge variant={integration.status === 'enabled' ? 'success' : 'default'}>
              {integration.status}
            </Badge>
          </div>
          {getSourceMethodLabel(integration.source_method) && (
            <div className={styles.badges}>
              <DataSourceBadge name={getSourceMethodLabel(integration.source_method)!} tinted />
            </div>
          )}
          <div className={styles.created}>
            Created {formatRelativeTime(integration.created_at)}
          </div>
        </div>

        <div className={styles.actions}>
          <IconButton onClick={onRunNow} disabled={isRunning} title="Run Now">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          </IconButton>
          <IconButton onClick={onEdit} title="Edit">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </IconButton>
          <DeleteIconButton onClick={onDelete} title="Delete" size={18} />
        </div>
      </div>
    </div>
  );
}
